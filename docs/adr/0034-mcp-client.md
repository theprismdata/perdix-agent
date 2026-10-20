# ADR 0034: MCP 클라이언트(stdio 도구)

상태: 채택

## 결정

Perdix 서버에 MCP 클라이언트를 추가한다. 범위는 **stdio 전송과 `tools` 기능**으로 한정한다.
resources, prompts, sampling, elicitation, Streamable HTTP/SSE 전송은 이번 범위가 아니다.

공식 Python SDK(`mcp`)는 쓰지 않고 `server/src/perdix/mcp/`에 명세 2025-06-18의 stdio 부분만
구현한다. 이유는 다음과 같다.

- SDK 2.x는 `StdioServerParameters`의 env 병합 규칙과 도구 속성(annotations) 필드를 공식 문서만으로
  확인할 수 없었다(AGENTS.md: 불확실한 외부 SDK는 추측하지 않는다).
- `mcp-types`, starlette, opentelemetry 등 의존성이 늘어난다. stdio 도구 호출은 JSON-RPC 몇 개로
  끝나며 프로세스 그룹·취소·env 정리를 직접 통제해야 한다(AGENTS.md 규칙 12).

## 프로토콜

- 메시지는 줄바꿈으로 구분한 UTF-8 JSON-RPC이며 줄 안에 개행을 넣지 않는다.
- `initialize`(protocolVersion `2025-06-18`, capabilities 빈 객체) → 응답 → `notifications/initialized`.
  서버가 `2025-06-18`, `2025-03-26`, `2024-11-05` 중 하나가 아닌 버전을 돌려주면 연결을 끊는다.
- `tools/list`(`nextCursor` 페이지네이션, 최대 500개), `tools/call`.
- 서버가 보낸 요청은 `ping`만 응답하고 나머지는 `-32601`로 거절한다. 알림은 무시한다.
- 요청마다 타임아웃을 두고, 초과하거나 취소되면 `notifications/cancelled`를 보낸다.
- 종료는 stdin 닫기 → SIGTERM → SIGKILL 순서로 프로세스 그룹에 보낸다.
- 서버의 stderr는 읽어서 로그로만 남긴다(상한 있음). stdout의 JSON이 아닌 줄은 무시한다.

## 설정과 신뢰 경계

| 출처 | 신뢰 |
|---|---|
| ACP `session/new`의 `mcpServers`(stdio) | 클라이언트가 명시한 것이므로 신뢰 |
| `~/.perdix/mcp.json` | 사용자 전역 설정이므로 신뢰 |
| 프로젝트 `.mcp.json` | **`trust_project`일 때만** 읽는다. 신뢰 전에는 실행하지 않는다(규칙 9) |

우선순위는 ACP 인수 > 프로젝트 > 전역이며 같은 이름은 상위가 덮어쓴다.
형식은 Claude Code와 같다: `{"mcpServers": {"이름": {"command", "args", "env", "cwd"}}}`.
Perdix 확장 키는 `readOnly`(bool), `timeout`(초), `enabled`(bool)이다. `.perdix/config.json`과
`.codex/config.toml`은 이번 범위에서 읽지 않는다.

## 하위 프로세스 환경(규칙 12)

서버 프로세스는 `scrub_env(os.environ)`로 비밀 패턴(`*_TOKEN`, `*_KEY`, `*SECRET*` 등)을 제거한
환경에 **설정에 명시한 `env`만** 더해 실행한다. 명시한 값은 사용자가 의도한 것이므로 제거하지 않는다.
`ExecBackend`는 장수 프로세스·양방향 파이프 계약이 없어 허브가 같은 `scrub_env`를 직접 적용한다.

## 도구 노출과 권한

- 이름은 `mcp__<서버>__<도구>`이며 공급자 제약에 맞춰 `[A-Za-z0-9_-]` 64자 이내로 정규화한다.
  충돌하면 짧은 해시를 붙인다.
- 입력은 서버가 준 `inputSchema`로 실행 전에 검증하고, 실패는 `is_error` 결과로 돌려준다(규칙 5).
- **도구 annotations는 신뢰하지 않는다**(명세의 요구). 기본은 쓰기 도구(`kind="other"`)이고
  권한 정책은 `ask`다. 사용자가 서버 항목에 `"readOnly": true`를 명시한 경우에만 `kind="search"`로
  보고 자동 허용하며 plan 모드에도 노출한다.
- 도구 결과의 텍스트는 데이터이며 지침이 아니다. 이미지·오디오는 자리 표시 문자열로 대체하고
  결과는 100,000자로 자른다.
- 서버 시작 실패와 `tools/list` 실패는 세션 생성을 막지 않고 경고 로그로만 남긴다.

## 수명주기

`PerdixAgent`가 `McpHub` 하나를 소유한다. 같은 (이름, 명령, 인수, env, cwd)의 연결은 세션 사이에서
재사용하고, 연결이 죽었으면 다음 세션에서 다시 시작한다. 서버 종료(`aclose`)에서 모두 닫는다.
메인 에이전트의 일반 턴과 목표 루프 턴에 노출하며, 서브에이전트(`task`)에는 노출하지 않는다.

## 영향

- ACP 메서드와 저장 스키마는 바뀌지 않는다. 세션 이벤트 로그에는 기존 도구 호출 기록이 그대로 남는다.
- 새 외부 의존성은 없다.
- 게이트웨이 프로젝트는 `trust_project: true`이고 해당 프로젝트에 `.mcp.json`이 있을 때, 또는
  `~/.perdix/mcp.json`이 있을 때 MCP 도구를 쓴다.

## 근거

- https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
- https://modelcontextprotocol.io/specification/2025-06-18/server/tools
