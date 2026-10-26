# Hermes Desktop으로 Perdix 쓰기

설계: [ADR 0036](adr/0036-hermes-desktop-compat-server.md). Hermes Desktop 앱은 수정하지 않는다.

## 1. Perdix 호환 서버

```bash
export PERDIX_HERMES_DESKTOP_PORT=18795
export PERDIX_HERMES_DESKTOP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
perdix --hermes-desktop --provider openrouter --model openai/gpt-6-luna
```

서버는 `127.0.0.1:18795`에서 `/api/health`, `/api/status`(공개)와 토큰이 필요한 REST,
`/api/ws`를 제공한다. `--debug`를 주면 요청·JSON-RPC 흐름을 stderr에 기록한다.

## 2. Hermes Desktop 연결

Hermes Desktop을 소스에서 빌드한다(`apps/desktop`에서 `npm install`, `npm run build`).
원격 모드 환경변수로 실행하면 로컬 Hermes 백엔드 대신 Perdix에 접속한다.

```bash
export HERMES_DESKTOP_REMOTE_URL=http://127.0.0.1:18795
export HERMES_DESKTOP_REMOTE_TOKEN="<위와 같은 토큰>"
export HERMES_HOME="$HOME/.perdix/hermes-desktop/home"   # 기존 ~/.hermes를 건드리지 않게 분리
cd apps/desktop && ./node_modules/.bin/electron --user-data-dir="$HOME/.perdix/hermes-desktop/userdata" .
```

사용하는 공급자의 키는 `~/.perdix/credentials.json` 또는 `perdix auth`로 등록한다. Perdix CLI는
하위 프로세스 env에서 비밀을 정리하므로 환경변수 키는 서버에 전달되지 않는다.

## Langflow 플로우 쓰기

서버를 `--langflow-config <파일>`(형식은 [docs/langflow.md](langflow.md))로 실행한다.

| 방법 | 사용 |
|---|---|
| 직접 실행 | 대화창에 `/flows`, `/flow <별칭> <입력>` — 모델·승인 없이 실행하고 결과를 표시 |
| 모델이 호출 | `/mode build` 후 평소처럼 요청하면 모델이 `langflow_run`을 부르고 승인 카드가 뜬다 |
| 목록 보기 | Capabilities → Skills의 `Langflow` 분류 |

호출 가능한 플로우는 설정의 허용 목록뿐이다.

## 현재 지원

채팅(스트리밍), 도구 실행 표시, 세션 목록, 모델 선택. 스킬·크론·음성·봇은 비어 있다.
