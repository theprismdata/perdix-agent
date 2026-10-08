# Perdix Agent

터미널(TUI)과 VS Code에서 동작하는 코딩 에이전트.
Python 에이전트 코어 하나를 TypeScript 클라이언트가 [ACP](https://agentclientprotocol.com)로 구동한다.

- 설계: [docs/architecture.md](docs/architecture.md)
- 하네스 루프: [docs/harness-loop.md](docs/harness-loop.md)
- 프로토콜: [docs/protocol.md](docs/protocol.md)
- 결정 기록: [docs/adr/](docs/adr/)
- 에이전트 작업 지침: [AGENTS.md](AGENTS.md)

현재 상태: Python 코어(이벤트 로그, 하네스 루프, Anthropic 어댑터, 내장 도구, 권한, 샌드박스)와 헤드리스 ACP stdio 서버(M1), 터미널 UI `perdix`(M2)가 동작한다. 구현 순서는 architecture.md 9절, 단계별 스펙은 docs/impl/ 참조.

## 사용법

### 1. API 키 준비

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

입력창에 `/`로 시작하는 명령을 입력한다.

| 명령 | 설명 |
|---|---|
| `/mode <id>` | 모드 바꾸기 |
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
3. PATH에 있는 `perdix-server`
4. 저장소 안에서 개발 중이면 `uv run perdix-server`

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

`task` 도구로 띄우는 서브에이전트(`explore`/`executor`/`reviewer`/`compactor`/`titler`)는 역할마다
기본 모델이 다르다. 기본값을 바꾸려면 JSON 설정 파일을 만들어 `perdix-server`에 넘긴다.

**기본값** (설정을 안 주면 이대로 동작한다)

| 역할 | 기본 모델 | effort |
|---|---|---|
| `explore` | `claude-haiku-4-5` | (없음) |
| `executor` | `claude-opus-5-5` | `high` |
| `reviewer` | `claude-sonnet-5-5` | `medium` |
| `compactor` | `claude-haiku-4-5` | (없음) |
| `titler` | `claude-haiku-4-5` | (없음) |

이 표는 **서브에이전트만** 적용 대상이다. 메인 대화(지금 응답하고 있는 에이전트)의 모델은
위에서 설명한 `--model`/`/model`로 따로 바꾼다.

**설정 파일 예시** (`agents.json`)

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
