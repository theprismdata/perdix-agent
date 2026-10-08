# 하네스 루프 설계

> 아키텍처 전체는 [architecture.md](architecture.md) 참조. 리뷰 반영 이력은 문서 끝 참조.

## 1. 용어

| 용어 | 정의 |
|---|---|
| Session | 하나의 대화 + 작업 디렉터리 + 모드 + 설정. ACP `session/new`로 생성 |
| Turn | 사용자 프롬프트 1회에 대한 처리 단위. ACP `session/prompt` 1회 = Turn 1회 |
| Step | LLM 호출 1회와 그 결과로 나온 도구 호출 실행까지 |
| Verification gate | `build` 모드에서 Turn 종료 전에 실행하는 검증 단계 |
| Budget | Turn 단위 토큰/시간/Step 수/검증 반복 수 상한 |
| Event log | 세션의 불변 append-only 기록(메시지, 도구 시작/결과, 압축 레코드) |
| ModelView | Event log에서 순수 함수로 만들어 **어댑터에 넘기는 내부 표현**. 와이어 형식은 어댑터가 정한다 |

## 2. 상태 기계

```
                 session/prompt
                       │
                       ▼
              ┌─────────────────┐
              │  BUILD_VIEW     │  ModelView = project(event_log) + 지침·메모리·스킬 목록
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
     ┌───────►│   LLM_STEP      │  스트리밍. text → agent_message_chunk 즉시 전달
     │        └────────┬────────┘  스트림 정상 종료 시 assistant 레코드 커밋
     │                 │ stop_reason
     │     ┌───────────┼───────────────┐
     │  tool_use    end_turn      max_tokens / refusal / error
     │     │           │                │
     │     ▼           │                ▼
     │ ┌──────────┐    │        ┌──────────────┐
     │ │ TOOLS    │    │        │ RECOVER      │  §3.5 참조
     │ │ §3.3     │    │        └──────┬───────┘
     │ └────┬─────┘    │               │
     │      ▼          ▼               │
     │ ┌──────────────────────┐        │
     └─┤ BUDGET / COMPACT     │◄───────┘
       │ §5                   │
       └──────────┬───────────┘
                  │ end_turn 이고 mode == build
                  ▼
       ┌──────────────────────┐   fail (iter < max)
       │ VERIFY  §4           │──────────────► 실패 로그를 synthetic user 메시지로 주입 → LLM_STEP
       └──────────┬───────────┘
                  │ pass / iter == max / 예산 초과
                  ▼
              ┌────────┐
              │  DONE  │  stopReason 매핑(§6) 후 응답
              └────────┘
```

## 3. Step 세부

### 3.1 Turn 진입
`prompt()` 핸들러는 **첫 `await` 전에 동기적으로** 세션의 Turn 슬롯과 취소 이벤트를 등록한다(SDK는 요청·알림마다 별도 task를 띄우므로, 늦게 등록하면 직후 도착한 `session/cancel`이 유실된다). 세션마다 `asyncio.Lock`을 두고 이미 Turn이 실행 중이면 JSON-RPC 오류로 거절한다.

### 3.2 요청 조립과 스트리밍
- 어댑터에 넘기는 순서는 `tools` → `system` → `messages` 고정(프롬프트 캐시 프리픽스 안정화). 가변 정보(시간, 턴 번호)는 마지막 user 메시지 쪽에 둔다.
- 텍스트 델타는 즉시 `agent_message_chunk`. tool_use 블록은 등장 순서대로 `tool_call(status=pending)`을 먼저 보내고 입력이 완성되면 `tool_call_update`로 갱신.
- 스트림이 **정상 종료**했을 때만 assistant 레코드를 커밋한다. 레코드는 `items: [{kind, provider, model, opaque_raw}]`의 **순서 리스트**다. thinking/reasoning/signature 같은 불투명 블록은 원본 바이트 그대로 `opaque_raw`에 보관한다.

### 3.3 도구 실행 규칙
1. **입력 스키마 검증**을 실행 전에 한다. 실패는 예외가 아니라 `is_error` 결과로 모델에 돌려준다.
2. **실행 순서**: tool_use 등장 순서로 연속 구간을 나눈다.
   - 연속된 읽기 전용 구간은 `asyncio.gather(return_exceptions=True)`로 동시 실행(예외는 `is_error`로 변환).
   - 쓰기·실행 도구는 장벽(barrier)으로 단독 실행. `[read A, edit A, read A]`는 read → edit → read 순서가 보장된다.
   - 읽기 전용 판정은 정적 플래그가 아니라 `tool.is_read_only(input)`이다. `task`는 preset에 따라, MCP 도구는 **기본 쓰기**로 본다(`readOnlyHint`는 신뢰하지 않는다).
3. **결과 레코드**는 `tool_use_id`와 `index`(등장 순서)를 갖는다. 완료 순서대로 커밋되지만 투영 시 `index`로 재정렬한다. 묶음 user 메시지 레코드는 **없다**. 투영이 개별 결과를 모아 어댑터에 넘기고, 와이어 형식(한 user 메시지 / `function_call_output` / `role: tool`)은 어댑터가 정한다.
4. **권한**: `ask`는 세션당 하나의 큐로 직렬화한다. 응답마다 남은 요청을 다시 평가하므로 `allow_always` 선택이 뒤의 요청에도 반영된다. 거절은 `is_error("user rejected")`로 돌려주고 루프를 계속한다.
5. **훅**: `pre_tool(name, input) -> allow | deny | mutate(input)`, `post_tool(name, input, result) -> result'`.
6. **체크포인트**: Step당 한 번, 첫 쓰기 도구 직전에만 섀도 git 커밋.
7. **커밋 시점**: 도구 시작 시 `tool_started` 레코드, 완료 즉시 `tool_result` 레코드. 크래시 시 "시작했는데 결과 없음"과 "시작도 안 함"을 구분할 수 있다.
8. **하위 프로세스**: `start_new_session=True`로 띄우고 취소 시 `os.killpg`로 프로세스 그룹 전체를 죽인다. 공급자 API 키 등 비밀 env는 제거한 환경으로 띄운다(architecture.md §6.6).

### 3.4 복구 (`session/load` 시)
event log를 읽어:
- `tool_started`는 있고 결과가 없는 tool_use → `is_error("interrupted: process exited")`
- `tool_started`조차 없는 tool_use → `is_error("not executed")`
를 **새 레코드로 append**한다. 과거 레코드는 절대 수정하지 않는다.

### 3.5 RECOVER 분기
| 상황 | 처리 |
|---|---|
| `max_tokens`, 텍스트만 잘림 | assistant 레코드 커밋 후 synthetic user "계속" 1회 주입 → LLM_STEP |
| `max_tokens`, tool_use JSON이 잘림 | 그 assistant 메시지는 **커밋하지 않는다**(깨진 input은 히스토리에 둘 수 없음). 보낸 `tool_call`은 `failed`로 닫고 `max_tokens`를 올려 1회 재요청. 재실패 시 `stopReason: max_tokens` |
| `refusal` | 사유를 사용자에게 전달하고 `stopReason: refusal` |
| 429 / 529 / 5xx / 연결 끊김, **첫 청크 전** | 지수 백오프 자동 재시도(기본 3회) |
| 같은 오류, **첫 청크 이후** | 보낸 `tool_call`을 전부 `failed`로 닫고, `agent_message_chunk`로 "[연결 끊김, 재시도]" 구분 텍스트를 보낸 뒤 재요청. 버린 부분 출력은 커밋하지 않으므로 리플레이에는 남지 않는다 |
| 400 등 비재시도 오류 | JSON-RPC error로 Turn 종료, 사유 기록 |

## 4. 검증 게이트 (`harness/verifier.py`)

```json
{ "verify": {
    "commands": [
      { "name": "test", "run": "pnpm test", "timeout": 600 },
      { "name": "typecheck", "run": "pnpm tsc --noEmit" }
    ],
    "reviewer": { "enabled": true, "model": "cheap", "rubric": "default" },
    "max_iterations": 3 } }
```

- 각 명령은 **합성 `tool_call{kind: execute, title: "verify: <name>"}`**으로 클라이언트에 표시하고, 그 `toolCallId`로 `session/request_permission`을 건다(ACP는 toolCall 없는 권한 요청을 허용하지 않는다). 한 번 `allow_always`면 세션 동안 자동.
- 실행 조건: 프로젝트가 신뢰 상태(architecture.md §6.9)이거나 서버가 `--trust-project`로 기동된 경우. 아니면 검증을 건너뛰고 `_meta.perdix.verification = "skipped"`.
- 하나라도 실패하면 stdout/stderr 꼬리(설정 가능)를 `VerificationFailed` synthetic user 메시지로 주입.
- 리뷰어 서브에이전트는 읽기 전용으로 diff를 보고 "완료 조건 충족 여부 + 미완 항목"을 구조화 판정으로 반환한다.
- 완료 조건 출처: (1) `/done-when` 명시 (2) Turn 시작 시 `todo` 체크리스트 (3) 둘 다 없으면 검증 명령만.
- 반복 소진 시 `end_turn` + 미완 항목 메시지 + `_meta.perdix.verification = "failed"`.

**synthetic user 메시지**("계속", VerificationFailed)는 `origin: synthetic`을 갖고, 리플레이 때 `user_message_chunk`로 내보내지 않는다.

## 5. 예산과 압축

| 항목 | 기본값 | 초과 시 |
|---|---|---|
| Step 수 / Turn | 200 | `stopReason: max_turn_requests`. 사용자가 다음 프롬프트로 이어간다 |
| 검증 반복 | 3 | 미완 항목 보고 후 `end_turn` |
| 컨텍스트 사용률 | 모델 창의 75% 도달 시 압축 | 압축 실패 시 오래된 도구 결과부터 치환 |
| 시간 | 무제한(설정 가능) | `stopReason: max_turn_requests` + 사유 (`cancelled`는 사용자 취소에만) |
| 서브에이전트 | 부모 Turn 예산에서 차감 | 부모 규칙과 동일 |

### 5.1 투영 규칙 (ModelView)
- 압축은 투영만 바꾼다. 결과는 `events(kind=compaction){range, summary | provider_opaque_block, provider, model}`로 append.
- **불투명 블록 수명**: assistant 아이템의 `(provider, model)`이 현재 요청의 것과 다르면 thinking/reasoning/signature 아이템을 투영에서 **버린다**. 같으면 원본 그대로, 원래 순서로 넘긴다(Anthropic은 thinking이 tool_use 앞에, Gemini는 `thoughtSignature`가 첫 functionCall part에, OpenAI는 reasoning item 바로 뒤에 후속 item이 있어야 한다). 전환 직후 추론 연속성과 캐시를 잃는 것은 감수한다.
- 자르는 경계는 완결된 user 턴 또는 완결된 tool_use/tool_result 쌍 단위로만. 현재 턴의 마지막 assistant 아이템은 압축 대상에서 제외.
- 공급자 서버측 컴팩션 블록은 `provider`를 기록하고, 공급자가 바뀌면 무효 처리 후 텍스트 요약으로 대체.
- 압축은 캐시 프리픽스를 깨뜨린다. 임계치를 낮게 잡을수록 비용이 늘어난다.

## 6. 종료 사유 매핑 (ACP `stopReason`)

| 상황 | `stopReason` | 부가 |
|---|---|---|
| 정상 완료 / 검증 통과 | `end_turn` | |
| 검증 반복 소진 | `end_turn` | `_meta.perdix.verification="failed"` |
| 검증 건너뜀(미신뢰/헤드리스) | `end_turn` | `_meta.perdix.verification="skipped"` |
| Step·토큰·시간 예산 초과 | `max_turn_requests` | 사유 메시지 |
| max_tokens 복구 실패 | `max_tokens` | |
| 모델 거부 | `refusal` | |
| 사용자 취소 | `cancelled` | |
| 복구 불가 공급자 오류 | JSON-RPC error 응답 | |

## 7. 취소/재개

- `session/cancel`은 **알림**이다. 서버는 LLM 스트림을 중단하고, 실행 중 도구 프로세스 그룹에 SIGTERM → 2초 후 SIGKILL.
- 커밋 규칙: **완결된 text 아이템만** assistant 레코드로 커밋하고, 미완성 tool_use와 thinking은 버린다. 이미 `tool_started`된 tool_use에는 `is_error("cancelled")`를 append. 이미 보낸 `tool_call`은 모두 `tool_call_update{status: failed}` + 사유로 닫는다(ACP에 cancelled 상태가 없다).
- 대기 중이던 `session/request_permission`이 `outcome: cancelled`로 돌아오는 것을 처리한 뒤, 원래의 `session/prompt`에 **정상 응답** `stopReason: cancelled`를 돌려준다(오류 응답 금지).
- `session/load`: §3.4 복구 후 event log를 `session/update`로 리플레이한다(`origin: synthetic`인 user 메시지는 제외). `allow_always` 규칙은 세션 레코드에 저장되어 재개 후에도 유지된다. `session/resume`은 Python SDK에서 unstable이므로 1차는 load + 리플레이만 쓴다.

## 8. 서브에이전트

- `task` 도구로 스폰. 입력: `preset(explore|executor|reviewer)`, `prompt`, `model?`.
- 하위 세션은 부모와 **히스토리를 공유하지 않는다**. 결과는 최종 텍스트 1개로 부모에 반환.
- **병렬 스폰은 `explore`/`reviewer`만** 허용. `executor`는 직렬 실행이 기본(병렬은 git worktree 격리로 2차).
- 자식의 도구 호출은 **항상** 최상위 `tool_call`로 클라이언트에 보낸다(권한 요청이 참조하는 `toolCallId`를 클라이언트가 알아야 하므로). `_meta.perdix.parentToolCallId`를 붙이고, `nestedToolCalls` capability를 광고한 클라이언트만 중첩 렌더링한다. 표준 클라이언트에서는 평평하게 보인다.
- 진행 상황은 부모 `tool_call_update.content`에 **최근 한 줄만** 넣는다(`content`는 매번 전체 교체라 누적하면 전송량이 제곱으로 는다). 전체 로그는 자식 세션 event log에 있다.
- 예산은 부모 Turn 예산에서 차감. 부모 권한 규칙을 상속하고 `explore`/`reviewer`는 쓰기 도구를 제거한다.

## 9. MCP 서버 장애
- 서버 프로세스 크래시: 해당 서버의 도구를 레지스트리에서 제거하고 모델에 `is_error("mcp server <name> unavailable")`로 알린다. 재시작은 다음 Turn 시작 시 1회 시도.
- 도구 호출 타임아웃(기본 60초): `is_error("timeout")`. 서버는 유지.
- 도구 스키마가 현재 공급자에서 변환 불가(`sanitize_tool_schema` 실패)하면 그 공급자에서만 제외하고 경고한다.

---
리뷰 반영 이력
- 2026-10-07 1차: 병렬 실행 규칙, 3단계 커밋과 복구, RECOVER 세분화, ModelView 투영, stopReason 매핑, cancel 알림 계약, 서브에이전트 병렬 제한, MCP 장애.
- 2026-10-07 2차: Turn 진입 동기 등록과 세션 락(§3.1), 아이템 순서 리스트와 불투명 블록 수명(§3.2, §5.1), 연속 구간 실행과 index 재정렬·묶음 레코드 제거(§3.3), tool_started 기반 복구(§3.4), 첫 청크 기준 재시도(§3.5), 합성 tool_call로 verify 권한(§4), synthetic origin(§4), 취소 시 커밋 범위(§7), 자식 tool_call 평면 전송과 진행 한 줄(§8).
