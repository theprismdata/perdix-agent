# Windows 설치 파일 빌드

Windows x64용 Perdix 설치 프로그램을 만드는 방법이다. CLI와 Python 서버를 함께 묶으므로, 설치 파일을 받는 사용자는 Node.js나 Python을 따로 설치하지 않아도 된다.

## 빌드 PC 준비

다음 도구를 설치하고 새 터미널을 연다. 빌드 중 의존성을 내려받기 위한 인터넷 연결이 필요하다.

| 도구 | 조건 / 확인한 버전 |
|---|---|
| Windows | 64비트 |
| Node.js | 22 이상, 검증 버전 22.22.1 |
| pnpm | 저장소 지정 버전 10.30.3 |
| Bun | 검증 버전 1.4.2 |
| uv | 검증 버전 0.12.13 |
| Inno Setup | 6, 검증 버전 6.7.1 |

Node.js, pnpm, Bun, uv는 PATH에 있어야 한다. Inno Setup은 기본 설치 경로나 PATH에서 찾으며, 다른 위치는 `-InnoCompiler`로 지정한다. Python 의존성은 uv가 잠금 파일에 맞춰 준비한다.

## 실행

가장 간단한 방법은 저장소 루트의 `build-windows.cmd`를 더블클릭하는 것이다. 종료 후 결과를 읽을 수 있도록 창이 열린 채로 유지된다.

터미널에서는 저장소 폴더에서 실행한다.

```powershell
pnpm build:windows
```

빌드는 다음 순서로 진행된다.

1. 필요한 도구 확인 및 잠금 파일에 따른 의존성 설치
2. Bun으로 CLI 실행 파일 생성
3. VS Code Webview 확장 빌드 및 VSIX 생성
4. PyInstaller로 Python 서버와 의존성 묶기
5. Inno Setup으로 설치 프로그램 생성
6. CLI 버전 실행 확인, 설치 파일 SHA256 출력, 임시 빌드 폴더 정리

결과는 `dist/Perdix-Setup-windows-x64.exe`이며, 다음 빌드에서 같은 이름으로 갱신된다. 다른 PC에 전달할 파일도 이 설치 프로그램이다. `dist/`는 Git에 올리지 않으므로 저장소를 받은 뒤 직접 빌드해야 한다.

## 옵션

| 명령 | 용도 |
|---|---|
| `pnpm build:windows -CheckOnly` | 도구 준비 상태만 확인 |
| `pnpm build:windows -TestInstaller` | 빌드 후 설치 동작까지 검증 |
| `pnpm build:windows -KeepBuildFiles` | 중간 실행 파일과 빌드 자료 보관 |
| `pnpm build:windows -InnoCompiler "C:\Tools\Inno Setup 6\ISCC.exe"` | 설치 컴파일러 위치 지정 |

옵션은 함께 사용할 수 있다. PowerShell에서 직접 실행하려면 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1` 뒤에 같은 옵션을 붙인다.

## 설치와 키 설정

설치 프로그램을 실행하고 공급자와 API 키를 입력한다. 프로그램, 기본 모델, 바탕화면 및 시작 메뉴 바로가기를 설치 과정에서 설정한다. API 키 자체는 공급자에게 발급받아야 하며, Perdix가 유효한 키를 자동 발급하지는 않는다.

VS Code가 설치되어 있으면 Perdix Webview 확장도 자동 설치한다. 설치가 끝난 뒤 열려 있던 VS Code에서 **Developer: Reload Window**를 한 번 실행하면 사이드바에 Perdix가 나타난다. VS Code가 없으면 이 단계만 건너뛰며 나중에 VSIX를 직접 설치할 수 있다.

- 프로그램: `%LOCALAPPDATA%\Programs\Perdix`
- 키: `%USERPROFILE%\.perdix\credentials.json`
- 기본 모델: `%USERPROFILE%\.perdix\preferences.json`

키 파일의 기본 구조는 다음과 같다. 실제 키는 사용할 공급자의 빈 문자열에 넣는다.

```json
{
  "anthropic": { "api_key": "" },
  "openai": { "api_key": "" }
}
```

첫 설치에는 선택한 공급자의 키가 필요하다. 재설치에서 키 입력을 비우면 해당 공급자의 기존 키를 유지한다. 프로그램을 제거해도 사용자 키와 세션 기록은 남는다. CLI를 직접 실행할 때도 키 파일이 없으면 빈 기본 구조를 생성하며 기존 파일은 덮어쓰지 않는다.

## 임시 파일과 오류 확인

각 빌드는 `dist/.windows-build-<고유값>/`에서 작업한다. 성공하면 해당 폴더를 휴지통으로 이동한다. 휴지통 기능을 사용할 수 없으면 경고와 경로를 표시하고 폴더를 남기며, 완성된 설치 파일은 사용할 수 있다. 빌드 실패 시에는 진단 자료를 보존한다.

`-TestInstaller`는 별도 임시 폴더에서 시험용 키로 설치하므로 실제 사용자의 키와 바로가기를 변경하지 않는다. 시험 설치 폴더와 로그는 시스템 임시 폴더의 `perdix installer <고유값>`에 남는다. 검증에 실패하면 시험 설치 파일도 `dist/Perdix-Setup-test.exe`에 남을 수 있다.

## 검증 기록 (2026-10-11)

- 더블클릭 실행 경로의 도구 확인 및 `pnpm build:windows` 전체 빌드 성공
- 생성된 CLI의 버전 실행, SHA256 출력, 중간 빌드 폴더 정리 확인
- 설치 검증 통과: 빈 키 설치 차단, 키와 기본 모델 저장, 바로가기 생성, 재설치 시 키 유지, 로그에 키 미노출, 제거 시 사용자 설정 보존
- TypeScript 린트·타입 검사 및 Python Ruff 통과
- TypeScript 테스트: CLI 125개 통과 / 4개 실패, protocol 2개 및 vscode 1개 통과
- Python 테스트: 235개 통과 / 25개 실패, mypy 오류 4개

남은 실패는 Windows에서의 기존 Unix 명령·권한·경로·프로세스 처리 호환성 문제다. 특히 Python 타입 오류는 `os.killpg`와 `signal.SIGKILL` 사용에 해당한다. 설치 검증 통과가 모든 에이전트 기능의 Windows 호환성을 의미하지는 않는다.

### 실행 직후 창이 닫히는 문제 수정

Windows PowerShell 5.1에서 Bun에 전달하는 `NODE_ENV` 정의의 큰따옴표가 사라져, 실제 TUI 실행 시 `production is not defined` 오류가 발생했다. `--version`은 화면 모듈을 불러오지 않아 이 오류를 발견하지 못했다.

빌드 옵션의 문자열을 작은따옴표로 감싸도록 수정했다. 같은 옵션으로 작은 검증 코드를 먼저 번들링하고 실행하여 `NODE_ENV`가 문자열 `production`, `DEV`가 불리언 `false`인지 확인한다. 잘못된 정의는 설치 파일 생성 전에 빌드를 중단시킨다. 수정한 바이너리는 실제 터미널에서 시작 화면과 입력창 표시, 정상 종료까지 확인했다.
