# ADR 0036: Hermes Desktop 호환 서버

상태: 채택 (1차 구현·동작 확인)

## 배경

채팅형 GUI(스트리밍 응답, 도구 실행 표시, 세션 목록, 모델 선택)가 필요하다. ADR 0035의
Electron 래퍼는 게이트웨이 작업 대시보드를 감쌀 뿐 대화형 화면이 아니다. Hermes Desktop
(MIT, `apps/desktop`)은 이미 그런 앱이고, 원격 Hermes 게이트웨이에 접속하는 기능이 있다
(`HERMES_DESKTOP_REMOTE_URL`·`HERMES_DESKTOP_REMOTE_TOKEN`). 접속 후에는 `/api/health`,
`/api/status`, 몇 개의 REST와 `/api/ws`(JSON-RPC 2.0, 연결 직후 `gateway.ready`)를 쓴다.
이 JSON-RPC는 기존 Hermes TUI 평가판(ADR 0015)이 쓰는 것과 같은 계열이라
`packages/cli/src/hermes/adapter.ts`가 이미 Perdix ACP로 변환한다.

## 결정

1. Hermes 앱과 소스는 수정하지 않는다. Perdix가 Hermes 게이트웨이처럼 보이는 서버를 제공하고
   앱이 원격 모드로 접속한다. Hermes가 업데이트돼도 서버가 필요한 계약만 지키면 된다.
2. 서버는 `packages/cli/src/hermes/desktop-server.ts`에 둔다. 에이전트 로직은 넣지 않는다
   (규칙 1). 대화·도구·권한은 `HermesAdapter` → ACP → Perdix 서버가 처리하고, 이 서버는
   HTTP/WS 전송과 REST 응답 구성만 맡는다.
3. `bridge.ts`의 소켓 처리를 `HermesSocketHub`로 분리해 TUI 브리지와 이 서버가 함께 쓴다.
4. REST는 앱이 시작할 때 요구하는 것만 구현한다: 프로필, 세션 목록, 모델 정보·옵션, 설정,
   도구·스킬·크론·메신저(빈 목록). 값이 Perdix에 있으면(세션, 모델) 어댑터 RPC로 채운다.
   나머지 Hermes 전용 기능(스킬, 크론, 음성, 봇)은 비어 있거나 동작하지 않는다.
5. 보안: 127.0.0.1에만 바인딩하고, `/api/health`·`/api/status`를 뺀 모든 요청과 WebSocket은
   토큰을 요구한다(타이밍 안전 비교). 토큰은 환경변수로만 전달하고 로그에 쓰지 않는다.
6. 실행은 `perdix --hermes-desktop`. 주소는 stdout에 출력하고 토큰은
   `PERDIX_HERMES_DESKTOP_SHOW_TOKEN=1`일 때만 출력한다.

## Langflow 플로우 사용 (추가 결정)

Hermes Desktop에서 Langflow를 쓰는 길은 세 가지이며 모두 Perdix 서버의 `langflow_run` 경로
(허용 목록, 버전 확인, 오류 정규화)를 재사용한다. Hermes 앱과 Langflow는 수정하지 않는다.

1. **모델이 호출**: 모델이 `langflow_run` 도구를 부른다. 실행형이라 build 모드에서만 보이고 Hermes
   승인 카드(`Reject`/`Always allow`/`Run`)로 사용자 승인을 받는다.
2. **직접 실행**: 대화창에 `/flows`(목록), `/flow <별칭> <입력>`을 입력한다. 사용자가 직접 입력한
   명령이므로 모델·승인 없이 서버가 바로 실행하고(`acp/flow_commands.py`), 허용 목록은 그대로 적용한다.
   결과는 대화에 표시되지만 모델의 맥락에는 들어가지 않는다.
3. **목록 보기**: Capabilities → Skills 화면에 플로우가 `Langflow` 분류의 스킬로 나온다. 호환 서버가
   서버와 같은 `--langflow-config` 파일에서 별칭·설명만 읽는다(읽기 전용, 실행·허용 판단은 서버).

## Langflow 편집 화면을 앱 안에서 열기 (추가 결정)

Hermes Desktop의 공식 확장 문인 **런타임 플러그인**(`$HERMES_HOME/desktop-plugins/<이름>/plugin.js`,
ESM + `@hermes/plugin-sdk`)을 쓴다. 앱 소스는 수정하지 않는다.

- `perdix --hermes-desktop --langflow-config <파일> --hermes-home <HERMES_HOME>`로 서버를 켜면
  `desktop-plugins/langflow/plugin.js`를 설치한다(`hermes/langflow-plugin.ts`의 소스). 설정이 없거나
  꺼져 있으면 설치하지 않는다. 기존 `~/.hermes`는 명시적으로 지정하지 않는 한 건드리지 않는다.
- 플러그인은 `routes` 영역에 `/langflow` 페이지를, `sidebar.nav` 영역에 왼쪽 사이드바의 `Langflow`
  항목(Scheduled jobs 아래)을 등록한다. 항목을 누르면 메인 영역에 Langflow 편집 화면이 열린다. 페이지 안은
  `<webview partition="persist:perdix-langflow">`다. iframe이 아니라 webview를 쓰는 이유는 앱(file://)과
  Langflow가 다른 사이트라 iframe에서는 로그인 쿠키가 막힐 수 있기 때문이다(Hermes의 웹 미리보기 창도
  webview를 쓴다). 처음에는 상태 표시줄 버튼+`openWorkspace` 탭으로 만들었다가 사이드바 요청에 따라 바꿨다.
- 주소는 파일에 박지 않고 플러그인이 `ctx.rest('/config')`(`GET /api/plugins/langflow/config`)로
  호환 서버에 묻는다. 서버는 `--langflow-config`의 `base_url`과 `/health` 응답 여부를 돌려주고,
  꺼져 있으면 페이지에 안내 문구와 `다시 확인` 버튼을 보여 준다.
- 대화창의 `/langflow`는 어댑터가 `perdix.langflow.open` 이벤트를 보내고, 플러그인이 `ctx.onEvent`로
  받아 `host.navigate('/langflow')`로 같은 페이지를 연다.
- Langflow는 UI를 포함해 실행해야 하고(`--backend-only` 아님), 개인용으로 `LANGFLOW_AUTO_LOGIN=true`를
  권장한다. 이때도 `LANGFLOW_API_KEY` 환경변수 인증은 그대로 동작한다.

## 첫 화면의 큰 글자 숨기기 (추가 결정)

새 세션 첫 화면의 큰 글자 `HERMES AGENT`는 Hermes 코어(`components/chat/intro.tsx`)가 상수로 그리며 플러그인이
대체할 수 있는 영역이 아니다(`chat.empty`는 이미 있는 빈 세션용). 처음에는 앱 소스를 고치지 않으려고 같은
플러그인이 글자를 `PERDIX`로 바꿨으나 큰 글자 디자인이 어색해서, 지금은 `MutationObserver`로 그려진 큰 글자
(`[data-slot="aui_intro"] .wordmark`)를 숨기기만 한다. 아래의 짧은 안내 문장은 그대로 둔다. Hermes의 DOM 구조가
바뀌면 글자가 다시 보일 뿐 기능에는 영향이 없다. 창 제목, 설정 문구 등 다른 `Hermes` 표기는 바꾸지 않았다.

## Langflow 화면의 홍보 요소 숨기기 (추가 결정)

Langflow 화면에는 상단의 GitHub 별·Discord 인원 표시, 왼쪽 `Get started` 안내(Star repo, Join the
community), 프로필 메뉴의 커뮤니티 링크가 기본으로 있어 앱 안에서 이질적이다. 플러그인이 `<webview>`의
`dom-ready`마다 숨김 스크립트를 주입한다(`MutationObserver`). 상단 아이콘은 아이콘 모양(GitHub 496x512,
Discord 640x512)으로 상단 바 안의 버튼만, 나머지는 `data-testid`로 찾는다. Langflow 소스는 고치지 않는다.
숨기기만 하며 Langflow가 별·인원을 가져오는 외부 요청(api.github.com, discord.com)은 막지 않는다.
Langflow 화면 구조가 바뀌면 숨김이 풀릴 뿐 기능에는 영향이 없다.

## Bots 끄기와 plugins.manage (추가 결정)

`BOTS`(Bot Mode)는 Hermes 프로필 체계에 기대는 기능이라 Perdix에서는 명단을 읽는 `profiles.list`가 지원되지
않아 쓸 수 없다. Hermes는 플러그인이 다른 플러그인의 활성 상태를 바꾸지 못하게 일부러 막아 두었으므로
(`host.pluginDecisions`는 읽기 전용) 우리 플러그인이 대신 끄지 않고, 사용자가 `Capabilities → Plugins`의
스위치로 끈다. 그 화면은 에이전트 플러그인 목록(`plugins.manage` `list`)을 못 받으면 데스크톱 플러그인
스위치까지 통째로 오류가 나므로, 호환 서버의 어댑터가 `list`에 빈 목록을 답하게 했다. 다른 동작은
지원하지 않는다고 오류로 답한다.

## 검증 (2026-10-26)

Hermes Desktop 소스 `63d0b94`를 별도 클론에서 빌드(`npm install`, `npm run build`)해
격리된 `--user-data-dir`·`HERMES_HOME`으로 실행했다. 앱이 `Backend ready`로 부팅했고, 새
세션에서 스트리밍 응답, 도구 2개 실행(`Used 2 tools`), 모델 선택기에 Perdix 모델 표시를 확인했다.

## 알려진 한계와 다음 일

- 앱이 보내는 RPC 중 `profiles.list`, `pet.info`, `projects.tree`, `complete.path` 등은 어댑터가
  지원하지 않아 오류로 응답한다(화면은 동작).
- 도구 호출 전에 나온 텍스트가 최종 응답에 한 번 더 붙는 현상이 있다(어댑터의 텍스트 누적).
- 플러그인은 Hermes의 플러그인 SDK(`routes`·`sidebar.nav` 영역, `host.navigate`, `ctx.rest`, `ctx.onEvent`)에 의존한다.
  SDK가 바뀌면 깨질 수 있어 `63d0b94` 기준으로 확인했다.
- Skills 화면의 켜기/끄기·Edit·Archive는 서버가 지원하지 않아 동작하지 않는다.
- 도구 카드가 `Result unavailable`로 표시된다(어댑터가 `tool.complete`에 결과를 싣지 않음).
- 세션 목록은 이어 보기·삭제·이름 변경 REST를 아직 구현하지 않았다.
- Hermes 쪽 버전이 바뀌면 필수 REST가 달라질 수 있다. 계약 테스트는 서버 쪽만 있다.
- 상주 실행(launchd)과 앱 설치·업데이트 절차는 정하지 않았다.
- ADR 0035의 Electron 래퍼는 이 결정으로 대체될 가능성이 크다(정리 여부는 별도 결정).
