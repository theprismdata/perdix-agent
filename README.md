# Perdix Agent

![Perdix CLI — 터미널 대화 화면](docs/images/perdix-cli.png)

터미널(TUI)과 VS Code에서 동작하는 코딩 에이전트.
Python 에이전트 코어 하나를 TypeScript 클라이언트가 [ACP](https://agentclientprotocol.com)로 구동한다.

- 설계: [docs/architecture.md](docs/architecture.md)
- 하네스 루프: [docs/harness-loop.md](docs/harness-loop.md)
- 프로토콜: [docs/protocol.md](docs/protocol.md)
- 결정 기록: [docs/adr/](docs/adr/)
- 에이전트 작업 지침: [AGENTS.md](AGENTS.md)

현재 상태: Python 코어(이벤트 로그, 하네스 루프, Anthropic 어댑터, 내장 도구, 권한, 샌드박스)와 헤드리스 ACP stdio 서버(M1), 터미널 UI `perdix`(M2)가 동작한다. 구현 순서는 architecture.md 9절, 단계별 스펙은 docs/impl/ 참조.

## 사용법

### VS Code 채팅 화면

`perdix-vscode-0.0.1.vsix`를 **Install from VSIX...**로 설치한 뒤, 사이드바의 Perdix 아이콘을 누른다. 터미널이 아닌 Webview에서 대화·모델 선택·작업 승인·이전 대화 불러오기를 제공한다. Windows 설치 프로그램의 서버와 키 설정을 자동으로 사용한다. 기존 확장을 업데이트했으면 VS Code 창을 다시 로드한다. 자세한 사용법은 [확장 안내](packages/vscode/README.md)를 참고한다.

### Windows 설치 프로그램 (권장)

자세한 준비 사항, 빌드 옵션, 키 설정 및 검증 결과는 [Windows 빌드 안내](docs/windows-build.md)를 참고한다.

`dist/Perdix-Setup-windows-x64.exe`를 실행하고 공급자와 API 키를 입력하면 설치가 끝난다.
프로그램, 키 파일, 기본 모델, 바탕화면·시작 메뉴 바로가기를 자동으로 설정한다.
VS Code가 설치되어 있으면 Perdix Webview 확장도 함께 자동 설치한다.
키 입력을 비우면 해당 공급자의 기존 저장 키를 사용한다. 첫 설치에는 키가 필요하다.
설정은 사용자 홈의 `.perdix/credentials.json`과 `.perdix/preferences.json`에 저장한다.
제거 시 키와 세션 기록은 남긴다. 런타임 설치나 JSON 직접 편집은 필요 없다.

Windows x64 개발 환경에서 Node.js 22 이상, pnpm 10.30.3, Bun, uv, Inno Setup 6을 준비한다.
루트의 `build-windows.cmd`를 더블클릭하거나 다음 명령 하나로 빌드한다.

```powershell
pnpm build:windows
```

의존성 설치, CLI·서버 빌드, 설치 파일 생성, 실행 확인을 자동으로 수행한다.
빌드 결과는 `dist/Perdix-Setup-windows-x64.exe`다. 성공하면 중간 빌드 폴더는 휴지통으로 이동한다. 휴지통 기능을 지원하지 않는 환경에서는 경고와 함께 폴더를 남긴다. 빌드 실패 시에도 진단을 위해 남긴다.

- `pnpm build:windows -CheckOnly`: 필요한 빌드 도구만 확인한다.
- `pnpm build:windows -TestInstaller`: 별도 폴더에서 설치·키 설정·바로가기·재설치·제거까지 검증한다. 실제 사용자 설정은 건드리지 않는다.
- `pnpm build:windows -KeepBuildFiles`: 중간 빌드 폴더를 보관한다.
- `pnpm build:windows -InnoCompiler "C:\경로\ISCC.exe"`: Inno Setup 컴파일러 위치를 지정한다.

현재 서버의 Unix 셸 명령·프로세스 그룹 취소 처리에는 Windows 호환성 제한이 있다.

### 1. API 키 준비

처음 CLI를 실행하면 `~/.perdix/credentials.json`을 자동 생성하고 경로를 안내한다.
`anthropic`과 `openai`의 `api_key` 기본값은 빈 문자열이다. 사용할 공급자의 키를 넣으면 된다.
기존 파일은 수정하거나 덮어쓰지 않는다. `--help`, `--version`은 파일을 생성하지 않는다.

둘 중 하나에 Anthropic API 키를 넣는다.

- 환경변수: `export ANTHROPIC_API_KEY=sk-ant-...`
- 파일: `~/.perdix/credentials.json` (권한 `chmod 600`)

```json
{"anthropic": {"api_key": "sk-ant-..."}}
```

### 2. 실행

프로젝트 폴더로 이동한 뒤 `perdix`를 실행한다.

```bash
cd 내-프로젝트
perdix
```

자주 쓰는 옵션:

| 옵션 | 설명 |
|---|---|
| `--mode <chat\|plan\|build>` | 작업 모드 (기본값 `chat`) |
| `--model <이름>` | 사용할 모델 (예: `claude-sonnet-5-5`) |
| `--cwd <폴더>` | 작업 폴더 지정 |
| `--resume <세션ID>` | 이전 대화 이어하기 |
| `--list` | 저장된 세션 목록 보기 |
| `-p "질문"` | 한 번만 묻고 끝내기 (비대화식) |
| `--json` | `-p`와 함께 쓰면 결과를 JSON 줄로 출력 |
| `-h`, `-v` | 도움말 / 버전 |

```bash
perdix --mode plan --model claude-sonnet-5-5
perdix -p "이 프로젝트 구조를 설명해줘"
```

### 3. 대화 중 명령

입력창에 `/`로 시작하는 명령을 입력한다. `/`만 쳐도 쓸 수 있는 명령 목록이 팝업으로 뜨고,
계속 타이핑하면 후보가 좁혀진다. ↑/↓로 선택을 옮기고 Tab이나 Enter로 확정한다. `/agent `,
`/model ` 뒤에서는 실제 값(`chat`/`plan`/`build`, 모델 목록)도 같은 방식으로 추천된다.

| 명령 | 설명 |
|---|---|
| `/agent <id>` | 에이전트 모드 바꾸기 (`chat`/`plan`/`build`) — 서브에이전트(아래 6번)와는 다른 개념이다 |
| `/model <이름>` | 모델 바꾸기 |
| `/sessions` | 저장된 세션 목록 보기 |
| `/clear` | 화면 지우기 |
| `/help` | 도움말 보기 |
| `/quit` (`/exit`) | 종료 (Ctrl+C 두 번도 가능) |

`/`로 시작하지 않는 글은 모두 에이전트에게 보내는 메시지다.

### 4. 서버 위치 지정 (보통은 필요 없음)

`perdix`는 아래 순서로 서버 실행 명령을 찾는다.

1. `--server <명령>` 옵션
2. 환경변수 `PERDIX_SERVER`
3. 실행 파일 옆의 `server/perdix-server.exe`
4. PATH에 있는 `perdix-server`
5. CLI 옆에 Python 서버 소스가 있으면 uv로 실행하는 호환 경로
6. 저장소 안에서 개발 중이면 `uv run perdix-server`

찾지 못하면 "perdix-server not found" 메시지가 나온다. 이때는 `uv tool install perdix`로 설치하거나 `PERDIX_SERVER`를 설정한다.

### 5. 서버만 따로 띄우기 (ACP 클라이언트용)

Zed 같은 ACP 클라이언트에 붙일 때는 서버만 실행한다.

```bash
(cd server && uv run perdix-server --model claude-opus-5-5)
```

API 키 없이 스크립트된 응답으로 테스트하고 싶으면 fake 공급자를 쓴다.

```bash
(cd server && uv run perdix-server --provider fake --fake-script tests/fixtures/demo.json --db /tmp/perdix.db)
```

### 6. 역할별 모델 지정 (서브에이전트)

`task` 도구로 띄우는 서브에이전트(`explore`/`executor`/`reviewer`/`debugger`/`security-reviewer`, 내부 전용 `compactor`/`titler`)는 역할마다
기본 모델이 다르다. 기본값을 바꾸려면 JSON 설정 파일을 만들어 `perdix-server`에 넘긴다.

**기본값** (설정을 안 주면 이대로 동작한다)

| 역할 | 기본 모델 | effort |
|---|---|---|
| `explore` | `claude-haiku-4-5` | (없음) |
| `executor` | `claude-opus-5-5` | `high` |
| `reviewer` | `claude-sonnet-5-5` | `medium` |
| `debugger` | `claude-sonnet-5-5` | `medium` |
| `security-reviewer` | `claude-sonnet-5-5` | `medium` |
| `compactor` | `claude-haiku-4-5` | (없음) |
| `titler` | `claude-haiku-4-5` | (없음) |

이 표는 **서브에이전트만** 적용 대상이다. 메인 대화(지금 응답하고 있는 에이전트)의 모델은
위에서 설명한 `--model`/`/model`로 따로 바꾼다.

**설정 파일 예시** ([`agents.example.json`](agents.example.json))

```json
{
  "models": {
    "strong": { "provider": "anthropic", "model": "claude-opus-5-5" },
    "cheap":  { "provider": "openai",    "model": "gpt-6-luna" }
  },
  "agents": {
    "executor": { "model": "strong", "effort": "high" },
    "explore":  { "model": "cheap" }
  }
}
```

- `models`: 모델 별칭 테이블. `agents`에서 별칭 대신 실제 모델 id를 바로 써도 된다.
- `agents`: 역할 id → `{ "model": ..., "effort": ... }`. 역할을 적으면 모델과 effort를
  **둘 다 같이** 교체한다(`effort`를 안 쓰면 "effort 없음"이 된다 — 위 기본값의 effort가
  그대로 유지되는 게 아니다).
- 적지 않은 역할은 위 기본값 표 그대로 동작한다.

**실행 방법**

서버를 직접 띄울 때:

```bash
(cd server && uv run perdix-server --agents-config /path/to/agents.json)
```

`perdix` CLI로 실행할 때는 `--server`로 서버 실행 명령 자체를 바꿔서 전달한다:

```bash
perdix --server "uv run --project server perdix-server --agents-config /path/to/agents.json"
```

**우선순위**: 모델이 겹쳐서 정해지면 `task` 도구 호출의 `model` 입력(모델 호출 시 그 자리에서
준 값) > 이 설정 파일의 오버라이드 > 위 기본값 표, 순서로 이긴다.

## 개발

### 처음 한 번 설치

```bash
scripts/bootstrap.sh          # pnpm install + (cd server && uv sync)
```

### 개발 중 실행

Windows PowerShell에서는 저장소 루트에서 의존성을 설치하고 빌드한다(Node.js 22 이상, pnpm, uv 필요).

```powershell
pnpm install --frozen-lockfile
uv sync --locked --project server
pnpm -r build
node packages/cli/dist/main.js --help
node packages/cli/dist/main.js
```

API 키를 환경변수로 지정할 때는 `$env:ANTHROPIC_API_KEY = '키'` 형식을 사용한다.

```bash
(cd packages/cli && pnpm build) && node packages/cli/dist/main.js   # 빌드한 CLI 실행
(cd server && uv run python scripts/chat.py)                       # TUI 없이 텍스트로 대화
```

### 테스트와 빌드

```bash
pnpm -r build && pnpm -r test # TS
(cd server && uv run pytest)  # Python
```

```bash
scripts/bootstrap.sh          # pnpm install + (cd server && uv sync)
pnpm -r build && pnpm -r test # TS
(cd server && uv run pytest)  # Python
```

uv 없이 pip만 쓰는 경우(서버):

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 런타임
pip install -r requirements-dev.txt -e . # 개발(pytest, ruff, mypy 포함)
```

`requirements*.txt`는 `uv.lock`에서 생성한 고정 버전 목록이다. 의존성을 바꾸면 `uv add/remove` 후 아래로 다시 생성한다.

```bash
cd server
uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o requirements.txt
uv export --format requirements-txt --no-hashes --no-emit-project -o requirements-dev.txt
```
