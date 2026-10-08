# ADR 0003: VS Code 확장은 통합 터미널에서 TUI를 재사용

- 상태: 채택 (2026-10-07)

## 결정
1차 VS Code 확장은 Webview를 만들지 않는다. 통합 터미널에 `perdix` CLI를 띄우고, IDE 브리지(WS/JSON-RPC)로 diff 뷰·파일 열기·선택 영역·진단만 연동한다.

## 근거
- UI 코드가 한 벌(Ink TUI)로 유지된다.
- Claude Code 확장이 같은 방식으로 검증된 경로다.

## 결과
- 리치 UI(사이드바 채팅)는 2차 과제. 그때 Webview는 두 번째 ACP 클라이언트로 붙이며 서버 변경은 없다.
- TUI 프레임워크는 Ink를 쓴다. OpenTUI는 Bun/Node 26 FFI를 요구해 통합 터미널 배포 조건과 맞지 않는다. UI 계층을 분리해 교체 여지를 남긴다.
