# MCP 서버 연결

Perdix는 MCP 서버의 **도구(tools)**를 stdio로 연결해 에이전트가 쓸 수 있게 한다.
범위와 보안 결정은 [ADR 0034](adr/0034-mcp-client.md)에 있다.

## 설정 위치

| 위치 | 읽는 조건 |
|---|---|
| `~/.perdix/mcp.json` | 항상(사용자 전역 설정) |
| 프로젝트의 `.mcp.json` | 서버를 `--trust-project`로 시작했고 세션 cwd가 그 프로젝트일 때만 |
| ACP `session/new`의 `mcpServers` | 클라이언트가 보낸 stdio 항목 |

같은 이름이면 ACP 인수가 프로젝트를, 프로젝트가 전역을 덮어쓴다. 서버를 실행할 수 없거나
`tools/list`가 실패해도 세션은 그대로 시작하고 서버 로그에 경고만 남긴다.

## 형식

Claude Code의 `.mcp.json`과 같다. HTTP/SSE 서버 항목은 건너뛴다.

```json
{
  "mcpServers": {
    "autorag": {
      "command": "/Users/me/.bun/bin/autorag-mcp",
      "args": [],
      "env": {
        "AUTORAG_CONFIG": "/Users/me/.autorag/config.json",
        "AUTORAG_MCP_READ_ONLY": "1"
      },
      "readOnly": true,
      "timeout": 240
    }
  }
}
```

| 키 | 설명 |
|---|---|
| `command`, `args` | 실행할 서버. 명령은 절대 경로를 권장한다 |
| `env` | 서버에 **추가로** 넘길 환경 변수. 비밀 패턴 환경 변수는 기본으로 제거되며, 여기에 명시한 값만 전달된다 |
| `cwd` | 서버의 작업 폴더(기본: 세션 cwd) |
| `readOnly` | `true`면 이 서버의 도구를 읽기 전용으로 보고 권한 확인 없이 허용하며 plan 모드에도 노출한다. 기본은 `false` |
| `timeout` | 도구 호출 제한 시간(초, 기본 120) |
| `enabled` | `false`면 건너뛴다 |
| `hint` | 이 서버의 모든 도구 설명 뒤에 붙는 사용 안내 |
| `omitParams` | 도구별로 모델에게 숨길 선택 인수. 예: `{"autorag.search": ["scope", "methods"]}`. 필수 인수는 숨기지 않으며 숨긴 인수를 보내면 검증 오류가 된다 |

## 도구 이름과 권한

도구는 `mcp__<서버>__<도구>`로 보인다(영문·숫자·`_`·`-`만 사용, 점 등은 `_`로 바뀐다).
예: `autorag.search` → `mcp__autorag__autorag_search`.

- 서버가 말하는 `readOnlyHint` 같은 annotations는 신뢰하지 않는다. 읽기 전용 허용은 사용자가 `readOnly`로
  명시한 서버에만 적용한다.
- `readOnly`가 아닌 서버의 도구는 `ask`다. `deny`, `accept-edits`, `allowlist` 모드에서는 거부된다.
- 도구 결과는 100,000자로 자르고 이미지·오디오는 자리 표시로 바꾼다.
- 서브에이전트(`task`)에는 MCP 도구를 노출하지 않는다.

## 예: AutoRAG를 지식 베이스로

1. `bun install -g @autorag/librarian`, `autorag lite init --search-paths <문서 폴더>`, `autorag lite refresh`
2. 위 예시대로 `~/.perdix/mcp.json`에 `autorag-mcp`를 `AUTORAG_MCP_READ_ONLY=1`, `readOnly: true`로 등록한다.
3. 새 세션에서 `mcp__autorag__autorag_search`로 문서를 검색한다. 색인은 사람이 `autorag lite refresh`로 갱신한다.

## 모델이 선택 인수를 잘못 채울 때

일부 모델은 모든 선택 인수를 채워 보낸다. 서버가 빈 배열이나 `"all"`을 "아무것도 선택하지 않음"으로 해석하면
결과가 0건이 된다(AutoRAG의 `scope`, `datasourceIds`, `methods`가 이 경우다). 설명 문구(`hint`)만으로는
모델이 무시할 수 있으므로 `omitParams`로 해당 인수를 스키마에서 숨기는 것이 확실하다.

```json
"omitParams": {"autorag.search": ["scope", "datasourceIds", "methods", "strict", "local"]}
```

## 문제 해결

- 도구가 안 보이면 서버 로그의 `MCP` 경고를 확인한다(명령 경로, 프로젝트 미신뢰, 프로토콜 버전).
- 서버 로그에는 서버의 stderr 마지막 줄이 종료 사유에 붙는다.
- 설정을 바꾼 뒤에는 새 세션을 만들어야 반영된다. 같은 설정의 서버 프로세스는 세션 사이에서 재사용한다.
