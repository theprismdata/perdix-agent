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

Dock 아이콘을 Perdix 것으로 띄우려면 [integrations/hermes-desktop](../integrations/hermes-desktop/README.md)의
실행 껍데기를 만들고 `apps/desktop` 대신 `apps/perdix`에서 실행한다. Hermes 파일은 고치지 않는다.

사용하는 공급자의 키는 `~/.perdix/credentials.json` 또는 `perdix auth`로 등록한다. Perdix CLI는
하위 프로세스 env에서 비밀을 정리하므로 환경변수 키는 서버에 전달되지 않는다.

## 설정 창

Hermes 설정 메뉴 중 Perdix에 연결되는 것만 남기고 나머지는 `perdix-settings` 플러그인이 감춘다
(`packages/cli/src/hermes/settings-plugin.ts`). Hermes 파일은 고치지 않고, 메뉴 항목의 `data-tour` 표식에
스타일 규칙을 건다.

| 남는 메뉴 | 동작 |
| --- | --- |
| Model › Main model | 새 대화의 기본 모델. 저장하면 agents.json의 `general` 역할 할당과 이 연결의 새 대화 기본값, 지금 보는 대화의 모델이 바뀐다. OpenRouter 모델만 저장된다 |
| Appearance (Pet 제외) | 테마·글꼴·창 배치. 앱 안에서만 처리한다 |
| Notifications | 데스크톱 알림·소리 |
| Gateways (Remote updates 제외) | 이 앱이 붙는 서버 연결 |
| Keyboard Shortcuts | 단축키 |
| Plugins | 플러그인 설정 페이지 |

감춘 메뉴: Chat, Workspace, Safety, Browser, Passwords & Logins, Memory & Context, Voice, Advanced, Billing,
Providers, Tools & Keys, Sessions, About, 그리고 Model의 Fallback·Auxiliary·Mixture of Agents. 모두 Hermes 설정
파일이나 Nous 서비스에 기대는 화면이라 Perdix에서는 열어도 실패하거나 저장되지 않는다.

### 한국어 메뉴

Hermes에는 한국어 번역이 없어서 Perdix가 한국어 팩(`packages/cli/src/hermes/locale-ko.ts`, 약 2,000문구)을 넣는다.
설정 → 모양 → 일반 → **언어**에서 "한국어"를 고르면 적용되고, 고른 언어는 `<HERMES_HOME>/perdix-desktop-config.json`에
저장돼 다시 열어도 유지된다.

- 팩은 `perdix-settings` 플러그인이 시작할 때 Hermes SDK(`ctx.i18n.registerAppLocale`)로 등록한다. 서버의
  `i18n.catalog`도 같은 문구(문자열만)를 돌려주지만, 원격 모드에서는 Hermes가 이 요청을 보내지 않을 때가 있다.
- 번역 범위는 화면에 보이는 영역(사이드바, 입력창, 대화 화면, 상태 표시, 단축키, 알림, 남긴 설정 메뉴)이다.
  나머지는 영어로 남는다. 앱·에이전트를 가리키는 "Hermes"는 "Perdix"로 옮겼다.
- 탭 이름 "NEW SESSION"은 Hermes 코드의 상수라 바뀌지 않는다. 입력창 안내 문구는 앱을 연 직후 첫 화면에서만
  영어로 나오고, 다른 대화로 옮기면 한국어가 된다(입력창이 팩 등록보다 먼저 문구를 고르기 때문).

주의:
- `general` 역할은 Langflow 중계의 general 역할(샘플 플로우)도 같이 쓴다. Main model을 바꾸면 그쪽도 바뀐다.
- 서버를 `--model`로 띄우면 다시 띄울 때 새 대화 기본값이 그 값으로 돌아간다. agents.json 할당만 계속 쓰려면
  `--model` 없이 띄운다.
- 설정 창은 마지막으로 연 화면을 기억한다. 감춘 화면을 열어 둔 채였다면 다른 메뉴를 한 번 누르면 된다.

## Langflow 플로우 쓰기

서버를 `--langflow-config <파일>`(형식은 [docs/langflow.md](langflow.md))로 실행한다.

| 방법 | 사용 |
|---|---|
| 직접 실행 | 대화창에 `/flows`, `/flow <별칭> <입력>` — 모델·승인 없이 실행하고 결과를 표시 |
| 모델이 호출 | `/mode build` 후 평소처럼 요청하면 모델이 `langflow_run`을 부르고 승인 카드가 뜬다 |
| 목록 보기 | Capabilities → Skills의 `Langflow` 분류 |

호출 가능한 플로우는 설정의 허용 목록뿐이다.

## Workflow 편집기 (Hermes 안에서 플로우 만들기)

`--hermes-home`으로 켜면 사이드바에 `Workflow` 항목이 생긴다. 팔레트에서 노드를 넣고 끌어서 잇고 저장하면
Langflow에 반영된다. 만드는 순서, 역할 고르기, `/flow` 별칭 등록, 문제 해결은 [docs/hermes-workflow.md](hermes-workflow.md)에 있다.

## Langflow 편집 화면 열기

서버를 `--langflow-config <파일> --hermes-home <HERMES_HOME>`로 켜면 Hermes 플러그인
(`<HERMES_HOME>/desktop-plugins/langflow/plugin.js`)이 설치된다. Hermes 앱을 (재)시작하면:

- 왼쪽 사이드바의 `Langflow` 항목(Scheduled jobs 아래)을 누르거나, 대화창에 `/langflow`를 입력(슬래시
  자동완성 때문에 Enter를 두 번)하면 메인 영역에 Langflow 편집 화면이 열린다.
- 화면 안에서 플로우 만들기·편집·Playground 실행을 그대로 쓸 수 있다. 로그인 상태는 앱 안에 유지된다.
- Langflow가 꺼져 있으면 안내 문구와 `다시 확인` 버튼이 보인다.

Langflow는 UI를 포함해 띄워야 한다(`langflow run`, `--backend-only` 제외). 개인용이면
`LANGFLOW_AUTO_LOGIN=true`가 편하다. GitHub·Discord 표시와 시작 안내가 없는 Perdix 판 화면은
`packages/langflow-ui/build.sh`로 빌드해 `langflow run --frontend-path ~/.perdix/langflow-ui/build`로 쓴다
([README](../packages/langflow-ui/README.md)). 화면을 바꾼 뒤에는 이미 열려 있는 Hermes 앱의
Langflow 페이지가 이전 문서를 붙잡고 있을 수 있으니 Hermes 앱을 다시 시작한다. 플러그인은 Hermes 앱을 수정하지 않는 공식 확장 방식이다.

## 사이드바

플러그인이 `Messaging`, `Artifacts`, `Scheduled jobs`를 숨긴다(Perdix에 없는 기능). 남는 항목은 `New session`,
`Capabilities`(Skills의 Langflow 플로우, Plugins), `Langflow`다. 다시 보이게 하려면
`packages/cli/src/hermes/langflow-plugin.ts`의 `hide` 목록에서 빼고 서버를 다시 시작한다.

## Bots 탭 끄기

사이드바의 `BOTS`는 Hermes의 Bot Mode(프로필을 봇 명단으로 보여 주는 기능)이고 Perdix에는 프로필이 없어
쓸 수 없다. Hermes는 플러그인이 다른 플러그인을 끄지 못하게 설계돼 있어서 사용자가 앱에서 직접 끈다:
**Capabilities → Plugins → Bots의 Desktop 스위치를 끈다.** 바로 적용되고 앱 데이터(`hermes.desktop.pluginDecisions.v2`)에
저장돼 재시작해도 유지된다. 앱 데이터 폴더(`--user-data-dir`)를 새로 만들면 다시 꺼야 한다.
이 화면이 열리도록 호환 서버는 `plugins.manage`의 `list`에 빈 목록으로 답한다(Perdix에는 Hermes식 에이전트
플러그인이 없다).

## 현재 지원

채팅(스트리밍), 도구 실행 표시, 세션 목록, 모델 선택. 스킬·크론·음성·봇은 비어 있다.
