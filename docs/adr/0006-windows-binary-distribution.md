# ADR 0006: Windows 독립 실행 바이너리 배포

상태: 런타임 번들링은 유지. ZIP 배포는 ADR 0007의 설치 프로그램으로 대체했다.

## 배경

사용자는 npm 패키지가 아닌 런타임 설치가 필요 없는 Windows 실행 패키지를 요구한다.

## 결정

- Bun compile로 CLI와 TUI, JavaScript 런타임을 perdix.exe로 묶는다.
- PyInstaller의 onedir 형식으로 Python 서버와 런타임을 server/에 묶는다.
- 두 결과를 하나의 Windows x64 ZIP으로 배포한다. 전체 압축을 풀고 perdix.exe를 실행한다.
- 명시적 서버 옵션·환경변수 다음으로 실행 파일 옆 server/perdix-server.exe를 탐색한다.
- Python 에이전트와 ACP 경계는 유지한다. 설치 시 다운로드나 별도 런타임은 필요하지 않다.

## 검증

개발 도구를 PATH에서 제외하고 저장소 밖에서 버전·서버 연결·fake 공급자 왕복을 검증한다.
기존 서버의 Windows 셸·프로세스 취소 호환성 문제는 바이너리 패키징과 별개로 남는다.
