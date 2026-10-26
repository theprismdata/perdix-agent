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

## 검증 (2026-10-26)

Hermes Desktop 소스 `63d0b94`를 별도 클론에서 빌드(`npm install`, `npm run build`)해
격리된 `--user-data-dir`·`HERMES_HOME`으로 실행했다. 앱이 `Backend ready`로 부팅했고, 새
세션에서 스트리밍 응답, 도구 2개 실행(`Used 2 tools`), 모델 선택기에 Perdix 모델 표시를 확인했다.

## 알려진 한계와 다음 일

- 앱이 보내는 RPC 중 `profiles.list`, `pet.info`, `projects.tree`, `complete.path` 등은 어댑터가
  지원하지 않아 오류로 응답한다(화면은 동작).
- 도구 호출 전에 나온 텍스트가 최종 응답에 한 번 더 붙는 현상이 있다(어댑터의 텍스트 누적).
- 세션 목록은 이어 보기·삭제·이름 변경 REST를 아직 구현하지 않았다.
- Hermes 쪽 버전이 바뀌면 필수 REST가 달라질 수 있다. 계약 테스트는 서버 쪽만 있다.
- 상주 실행(launchd)과 앱 설치·업데이트 절차는 정하지 않았다.
- ADR 0035의 Electron 래퍼는 이 결정으로 대체될 가능성이 크다(정리 여부는 별도 결정).
