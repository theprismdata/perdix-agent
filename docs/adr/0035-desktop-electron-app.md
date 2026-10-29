# ADR 0035: 데스크톱 네이티브 앱(Electron)

상태: 대체됨 ([ADR 0036](0036-hermes-desktop-compat-server.md)) — `packages/desktop` 제거함

ADR 0036의 Hermes Desktop 호환 서버가 실시간 스트리밍 채팅·도구 실행 표시·모델 선택이
되는 네이티브 GUI를 이미 제공해서, 이 ADR의 게이트웨이 대시보드 전용 Electron 래퍼
(`packages/desktop`)는 더 쓰지 않는다. 아래 내용은 기록용으로 남긴다.

## 배경

ADR 0003은 "UI 코드 두 벌"을 피하려고 VS Code 확장을 터미널 TUI 재사용으로 시작했고,
ADR 0008에서 사용자 요청에 따라 Webview 채팅으로 대체됐다(여기서도 서버는 그대로 두고
클라이언트 쪽 UI만 Webview로 바뀐 것). ADR 0012로 생긴 `perdix-gateway`(로컬 상주 HTTP
대시보드)는 세 번째 UI이지만, 코딩 워크플로 스튜디오에 이어 온톨로지 스튜디오 화면까지
들어가면서 브라우저로만 쓰기엔 아쉽다는 요청이 있었다 — 독립 실행 파일(.app/.exe)로
패키징해 달라는 것.

## 결정

Electron으로 새 패키지 `packages/desktop`을 만들어 **기존 `perdix-gateway` HTTP
대시보드를 네이티브 창으로 감싸는 얇은 래퍼**로 한정한다. Electron 쪽에 새 UI 코드를
두지 않는다 — ADR 0003이 피하려던 "UI 코드 두 벌" 문제를 다시 만들지 않기 위해서다.

- Electron 메인 프로세스가 `perdix-gateway`를 자식 프로세스로 스폰한다. CLI가
  `perdix-server`를 스폰하는 기존 패턴(`packages/cli/src/agent/spawn.ts`)과 서버 탐색
  방식(설정된 경로 → PATH → Windows 설치 프로그램 동봉 서버, ADR 0009)을 그대로 따른다.
- 게이트웨이가 loopback 포트에서 뜨면 `BrowserWindow`가 그 URL을 로드한다. 대시보드
  HTML/CSS/JS(`server/src/perdix/gateway/static/`)는 그대로 재사용하며 Electron 전용
  분기를 넣지 않는다 — 서버는 여전히 Electron을 모른다(경계 규칙 2의 연장).
- 연결 토큰은 쓰지 않는다. 개인용 로컬 앱이므로 게이트웨이는 `auth`를 생략하면 루프백 host에서
  `none`(토큰 없음)으로 뜨고, 대시보드는 `X-Perdix-Client` 헤더와 로컬 Origin 검사로만 요청을
  받는다(`docs/gateway.md`). 원격 bind나 외부 네트워크 노출은 다루지 않는다.
- 창은 OS 네이티브 타이틀바·메뉴를 쓴다. 트레이 아이콘에서 서버 상태 확인과 재시작을
  제공한다. 다중 창, 자동 업데이트, 코드 서명은 이번 범위 밖이다.

## 범위

1차는 macOS만 다룬다. Windows/Linux 배포는 기존 설치 프로그램 ADR(0006, 0007, 0009)을
참고해 후속 ADR로 다룬다. `packages/desktop`이 CLI의 서버 스폰/탐색 로직을 소스 import로
재사용할지 공용 모듈로 뺄지는 구현 시점에 정한다(ADR 0008이 CLI의 ACP 어댑터를 소스
import로 재사용한 선례를 따를 가능성이 높다).

## 영향

- ACP 프로토콜, 저장 포맷, 서버 코드는 바뀌지 않는다. 새 외부 의존성(`electron`,
  패키징 도구)은 `packages/desktop`에만 추가된다.
- 게이트웨이 대시보드 쪽에 추가되는 신규 화면(온톨로지 스튜디오 등)은 이 ADR과 무관하게
  계속 `server/src/perdix/gateway/static/`에서 진행한다 — Electron 래퍼는 그 화면을
  그대로 물려받는다.
- 과거 세션 복원, 다중 사용자 호스팅은 ADR 0012와 동일하게 이번 범위 밖이다.
