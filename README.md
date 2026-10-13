# Perdix Agent

![Perdix CLI — 터미널 대화 화면](docs/images/perdix-cli.png)

터미널(TUI)과 VS Code에서 동작하는 코딩 에이전트.
Python 에이전트 코어 하나를 TypeScript 클라이언트가 [ACP](https://agentclientprotocol.com)로 구동한다.

- 설계: [docs/architecture.md](docs/architecture.md)
- 하네스 루프: [docs/harness-loop.md](docs/harness-loop.md)
- 슬래시 목표 루프: [docs/goal-loop.md](docs/goal-loop.md)
- OpenAI API 키 / ChatGPT 로그인: [docs/openai-auth.md](docs/openai-auth.md)
- 프로토콜: [docs/protocol.md](docs/protocol.md)
- 결정 기록: [docs/adr/](docs/adr/)
- 에이전트 작업 지침: [AGENTS.md](AGENTS.md)

현재 상태: Python 코어(이벤트 로그, 하네스 루프, Anthropic 어댑터, 내장 도구, 권한, 샌드박스)와 헤드리스 ACP stdio 서버(M1), 터미널 UI `perdix`(M2)가 동작한다. 구현 순서는 architecture.md 9절, 단계별 스펙은 docs/impl/ 참조.

## 사용법

`websearch`는 별도 키 없이 웹 검색 결과의 제목·URL·요약을 반환하고,
`webfetch`는 공개 페이지의 HTML 본문·링크 또는 텍스트를 읽는다. 모델이 일반 도구로
선택하며 chat/plan/build와 읽기 전용 하위 에이전트에도 제공한다.
기본 검색은 Bing RSS이며 검색 엔진의 차단·형식 변경은 도구 오류로 표시한다.
JavaScript 렌더링·로그인·PDF는 지원하지 않는다. 내부 주소·비표준 포트는 차단하며
30초·1MB 제한을 적용한다. `deny` 권한 모드에서는 웹 접근도 거부한다.

프로젝트 규칙은 매 모델 호출마다 자동으로 읽는다. 전역 `~/.claude/CLAUDE.md`,
상위 폴더부터 작업 폴더까지의 `AGENTS.md`, `CLAUDE.md`, `.claude/CLAUDE.md`,
`CLAUDE.local.md`를 합치며 가까운 폴더와 나중 파일을 우선한다. 메모리 참조를
꺼도 규칙 로딩은 유지된다. 메인·하위 에이전트 모두 같은 로더를 사용한다.
`CLAUDE.md`의 독립된 `@문서.md` 줄은 프로젝트 안의 Markdown을 임포트한다.
임포트 순환·중복·크기를 제한하며 외부 경로와 코드 블록 안의 임포트는 제외한다.
하위 폴더 규칙은 파일 도구의 `path`로 발견하고 다음 모델 호출에 적용한다.
새 규칙을 보지 못한 상태의 쓰기는 확인 요청 결과를 반환하고 재시도한다.
셸 명령 문자열의 경로는 추론하지 않으므로 하위 파일은 먼저 파일 도구로 조회해야 한다.
규칙은 프롬프트 지침이며 읽기 전용 모드·신뢰 게이트·실행 권한을 완화하지 않는다.

여러 단계가 필요한 요청에서는 작업 시작 설명, 주요 발견과 검증 결과를 대화 중에
짧게 전달하도록 에이전트에 지시한다. 진행 설명은 일반 답변처럼 즉시 스트리밍되며,
CLI와 VS Code 채팅에서 도구 실행 상태와 함께 볼 수 있다. 설명의 빈도는 모델에
따라 달라지며, 고정 시간 알림이나 완료율 표시는 제공하지 않는다.

CLI 입력창 위에서는 모델 응답 대기·도구 실행·승인 대기와 경과 시간을 볼 수 있다.
30초간 새 이벤트가 없으면 수신 대기 안내가 표시된다. 요청 종료 시에는 대화에
완료·취소·검증 실패 안내를 남기며, 최종 답변이나 도구 결과가 없으면 미완료를 알린다.
`chat`과 `plan`은 읽기 전용이다. Git 동기화 등 변경 작업은 `/agent build`로 전환한다.

### VS Code 채팅 화면

`perdix-vscode-0.0.1.vsix`를 **Install from VSIX...**로 설치한 뒤, 사이드바의 Perdix 아이콘을 누른다. 터미널이 아닌 Webview에서 대화·모델 선택·작업 승인·이전 대화 불러오기를 제공한다. Windows 설치 프로그램의 서버와 키 설정을 자동으로 사용한다. 기존 확장을 업데이트했으면 VS Code 창을 다시 로드한다. 자세한 사용법은 [확장 안내](packages/vscode/README.md)를 참고한다.

### macOS 설치 프로그램

`Perdix-Setup-macos-arm64.dmg`를 열고 **Perdix Setup.app**을 실행한다. 공급자·API 키·모델을 설정하면 CLI·서버·VS Code 확장을 함께 설치한다. 새 터미널에서 `perdix`를 실행한다. Apple Silicon용이며 Intel Mac에서는 해당 환경에서 `pnpm build:macos`로 x64용을 빌드한다. 자세한 설치·빌드 방법은 [Mac 설치 안내](docs/macos-installation.md)를 참고한다.

### Windows 설치 프로그램 (권장)

자세한 준비 사항, 빌드 옵션, 키 설정 및 검증 결과는 [Windows 빌드 안내](docs/windows-build.md)를 참고한다.

`dist/Perdix-Setup-windows-x64.exe`를 실행하고 공급자와 API 키를 입력하면 설치가 끝난다.
프로그램, 키 파일, 기본 모델, 바탕화면·시작 메뉴 바로가기를 자동으로 설정한다.
VS Code가 설치되어 있으면 Perdix Webview 확장도 함께 자동 설치한다.
키 입력을 비우면 해당 공급자의 기존 저장 키를 사용한다. 첫 설치에는 키가 필요하다.
API 키는 사용자 홈의 `.perdix/credentials.json`에 저장한다. 모델은
`.perdix/agents.json`의 역할별 할당을 사용하며 `--model`로 명시적으로 변경할 수 있다.
제거 시 키와 세션 기록은 남긴다. 런타임 설치나 JSON 직접 편집은 필요 없다.

Windows x64 개발 환경에서 Node.js 22 이상, pnpm 10.30.3, Bun, uv, Inno Setup 6을 준비한다.
루트의 `build-windows.cmd`를 더블클릭하거나 다음 명령 하나로 빌드한다.

```powershell
pnpm build:windows
```

의존성 설치, CLI·서버 빌드, 설치 파일 생성, 실행 확인을 자동으로 수행한다.
빌드 결과는 `dist/Perdix-Setup-windows-x64.exe`다. 성공하면 중간 빌드 폴더는 휴지통으로 이동한다. 휴지통 기능을 지원하지 않는 환경에서는 경고와 함께 폴더를 남긴다. 빌드 실패 시에도 진단을 위해 남긴다.

- `pnpm build:windows -CheckOnly`: 필요한 빌드 도구만 확인한다.
- `pnpm build:windows -KeepBuildFiles`: 중간 빌드 폴더를 보관한다.
- `pnpm build:windows -InnoCompiler "C:\경로\ISCC.exe"`: Inno Setup 컴파일러 위치를 지정한다.

현재 서버의 Unix 셸 명령·프로세스 그룹 취소 처리에는 Windows 호환성 제한이 있다.

### 1. API 키 준비

처음 CLI를 실행하면 `~/.perdix/credentials.json`을 자동 생성하고 경로를 안내한다.
`anthropic`과 `openai`의 `api_key` 기본값은 빈 문자열이다. 사용할 공급자의 키를 넣으면 된다.
기존 파일은 수정하거나 덮어쓰지 않는다. `--help`, `--version`은 파일을 생성하지 않는다.

OpenAI는 키가 없어도 사용할 수 있다. OpenAI 모델의 첫 요청에서 브라우저로
ChatGPT 로그인을 진행하고, 이후 저장된 토큰을 자동 갱신한다.
`perdix auth login --provider openai`로 먼저 로그인할 수도 있다.
환경변수 또는 파일에 API 키가 있으면 키가 우선한다. [인증 안내](docs/openai-auth.md)

둘 중 하나에 Anthropic API 키를 넣는다.

- 환경변수: `export ANTHROPIC_API_KEY=sk-ant-...`
- 파일: `~/.perdix/credentials.json` (권한 `chmod 600`)

```json
{"anthropic": {"api_key": "sk-ant-..."}}
```

Gemini는 `GEMINI_API_KEY` 또는 같은 파일의 `gemini.api_key`를 사용한다.
`perdix auth login --provider gemini --api-key <key>`로 등록하거나 Hermes에서
`/login gemini`를 실행하고 숨겨진 입력창에 키를 붙여 넣는다.
키 발급: https://aistudio.google.com/apikey
`/model gemini-3.8-flash`로 선택할 수 있으며 인증 실패 시 다른 공급자로 우회하지 않는다.
Gemini 네이티브 어댑터는 텍스트·이미지·도구 호출·도구 결과·토큰 사용량을 처리하고
동일 모델의 thoughtSignature를 보존한다([ADR 0018](docs/adr/0018-gemini-provider.md)).

OpenRouter는 `OPENROUTER_API_KEY` 또는 `credentials.json`의
`{"openrouter": {"api_key": "..."}}`로 인증한다. 키 발급은
https://openrouter.ai/settings/keys 에서 한다. Hermes의 `/login openrouter`는
키를 숨겨서 입력받으며, CLI에서는
`perdix auth login --provider openrouter --api-key <key>`로 등록한다.
`perdix auth status --provider openrouter`로 활성 키의 출처를 확인한다.
키가 없거나 인증·잔액 오류가 발생하면 다른 공급자로 우회하지 않는다.

`/model qwen/qwen3-coder-flash`, `/model openai/gpt-6.1-sol`,
`/model anthropic/claude-sonnet-5.5`, `/model google/gemini-3.8-flash`는
OpenRouter 경유 연결이다. 공급자 접두사 없는 기존 모델 ID는 직접 연결이다.
프롬프트·도구·권한은 기존 것을 사용하며 공급자 어댑터가 와이어 형식을 변환한다.
목록 밖 OpenRouter 모델도 `agents.json`의 `models`에 `provider: "openrouter"`로
등록하고 역할에 할당할 수 있다([ADR 0019](docs/adr/0019-openrouter-provider.md)).

저장소의 [`agents.openrouter.example.json`](agents.openrouter.example.json)을 사용자 설정
폴더의 `~/.perdix/agents.openrouter.json`에 복사하면 OpenRouter 전용 경제형 설정을
사용할 수 있다. 대화·코딩·탐색은 Qwen Coder Flash, 검토·계획은 Claude Sonnet,
디버깅은 GPT Sol을 사용한다. `perdix --agents-config ~/.perdix/agents.openrouter.json`으로
시작하면 모드 전환·하위 에이전트·인증 후 재연결에도 이 설정을 사용한다. 또는
기존 `agents.json`을 백업하고 내용으로 적용한 뒤 서버를 재시작한다.
카탈로그 비용은 2026-10-13 기본 단가에 따른 추정이며 긴 입력·캐시·라우팅·키 충전
수수료에 따라 실제 청구액은 달라질 수 있다.

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
| `--tui hermes` | Hermes TUI 평가판 ([사용 안내](docs/hermes-tui.md)) |
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
| `/memories` | 자동 메모리 상태 조회 (`use on/off`, `generate on/off`로 세션별 제어) |
| `/clear` | 화면 지우기 |
| `/help` | 도움말 보기 |
| `/quit` (`/exit`) | 종료 (Ctrl+C 두 번도 가능) |

`/`로 시작하지 않는 글은 모두 에이전트에게 보내는 메시지다.

유휴 대화에서 메모리를 자동 추출·통합해 다음 세션에 반영한다. 기본 유휴 기준은
6시간이며 자세한 설정과 저장 경로는 [메모리 안내](docs/memory.md)를 참고한다.

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

### 6. 역할별 모델 지정

`task` 도구로 띄우는 서브에이전트(`explore`/`executor`/`reviewer`/`debugger`/`security-reviewer`, 내부 전용 `compactor`/`titler`)는 역할마다
기본 모델이 다르다. 메인 역할 `chat`, `plan`, `build`도 같은 JSON 설정으로 할당한다.
서버는 사용자 설정 폴더의 `~/.perdix/agents.json`을 자동으로 읽으며 `--agents-config`가 우선한다.

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

이 표는 설정 파일이 없을 때의 서브에이전트 기본값이다. 사용자 설정 파일
`~/.perdix/agents.json`에는 다음처럼 할당할 수 있다.

| 역할 | 모델 |
|---|---|
| `chat` | `gpt-6-luna` |
| `build`, `executor` | `gpt-6.1-sol` |
| `debugger` | `gpt-6-astra` |
| `plan`, `security-reviewer` | `claude-opus-5-5` |
| `reviewer` | `claude-sonnet-5-5` |
| `explore`, `compactor`, `titler` | `gemini-3.8-flash` |

자동·수동 모드 전환 시 해당 역할의 모델과 effort가 적용된다. 자동 전환에서는
현재 Turn의 다음 모델 호출부터 바뀌며 화면에도 모델 변경이 전달된다.
새 세션은 시작 역할의 모델을 사용하지만 CLI `--model` 등 명시적 세션 초기 모델이 우선한다.
서버 `--model`은 역할 할당이 없을 때의 기본값이다.
`/model`은 현재 역할의 모델을 바꾸고, 다음 모드 전환에서 사전 할당이 다시 적용된다.
파일 변경은 서버 재시작 후 적용되며 모든 프로젝트에 같은 할당을 사용한다.
다른 파일을 사용하려면 `--agents-config`에 절대 경로를 지정한다.

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

### 빌드와 정적 검사

```bash
pnpm -r build && pnpm -r lint && pnpm -r typecheck
(cd server && uv run ruff check . && uv run mypy src)
```

```bash
scripts/bootstrap.sh          # pnpm install + (cd server && uv sync)
pnpm -r build && pnpm -r lint && pnpm -r typecheck
(cd server && uv run ruff check . && uv run mypy src)
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

### OpenRouter 모델 메뉴

Hermes TUI에서 `/assign`을 실행하면 OpenRouter 목록을 조회한 뒤 에이전트를
고르고 모델 ID를 입력 검색해 Enter로 할당할 수 있다. Esc로 이전 단계·취소한다.
목록은 텍스트 출력과 도구 호출을 지원하는 모델에 한정한다. 저장은 서버가
`~/.perdix/agents.json` 또는 `--agents-config` 지정 파일에 수행한다. 다른 역할과
기존 effort는 보존하고 모델의 한도·가격 메타데이터도 저장한다. 현재 역할이면
현재 세션에도 적용되며 다른 역할은 다음 실행부터 사용한다. 조회·저장은 실행 중
거절되며 목록 오류 시 설정을 바꾸지 않는다. 실제 호출에는 OpenRouter 키가 필요하다.

### 자동 컨텍스트 압축

모델 호출 전 입력 추정치와 출력 예약량이 컨텍스트 창의 75%에 도달하면 완료된
오래된 기록을 자동 요약한다. `~/.perdix/agents.json`의 `compactor` 모델·effort
할당을 사용하며, 현재 사용자 요청과 마지막 assistant·도구 결과는 보존한다.
원본 event log는 유지하고 `compaction` 이벤트를 추가해 다음 요청의 투영만 바꾼다.
추정치는 UTF-8 크기와 직전 공급자 사용량을 참고하므로 실제 토큰 수와 차이가 있다.
요약 호출은 45초·최대 8회로 제한하며 공용 실행 예산을 소비한다. 요약 실패 시
오래된 도구 결과를 줄이고, 그래도 한도를 넘으면 명확한 오류로 요청을 중단한다.
세부 정책은 [ADR 0022](docs/adr/0022-automatic-compaction.md)를 참고한다.

### Telegram · GitHub · 웹 대시보드

`perdix-gateway`로 상주 게이트웨이를 실행할 수 있다. 등록한 프로젝트의 작업을
웹·Telegram·GitHub 이슈에서 접수하고, 웹에서 실행 기록·결과·취소·권한 승인을
처리한다. 화면을 닫아도 작업은 계속 실행된다.

```bash
cd server
uv run perdix-gateway --config /absolute/path/to/gateway.json
```

환경 변수 `PERDIX_GATEWAY_TOKEN`과 프로젝트 설정이 필요하다.
[설정과 서비스 연결 방법](docs/gateway.md)을 참고한다.
