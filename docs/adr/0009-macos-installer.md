# ADR 0009: macOS 독립 설치 앱

- 상태: 채택 (2026-10-11)

Windows Inno Setup 배포에 대응하는 macOS DMG를 제공한다. 설치 앱은 Swift/AppKit으로
공급자·API 키·모델을 입력받고 사용자 홈에 설치한다. Bun으로 만든 CLI와 PyInstaller
서버를 동봉하여 사용자가 Node.js·Python·uv를 준비할 필요가 없다.

설치는 `~/Library/Application Support/Perdix/versions/<UUID>`에 새 파일을 복사하고
`~/.local/bin`의 CLI·서버 링크를 교체한다. 기존 키·세션과 이전 설치는 삭제하지 않는다.
CLI는 링크의 실제 실행 파일 옆에서 서버를 찾는다. 확장은 macOS에서 사용자 서버
링크를 탐색한다. 설치 앱은 에이전트 실행 로직을 포함하지 않는다.

키는 기존 credentials.json, 기본 모델은 preferences.json으로 저장한다. 개인 키는
배포본에 포함하지 않고 키 파일은 0600으로 저장한다. 설치 앱의 자동 테스트는 별도
설치·설정 경로를 사용하며 사용자 PATH·VS Code·실제 키를 변경하지 않는다.

Python 바이너리는 교차 빌드하지 않는다. Apple Silicon은 arm64, Intel은 x64 환경에서
각각 빌드하고 DMG 이름에 구분을 표시한다. 개발자 인증서 없는 빌드는 서명·공증된
공개 배포본으로 주장하지 않는다.
