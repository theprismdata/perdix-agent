# ADR 0008: VS Code Webview 채팅

- 상태: 채택 (2026-10-11)
- ADR 0003의 터미널 전용 UI 결정을 사용자 요청에 따라 대체한다.

## 결정

Perdix 사이드바에 Webview 채팅을 제공한다. 확장 호스트가 Python 서버를 ACP stdio로 실행하고 Webview는 검증된 메시지를 통해 표시와 사용자 입력만 담당한다. LLM 호출, 도구 실행과 권한 정책은 계속 Python에 둔다. 서버에 VS Code 의존성을 추가하지 않는다.

CLI의 기존 ACP 연결·세션 어댑터를 소스 import로 재사용하고 확장 번들에 포함한다. 새 프로토콜이나 저장 형식은 추가하지 않는다. 화면 이동·숨김에는 세션을 유지하고, 확장 종료 및 새 대화에는 서버 연결을 정리한다. 과거 세션은 ACP session/list와 session/load로 복원한다.

Windows 설치 프로그램의 동봉 서버를 자동 탐색한다. 다른 환경에서는 서버 경로 설정 또는 PATH를 이용한다. 프로젝트 신뢰와 로컬 작업 폴더를 확인한 뒤에만 서버를 시작한다. 키는 기존 사용자 설정에서 서버가 읽으며 Webview로 보내지 않는다.

Webview는 외부 네트워크를 금지하는 CSP와 nonce를 적용하고 사용자·모델 콘텐츠를 textContent로 표시한다. 권한 선택은 대기 중인 요청의 optionId만 허용하며 취소·종료 시 미응답 요청을 취소한다.

## 참고

- [VS Code Webview API](https://code.visualstudio.com/api/extension-guides/webview)
