# Perdix for VS Code

터미널 대신 VS Code 사이드바에서 Perdix와 대화하는 Webview 확장입니다.

## 설치와 시작

1. Windows에서는 Perdix 설치 프로그램으로 서버와 API 키를 설정합니다.
2. VS Code 확장 메뉴의 **Install from VSIX...**에서 `perdix-vscode-0.0.1.vsix`를 설치합니다. 기존 확장 설치 시 **Developer: Reload Window**로 새 버전을 적용합니다.
3. 작업할 폴더를 열고 신뢰 여부를 확인합니다.
4. 사이드바의 **Perdix 아이콘**을 누르거나 명령 팔레트에서 **Perdix: Open Chat**을 실행합니다.
5. 메시지를 입력하면 서버에 연결하고 답변을 화면에 표시합니다.

오른쪽에 두려면 Perdix 뷰의 제목을 우클릭하고 **Move View → Secondary Side Bar**로 이동합니다. 다른 확장의 Codex 탭 안에 들어가는 것이 아니라 독립된 Perdix 뷰입니다.

## 기능

- 응답 스트리밍, 도구 실행 내용과 변경 사항, 작업 계획 표시
- 작업 중지, 모델·모드 변경, 작업 허용·거절
- 새 대화 및 이전 대화 불러오기
- 기존 API 키 설정 파일 열기, 서버 경로 설정

Windows 기본 서버 위치 `%LOCALAPPDATA%\Programs\Perdix\server\perdix-server.exe`를 자동 탐색합니다. 다른 환경은 `perdix.serverPath`에 서버 실행 파일의 전체 경로를 지정하거나 PATH에 `perdix-server`를 설치하세요. 인자는 경로에 넣지 않습니다. SSH/WSL에서는 원격 확장 호스트 쪽 서버와 키를 사용합니다.

API 키는 Webview로 전달하지 않습니다. 파일·명령 실행 및 권한 정책은 Python 서버에서 처리합니다. 신뢰하지 않은 작업 폴더에서는 서버를 시작하지 않습니다. 여러 폴더를 연 작업 공간은 첫 번째 폴더를 사용합니다.

대화 기록은 서버에 보관됩니다. 창을 다시 연 후에는 **기록**에서 이전 대화를 선택할 수 있습니다. 화면에는 응답을 안전한 일반 텍스트로 표시하며 HTML을 실행하지 않습니다.

## 개발과 검증

저장소 루트에서 의존성을 설치한 후 실행합니다.

```powershell
pnpm --filter @perdix/protocol build
pnpm --filter perdix-vscode build
pnpm --filter perdix-vscode test
pnpm --filter perdix-vscode test:webview
pnpm --filter perdix-vscode test:server
```

`test:webview`는 Windows의 Edge를 헤드리스로 실행해 화면과 입력·승인·취소를 검사합니다. 다른 운영체제에서는 Playwright Chromium 설치가 필요합니다. `test:server`는 설치된 서버를 테스트 공급자로 실행하고 별도 임시 DB를 사용하므로 실제 API 요청을 보내지 않습니다. 서버 경로를 마지막 인자로 지정할 수도 있습니다.

VSIX는 `packages/vscode`에서 `pnpm dlx @vscode/vsce package --no-dependencies --allow-missing-repository --out ../../dist/perdix-vscode-0.0.1.vsix`로 만듭니다. 실제 VS Code 호스트 검증은 `scripts/test-vscode-host.cjs`를 VS Code의 `--extensionTestsPath`에 지정하고 이 패키지를 `--extensionDevelopmentPath`에 지정해 실행합니다. 사용자 설정에 영향을 주지 않도록 별도 `--user-data-dir`과 `--extensions-dir`을 사용합니다.

2026-10-11 검증: 확장 테스트 10개, Webview 브라우저 검사, 실제 확장 호스트 활성화·뷰 열기, 설치된 Python 서버의 응답·대화 복원 검사 통과. 전체 저장소의 기존 Windows 문제(TS 테스트 4개, Python 테스트 25개, mypy 오류 4개)는 남아 있습니다. 린트와 TS 타입 검사, Python Ruff는 통과했습니다.
