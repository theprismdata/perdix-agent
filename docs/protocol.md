# 프로토콜: ACP 사용 범위와 확장

## 게이트웨이 채널 API

`perdix-gateway`는 기존 ACP 표준을 사용하는 클라이언트다. 아래 HTTP API는 웹·외부
채널 접수를 위한 제어면이며 ACP 메서드나 `_perdix` 확장을 추가하지 않는다.
설정은 snake_case, HTTP JSON은 camelCase다. 상세 설정은 [gateway.md](gateway.md).

| HTTP | 경로 | 용도 |
|---|---|---|
| GET | `/api/status` | 등록 프로젝트와 채널 상태 |
| GET/POST | `/api/jobs` | 최근 작업 목록 / `{project, prompt}` 제출 |
| GET | `/api/jobs/{id}/events?after={seq}` | 커서 이후 이벤트(최대 1000개) |
| POST | `/api/jobs/{id}/cancel` | 표준 ACP cancel 및 서버 정리 |
| GET | `/api/jobs/{id}/permissions` | 현재 승인 요청 |
| POST | `/api/jobs/{id}/permissions/{permissionId}` | `{optionId}` 승인 선택(null은 거절) |
| POST | `/api/github/import` | `{project, issueNumber, instruction?}` 이슈 가져오기 |
| POST | `/webhooks/github` | HMAC 서명된 issue_comment 접수 |

`/api/*`는 Bearer 토큰을 요구한다. Webhook은 별도 GitHub 서명을 요구한다.
작업 단위마다 독립 ACP 세션을 만들며 권한·도구·LLM 판단은 기존 Python 서버가 한다.

## 1. ACP (Agent Client Protocol)

- 전송: JSON-RPC 2.0 over stdio (1차). WebSocket은 원격 서버 시나리오용으로 예약.
- Python: `agent-client-protocol` **0.12.x에 고정**(schema v1.19). 진입점은 `acp.run_agent(agent, use_unstable_protocol=True)`. `AgentSideConnection` 직접 생성은 deprecated. `elicitation`과 `PromptResponse.usage`가 unstable이라 플래그가 필요하고, 그래서 버전을 고정한다.
- TypeScript: `@agentclientprotocol/sdk` **1.7.x에 고정**. 진입점은 `client({ name }).connectWith(stream, …)`. `new ClientSideConnection(...)`은 deprecated. `@perdix/protocol`에서 재수출.
- 스펙 확인일: 2026-10-07 (agentclientprotocol.com/protocol/schema + 두 SDK 소스 대조). SDK를 올릴 때 이 문서의 표를 다시 검증한다.

### 1.1 사용하는 표준 메서드

| 방향 | 메서드 | Perdix 매핑 |
|---|---|---|
| C→A | `initialize` | capability 협상. 클라이언트는 `fs`, `terminal`, `elicitation.form` 지원을 광고. 서버는 `loadSession: true`, `sessionCapabilities.list`를 광고 |
| C→A | `authenticate` | `methodId: openai-chatgpt`. API 키가 있으면 로그인 생략, 없으면 서버가 ChatGPT OAuth를 수행. `initialize.authMethods`로 광고 |
| C→A | `session/new` | cwd, mcpServers 수신 → Session 생성. 응답에 `modes`(chat/plan/build)와 `configOptions`(model 등). 세션별 덮어쓰기는 `_meta.perdix` |
| C→A | `session/load` | event log 복구 + 히스토리 리플레이 |
| C→A | `session/list` | 세션 목록(재개 UI) |
| C→A | `session/prompt` | Turn 실행 또는 `/loop` 제어. 응답에 `stopReason`(harness-loop.md §6) |
| C→A | `session/cancel` | 알림. Turn 취소 후 prompt에 `cancelled`로 정상 응답 |
| C→A | `session/set_mode` | chat / plan / build. 메인 모델의 `switch_mode` 자동 전환도 동일한 기록·`current_mode_update`를 사용. 역할별 모델 할당이 있으면 `config_option_update`도 통지 |
| C→A | `session/set_config_option` | 모델 전환(`category: model`), `memory_use` / `memory_generate` (`on` / `off`), `openrouter_catalog=refresh` 목록 조회, `agent_model_<role>=<model ID>` 역할 할당 저장. 메모리 설정은 `config_change`에 보존한다. 결과는 `config_option_update`로 통지 |
| A→C | `session/update` | `user_message_chunk`(리플레이), `agent_message_chunk`, `agent_thought_chunk`, `tool_call`, `tool_call_update`, `plan`, `available_commands_update`, `current_mode_update`, `config_option_update`, `usage_update`, `session_info_update` |
| A→C | `session/request_permission` | 권한 `ask`. **`toolCall` 필수**이므로 도구 호출 맥락에서만. verify 명령은 합성 `tool_call`을 먼저 보낸다. 옵션 kind: `allow_once / allow_always / reject_once / reject_always`. `outcome: cancelled` 처리 필수 |
| A→C | `elicitation/create` | 사용자 질문(`ask_user` 도구)과 프로젝트 신뢰 승인. `sessionId` + 가능하면 `toolCallId`를 넣는다. 클라이언트가 `elicitation.form`을 광고하지 않으면 `ask_user` 도구를 제거하고 신뢰 게이트는 "미신뢰 유지 + `session_info_update`로 안내" |
| A→C | `fs/read_text_file`, `fs/write_text_file` | 클라이언트가 capability 광고 시 파일 I/O 위임 |
| A→C | `terminal/*` | 옵션. 로컬 sandbox 결과에는 `terminal` 콘텐츠를 쓰지 않는다(클라이언트가 만든 terminalId가 필요) |

`usage_update`의 필드는 `used`/`size`(현재 컨텍스트 점유 토큰/창 크기)와 선택적 `cost`다. 턴별 입력/출력 토큰이 아니다. 턴별 토큰은 내부 `usage` 테이블에만 기록한다.

### 1.2 확장 (`_meta.perdix` 및 `_perdix/` 접두)
표준에 있는 기능(세션 목록, 설정 옵션, 사용량, 질문)은 모두 표준 메서드를 쓴다. 확장은 아래로 한정한다.

| 위치 | 이름 | 용도 |
|---|---|---|
| `initialize` 요청 `clientCapabilities._meta.perdix` | `{ checkpoints: bool, nestedToolCalls: bool }` | 확장 지원 협상 |
| `session/new` 요청 `_meta.perdix` | `{ model?, mode?, permissionMode? }` | 세션별 덮어쓰기. `permissionMode`는 서버 argv보다 **완화할 수 없다** |
| `tool_call` `_meta.perdix` | `{ parentToolCallId? }` | 서브에이전트 중첩 렌더링 힌트 |
| `session/prompt` 응답 `_meta.perdix` | `{ verification?: "passed" \| "failed" \| "skipped" }` | 검증 게이트 결과 |
| `session/update`의 `update._meta.perdix.workflow` | `{ schemaVersion: 1, kind: "snapshot" \| "event", runId, seq, payload }` | 선택적 workflow 투영/이벤트. 표준 텍스트를 함께 제공 |
| C→A `_perdix/checkpoint/list`, `_perdix/checkpoint/restore` | 체크포인트 | `checkpoints` capability 광고 시에만 |
| C→A `_perdix/diagnostics` (알림) | IDE 진단을 서버에 전달 | 서버는 "클라이언트가 준 진단"으로만 취급 |

SDK 구현 메모:
- **와이어 키는 camelCase**(ACP 관례). Python 내부에서는 snake_case로 변환한다. AGENTS.md의 snake_case 규약은 설정 파일과 내부 코드에만 적용된다.
- Python SDK는 `_meta`의 키를 핸들러 kwargs로 펼친다. `_meta.perdix`는 `perdix=` kwarg로 들어오므로 핸들러 시그니처는 `**kwargs`를 받는다.
- 확장 메서드는 핸들러에 앞의 `_`가 제거된 이름(`perdix/checkpoint/list`)으로 전달된다.

표준 ACP 클라이언트(Zed 등)는 확장을 전혀 몰라도 채팅, 도구 실행, 권한 요청, 모드/모델 전환이 동작해야 한다.

Workflow 명령은 `/workflow plan <요청>`, `start <runId>`, `status <runId>`,
`resume <runId>`, `retry <runId> <taskId>`, `accept <runId>`, `reject <runId>`이며
표준 `session/prompt` text로 전달한다. 취소는 `session/cancel`이다.
metadata는 Python SDK update 객체의 `field_meta={"perdix": {"workflow": envelope}}`로
설정한다. notification 외곽 kwargs에만 넣으면 update를 저장하는 client에서 유실된다.
`payload`는 kind에 따른 snapshot/event이며, 알 수 없는 schemaVersion은 무시하고 표준
agent_message_chunk/plan/tool_call 콘텐츠로 표시한다. 수락 명령은 사용자 origin만 허용한다.
`resume`은 완료 ledger를 재사용하고 중단 흔적을 대조한다. unknown 부작용을 자동 재실행하지
않으며 schema/graph 버전 불일치에서는 열람만 허용한다. `accept`는 passed receipt의
candidateCommit/candidateTree와 현재 후보가 일치하고 대상 HEAD가 baseCommit이며 작업 트리가
깨끗할 때만 fast-forward한다. 거절·충돌 시 사용자 파일을 덮어쓰거나 강제 reset하지 않는다.
상세 상태·영속 계약은 [ADR 0026](adr/0026-verified-team-workflows.md)을 따른다.

### 1.3 권한 모드 (`--permission-mode`)
| 모드 | 의미 | 용도 |
|---|---|---|
| `interactive` (기본) | `ask` → `session/request_permission` | TUI |
| `deny` | 내장 읽기 전용 기본값만 allow, 나머지 전부 deny | CI 분석 |
| `accept-edits` | `edit`/`write` allow, `bash` 등은 규칙대로(ask는 deny 처리) | 헤드리스 구현 |
| `allowlist` | 설정의 `allow` 규칙 적용, 나머지 deny. **신뢰된 프로젝트에서만** | CI build 모드 |

헤드리스(`perdix -p`)의 기본은 `deny`. 거부된 호출은 `is_error("non-interactive: permission required")`.
새로 클론한 저장소를 CI에서 돌리려면 `--trust-project`를 함께 준다.

### 1.4 목표 루프 명령

목표 루프의 `/loop <목표>`, `/loop status`, `/loop cancel`, `/loop resume`은 표준
`session/prompt`의 text 블록 하나로 전달한다. 서버는 `available_commands_update`로
`loop`와 인자 힌트를 광고한다. 새 `_perdix/` 메서드는 추가하지 않는다.
진행·종료 사유는 `agent_message_chunk`, 검증 실행은 합성 `tool_call`로 표시한다.
status/cancel/도움말은 실행 중에도 처리되는 제어 요청이므로 클라이언트의 작업 중 상태를
해제하지 않는다. 실제 목표 실행 응답의 `_meta.perdix.verification`만 성공 여부를 표시한다.
상세 계약은 [goal-loop.md](goal-loop.md)에 있다.

## 2. IDE Bridge (VS Code 확장 ↔ CLI)

서버는 관여하지 않는다. CLI(ACP 클라이언트)가 브리지의 유무에 따라 ACP `fs/*` 처리와 diff 표시를 분기한다.

- 발견: 확장이 `createTerminal({ env: { PERDIX_IDE_PORT } })`로 터미널을 열고 `~/.perdix/ide/<port>.lock`(권한 0600)에 `{ pid, workspaceFolders, token }`을 기록. CLI는 토큰을 헤더로 보내 WS 연결. env 포트로 연결이 안 되면(창 재로드 후 영구 터미널 복원 등) 락파일을 스캔해 `pid` 생존과 `workspaceFolders`에 cwd 포함 여부를 확인하고 대체 연결한다.
- 전송: WebSocket, localhost 전용, JSON-RPC 2.0. 서버는 `Origin` 헤더를 검사한다.
- Remote(SSH/WSL/컨테이너)에서 터미널과 같은 호스트에 뜨도록 확장은 `extensionKind: ["workspace"]`를 선언한다.
- **원칙: 디스크가 진실.** read/grep/bash/검증/체크포인트 모두 디스크를 본다. 에디터 버퍼는 힌트일 뿐이다.

| 메서드 | 방향 | 설명 |
|---|---|---|
| `ide/openDiff` | CLI→IDE | `{path, oldText, newText, title}` 로 diff 에디터 열기 |
| `ide/openFile` | CLI→IDE | 파일/라인 포커스 |
| `ide/getDirtyFiles` | CLI→IDE | 저장 안 된 변경이 있는 파일 목록 |
| `ide/revertFile` | CLI→IDE | 디스크 내용으로 버퍼 되돌리기(쓰기 충돌 해소 후) |
| `ide/getSelection` | CLI→IDE | 활성 편집기 선택 영역(프롬프트에 자동 첨부) |
| `ide/getDiagnostics` | CLI→IDE | 언어 서버 진단. CLI가 `_perdix/diagnostics` 알림으로 서버에 전달 |
| `ide/getOpenEditors` | CLI→IDE | 열린 파일 목록(컨텍스트 힌트) |
| `ide/selectionChanged` | IDE→CLI | 알림. TUI 상태표시줄에 반영 |

파일 I/O 처리:
- `fs/read_text_file`: **항상 디스크**에서 읽는다. 해당 파일이 dirty면 결과 끝에 "[편집기에 저장 안 된 변경 있음]" 경고 한 줄을 덧붙인다.
- `fs/write_text_file`: dirty가 아니면 CLI가 디스크에 직접 쓴다(VS Code는 dirty가 아닌 버퍼를 자동으로 다시 읽는다). 에디터 저장 경로는 쓰지 않는다. `formatOnSave`가 끼어들어 내용이 바뀌기 때문이다. dirty면 **충돌**로 보고 TUI에서 "저장 안 된 편집을 버리고 덮어쓸까?"를 묻는다. 승인 시 디스크에 쓰고 `ide/revertFile`, 거절 시 `is_error("file has unsaved editor changes")`.

## 3. 내부 이벤트 → ACP 매핑 규칙 (`server/src/perdix/acp/mapping.py`)

| 내부 이벤트 | ACP |
|---|---|
| `text_delta` | `agent_message_chunk` |
| `thinking_delta` | `agent_thought_chunk` (공급자가 요약을 주는 경우만) |
| `tool_started` | `tool_call{ kind: read \| edit \| delete \| move \| search \| execute \| think \| fetch \| switch_mode \| other, status: pending }` |
| `tool_running` | `tool_call_update{status: in_progress}` |
| `tool_finished` | `tool_call_update{status: completed, content: [text \| diff]}` (로컬 셸 출력은 `text`) |
| `tool_failed` / `tool_cancelled` | `tool_call_update{status: failed}` + 사유 텍스트 |
| `verify_started` | 합성 `tool_call{kind: execute, title: "verify: <name>"}` |
| `todo_updated` | `plan` |
| `mode_changed` | `current_mode_update` |
| `config_changed` | `config_option_update` |
| `context_usage` | `usage_update{used, size, cost?}` |
| `trust_notice` | `session_info_update` |
| `history_replay(user, origin != synthetic)` | `user_message_chunk` |

## 4. 로컬 터미널 팀

팀은 서버 실행 옵션으로 구성하며 별도 ACP 확장을 추가하지 않는다(ADR 0025).
`--team-worker` CLI는 표준 `session/prompt`를 반복하고 Python 서버가 메시지를 기다린다.
대기 중에는 모델 요청과 사용량이 발생하지 않는다. 취소는 `session/cancel`이다.
팀 종료·제거 또는 사용자 취소는 `stopReason: cancelled`로 대기 요청을 끝낸다.
`/team status`, `/team stop`, `/team leave`도 일반 `session/prompt`로 전달한다.
서버는 `team` 도구 실행을 기존 `tool_call`/`tool_call_update`로 표시한다.
팀 도구는 명시적으로 팀에 참여한 세션에만 노출한다. `deny`에서도 팀 내부 통신은
허용하되 파일 쓰기·명령 실행 권한은 기존 정책으로 차단한다. 팀 메시지는 원본 세션에
`user_message(origin=synthetic)`로 append한 뒤 수신 확인한다.
