# ADR 0027: Langflow가 하네스 루프를 소유하는 실행 백엔드

상태: 초안.

## 맥락
하네스 루프(LLM 호출 → 도구 → 검증)를 Langflow 플로우로 구성해 시각적으로 편집·교체하고 싶다.
Perdix는 계속 ACP 서버로서 세션·이벤트 로그·권한 UI를 제공하고, 한 Turn의 실행만 플로우에 맡긴다.

## 결정
- `--langflow-url`과 `--langflow-flow`를 주면 서버는 `TurnRunner` 대신 `LangflowTurnRunner`
  (`server/src/perdix/harness/langflow.py`)로 Turn을 실행한다. 둘 다 같은 `run()` 시그니처를 가진다.
- 호출은 Langflow의 `POST /api/v1/run/{flow}?stream=true`다. ACP 세션 ID를 Langflow `session_id`로
  넘겨 플로우 메모리가 세션과 같은 단위가 되게 한다.
- 스트림 이벤트 변환: `token` → `TextDelta`, `add_message`의 `tool_use` 콘텐츠 →
  `ToolStarted`/`ToolFinished`/`ToolFailed`, `end` → 종료. 토큰이 없었으면 최종 메시지 텍스트를 보낸다.
- 사용자 메시지와 최종 assistant 텍스트는 평소처럼 event log에 append한다(`session/load` 복구 유지).
- API 키는 `LANGFLOW_API_KEY` 환경변수로만 받는다. 설정 파일에는 적지 않는다.
- ACP/`_perdix` 프로토콜 변경은 없다.

## LLM 중계와 내장 도구 노출 (`server/src/perdix/relay/`)
Langflow Agent는 API 키만 받고 OAuth를 쓰지 못한다. 그래서 `perdix-relay`가 Perdix 인증으로 외부 도구의
LLM 호출과 도구 사용을 대신 처리한다. 127.0.0.1 전용이고 `~/.perdix/relay-token`(0600) Bearer 토큰을 요구한다.
- `POST /v1/chat/completions`(스트리밍 포함): OpenAI 와이어를 기존 `Provider` 어댑터 호출로 바꾼다.
  공급자 키와 OAuth는 이 프로세스 밖으로 나가지 않는다. 모델명은 `perdix:<모델>`로 쓴다
  (langchain이 gpt-5 이상 이름을 Responses API로 보내는 문제를 피한다).
- `GET /v1/agents`, `GET /v1/agents/{role}`(`--workspace` 지정 시): Perdix 역할(chat, plan, build와 서브에이전트
  프리셋)이 안에서 실제로 쓰는 시스템 프롬프트(`build_system_prompt`), 모델(`agents.json` 할당 → 프리셋 기본값),
  도구 목록을 돌려준다. 도구는 모드의 읽기 전용 여부로 좁힌 내장 도구이고, 중계가 실행할 수 없는
  `task`, `session_search`, `memory_*`는 `omittedTools`로 이유와 함께 밝힌다.
- `POST /mcp/{role}`(`/mcp`는 chat): 그 역할의 내장 도구를 MCP(streamable HTTP, JSON 응답)로 연다.
  호출은 Perdix `PermissionPolicy`가 판정한다. 승인 채널이 없어서 `ask`는 거부하고, 기본 `--permission-mode deny`는
  읽기 전용 내장 도구만 통과시킨다. `accept-edits`로 띄워야 `write`/`edit`가 실행된다.
- 모델 `perdix:role/<역할>`은 그 역할에 할당된 모델로 호출한다.
- Langflow 컴포넌트 `Perdix Agent`(`integrations/langflow/components/perdix/perdix_agent.py`)가 위 HTTP
  계약만 써서 역할의 Instructions / Language Model / Tools를 각각 출력한다. 표준 Agent 노드에 연결하면
  Perdix 구성이 그대로 적용되고, 역할을 바꾸면 노드를 다시 실행할 때 따라온다. 소스 수정 없이
  `LANGFLOW_COMPONENTS_PATH`로 로드한다.
- 공급자 생성은 `llm/factory.py`로 모아 에이전트와 중계가 공유한다.
- 검증된 샘플 `perdix-harness-roles`: plan → explore → reviewer 세 역할을 Perdix Agent 노드로 구성해
  Perdix 서버로 실행했다. 중계 로그에서 역할마다 할당된 모델이 호출됐고 `glob`/`grep`/`read` 도구
  이벤트가 ACP 쪽에 전달됐다.
- 한계: Langflow Agent는 자체 루프를 돌리고 기본 도구(`get_current_date`, `evaluate_expression`)를 붙인다.
  Perdix 하네스의 압축·체크포인트·승인 흐름은 적용되지 않는다.
- Langflow UI는 출력이 여럿인 노드에서 선택된 출력의 연결만 유효로 본다. 플로우를 코드로 만들 때
  `data.selected_output`을 맞추고, 도구는 tool_mode 노드(`component_as_tool`)를 써야 한다.

## 제약
- 플로우 안의 도구는 Langflow가 실행하므로 Perdix의 권한 정책·체크포인트·검증 게이트는 적용되지 않는다.
  `ask_permission`은 호출되지 않는다. 워크스페이스를 직접 바꾸는 플로우는 Langflow 쪽 책임이다.
- `/loop`, `/team`은 이 백엔드에서 지원하지 않는다.
- 취소는 HTTP 스트림을 닫는 것으로 구현한다. Langflow가 이미 시작한 도구 호출은 중단되지 않을 수 있다.
