# Perdix Agent — 아키텍처 설계 (v0.1 초안)

> 상태: 설계 초안. 구현 전 합의용 문서.
> 관련 문서: [harness-loop.md](harness-loop.md), [protocol.md](protocol.md), [adr/](adr/)

## 1. 한 줄 요약

**Python 에이전트 코어(하네스 루프 + 도구 + LLM) 하나를, TypeScript 클라이언트(터미널 TUI, VS Code 확장)가 ACP(Agent Client Protocol)로 구동하는 코딩 에이전트.**
저장소 자체는 Claude Code와 Codex 양쪽으로 개발 가능해야 하며, 에이전트도 두 도구의 설정 규약(AGENTS.md, CLAUDE.md, skills, MCP)을 그대로 읽는다.

## 2. 확정된 결정 사항

| 항목 | 결정 | 근거 / 비고 |
|---|---|---|
| 코어 언어 | Python 3.12+ | 하네스 루프, LLM 호출, 도구 실행, 세션/권한 전부 Python |
| 클라이언트 언어 | TypeScript | CLI/TUI, VS Code 확장, 공유 프로토콜 타입 |
| 클라이언트↔코어 프로토콜 | **ACP (JSON-RPC 2.0, stdio)** | 표준 채택으로 Zed, Neovim 등 다른 에디터에도 그대로 연결. Python/TS SDK 존재 |
| VS Code UI | **사이드바 Webview 채팅** | 확장 호스트가 ACP 클라이언트로 Python 서버를 구동한다. 기존 터미널 전용 결정은 ADR 0008로 대체 |
| 하네스 루프 | 검증 포함 자율 루프 | tool-call 루프 + 계획→실행→검증→재시도 외부 루프 + 컨텍스트 압축 + 중단/재개 |
| LLM 공급자 | Anthropic, OpenAI(+호환), Gemini, 로컬(Ollama 등) | 공급자 추상화 뒤에 네이티브 어댑터 |
| 개발 도구 호환 | AGENTS.md(정본) + CLAUDE.md(임포트) | Codex와 Claude Code 모두 이 저장소를 읽고 작업 가능 |
| 설정 포맷 호환 | AGENTS.md / CLAUDE.md / skills / MCP 설정 읽기 | 사용자의 기존 프로젝트 규약을 그대로 활용 |
| TUI 프레임워크 | Ink (React for CLI) | OpenTUI는 Bun/Node 26 FFI 요구로 통합 터미널 배포에 부담. 교체 가능하도록 UI 계층 분리 |

## 3. 참조 아키텍처에서 가져오는 것

| 출처 | 차용하는 개념 | Perdix에서의 위치 |
|---|---|---|
| **OpenCode** | 클라이언트/서버 분리, 하나의 서버에 여러 프런트엔드, 도구별 allow/ask/deny 권한, agent 프리셋(build/plan), 플러그인 훅 | `server/` 전체 구조, `permissions/`, `harness/hooks.py` |
| **Hermes Agent** | 단일 에이전트 + 툴셋 조합, SQLite 세션 저장(FTS5 검색), 컨텍스트 압축, MEMORY.md 영속 메모리, 스킬 자동 추출, 서브에이전트 위임, 실행 백엔드 추상화(local/docker/ssh) | `tools/` 툴셋 구성, `session/store.py`, `context/compaction.py`, `memory/`, `subagents/`, `sandbox/` |
| **Kilo Code** | 모드(Code/Architect/Ask/Debug/Orchestrator), 체크포인트(섀도 git으로 파일 상태 스냅샷), 컨텍스트 condensing, MCP 허브, VS Code 에디터 연동 | `harness/modes.py`, `session/checkpoint.py`, `mcp/hub.py`, `packages/vscode` |
| **Claude Code 확장** | IDE 브리지 방식(확장이 로컬 서버를 띄우고 CLI가 환경변수/락파일로 발견) | `packages/vscode/src/bridge`, `packages/cli/src/ide` |

## 4. 전체 구조

```
┌──────────────────────────────────────────────────────────────────┐
│  Clients (TypeScript)                                            │
│                                                                  │
│  ┌──────────────┐   IDE bridge     ┌────────────────────────┐    │
│  │ VS Code 확장  │◄───(WS/JSON-RPC)─┤ perdix CLI (Ink TUI)    │    │
│  │ terminal 런처 │                  │  = ACP Client           │    │
│  │ diff/open/   │                  │                         │    │
│  │ selection    │                  └───────────┬────────────┘    │
│  └──────────────┘                              │ ACP (stdio)     │
└────────────────────────────────────────────────┼─────────────────┘
                                                 │
┌────────────────────────────────────────────────▼─────────────────┐
│  perdix-server (Python)  = ACP Agent                             │
│                                                                  │
│  acp/        ACP 어댑터: initialize, session/new|load|prompt|cancel│
│  harness/    루프 러너, 턴/스텝, 검증 게이트, 예산, 훅, 모드         │
│  context/    프롬프트 빌더, 지침 로더(AGENTS.md…), 스킬, 컴팩션      │
│  llm/        Provider 추상화 + anthropic/openai/gemini/compat     │
│  tools/      레지스트리 + 내장 도구(read/edit/bash/grep/…)          │
│  permissions/ 정책 평가(allow/ask/deny), 룰 매칭                   │
│  mcp/        MCP 클라이언트 허브(.mcp.json, codex config.toml)     │
│  subagents/  서브에이전트 스폰(explore/executor/reviewer)           │
│  session/    SQLite 저장, 체크포인트(섀도 git), 재개                │
│  memory/     MEMORY.md 영속 메모리                                 │
│  sandbox/    셸 실행 백엔드(local → docker/ssh 확장 지점)            │
│  config/     설정 병합(전역 → 프로젝트 → env → CLI 플래그)           │
└──────────────────────────────────────────────────────────────────┘
```

### 4.1 데이터 흐름 (한 턴)

1. 사용자가 TUI에 프롬프트 입력 → `session/prompt` (ACP)
2. 서버가 컨텍스트 빌드(시스템 프롬프트 + 지침 파일 + 메모리 + 압축된 히스토리) → LLM 스트리밍
3. 텍스트 청크는 `session/update(agent_message_chunk)`로 즉시 전달
4. 도구 호출 발생 → 권한 평가 → 필요 시 `session/request_permission` → 실행 → `session/update(tool_call / tool_call_update)`
5. 파일 편집 도구는 결과에 `diff` 콘텐츠 포함 → TUI 인라인 렌더, IDE 연결 시 VS Code diff 뷰도 오픈
6. 모델이 `end_turn` → 모드가 `build`면 검증 게이트 실행 → 실패 시 실패 내용을 다음 입력으로 주입하고 루프 계속
7. 완료 → `session/prompt` 응답(`stopReason`) 반환, 세션 DB에 저장

## 5. 저장소 레이아웃 (모노레포)

```
perdix-agent/
├── AGENTS.md                  # 정본 지침 (Codex, 범용 에이전트)
├── CLAUDE.md                  # @AGENTS.md 임포트 + Claude Code 전용 보충
├── README.md
├── package.json               # pnpm workspace 루트
├── pnpm-workspace.yaml
├── docs/
│   ├── architecture.md        # 이 문서
│   ├── harness-loop.md        # 루프 상태기계, 검증 게이트, 예산
│   ├── protocol.md            # ACP 사용 범위 + _perdix 확장 메서드 + IDE bridge
│   └── adr/                   # 결정 기록 (0001-acp, 0002-python-core, ...)
├── server/                    # Python 패키지: perdix-server
│   ├── pyproject.toml         # uv 관리, hatchling
│   ├── src/perdix/
│   │   ├── __main__.py        # `perdix-server` 진입점 (ACP over stdio)
│   │   ├── acp/
│   │   │   ├── agent.py       # AgentSideConnection 구현 (SDK 바인딩)
│   │   │   ├── mapping.py     # 내부 이벤트 ↔ ACP session/update 변환
│   │   │   └── extensions.py  # _perdix/* 확장 메서드
│   │   ├── harness/
│   │   │   ├── runner.py      # Session 단위 루프 오케스트레이터
│   │   │   ├── turn.py        # 한 사용자 턴: step 반복 + 종료 판정
│   │   │   ├── step.py        # LLM 1회 호출 + 도구 디스패치
│   │   │   ├── verifier.py    # 검증 게이트(테스트/린트/타입체크/리뷰어)
│   │   │   ├── budget.py      # 토큰/시간/반복 예산
│   │   │   ├── hooks.py       # pre/post tool, on_turn_end 등 훅 버스
│   │   │   └── modes.py       # chat / plan / build 모드 정의
│   │   ├── context/
│   │   │   ├── builder.py     # 시스템 프롬프트 조립(캐시 친화 순서)
│   │   │   ├── instructions.py# AGENTS.md / CLAUDE.md(@import) 탐색·병합
│   │   │   ├── skills.py      # SKILL.md 로더(.claude/skills, .agents/skills, ~/.*)
│   │   │   ├── compaction.py  # 히스토리 압축(요약/도구결과 정리)
│   │   │   ├── messages.py    # 내부 메시지/콘텐츠 블록 모델
│   │   │   └── view.py        # event log → ModelView 순수 투영
│   │   ├── llm/
│   │   │   ├── base.py        # Provider 프로토콜, ModelRequest/ModelEvent
│   │   │   ├── anthropic.py   # anthropic SDK (adaptive thinking, prompt caching)
│   │   │   ├── openai.py      # Responses API
│   │   │   ├── openai_compat.py # chat/completions (Ollama, vLLM, OpenRouter, LM Studio)
│   │   │   ├── gemini.py
│   │   │   └── catalog.py     # 모델 메타(컨텍스트 창, 가격, 기능 플래그)
│   │   ├── tools/
│   │   │   ├── registry.py    # 툴셋 조합, 스키마 노출
│   │   │   ├── fs.py          # read / write / edit / glob / ls
│   │   │   ├── search.py      # grep (ripgrep 래핑)
│   │   │   ├── shell.py       # bash (sandbox 백엔드 경유)
│   │   │   ├── web.py         # webfetch / websearch
│   │   │   ├── todo.py        # 계획/체크리스트
│   │   │   ├── task.py        # 서브에이전트 스폰
│   │   │   ├── skill.py       # 스킬 로드
│   │   │   ├── session_search.py # 과거 세션 FTS5 검색(모든 프로젝트, 같은 프로젝트 우선 정렬)
│   │   │   └── ask_user.py    # 사용자 질문(ACP elicitation/create)
│   │   ├── permissions/
│   │   │   ├── policy.py      # allow / ask / deny 규칙 평가
│   │   │   └── rules.py       # 패턴 매칭(bash 명령 프리픽스, 경로 글롭)
│   │   ├── mcp/
│   │   │   ├── hub.py         # 서버 수명주기, 도구 노출
│   │   │   └── config.py      # .mcp.json, .codex/config.toml, .perdix 설정 통합
│   │   ├── subagents/
│   │   │   ├── spawn.py       # 격리된 하위 세션 생성/수집
│   │   │   └── presets.py     # explore(읽기전용), executor, reviewer
│   │   ├── session/
│   │   │   ├── store.py       # SQLite event log(append-only) + FTS5, WAL
│   │   │   ├── checkpoint.py  # 섀도 git 스냅샷/복원
│   │   │   └── resume.py      # session/load 복원
│   │   ├── memory/
│   │   │   └── store.py       # MEMORY.md 읽기/쓰기(프로젝트/전역)
│   │   ├── sandbox/
│   │   │   ├── base.py        # ExecBackend 프로토콜
│   │   │   └── local.py       # 로컬 subprocess (docker/ssh는 추후)
│   │   └── config/
│   │       ├── schema.py      # pydantic 설정 모델
│   │       ├── loader.py      # 전역 → 프로젝트 → env → argv → session _meta 병합
│   │       └── trust.py       # 프로젝트 신뢰 게이트(~/.perdix/trust.json)
│   └── tests/
├── packages/
│   ├── protocol/              # @perdix/protocol — ACP 확장 타입, 이벤트 모델, IDE bridge 타입
│   ├── cli/                   # perdix — Ink TUI, 서버 스폰, ACP client, IDE bridge client
│   │   └── src/
│   │       ├── main.ts        # 인자 파싱, 서버 프로세스 기동
│   │       ├── acp/           # ClientSideConnection 구현
│   │       ├── ide/           # IDE bridge 발견/연결(env + lock file)
│   │       └── ui/            # Ink 컴포넌트(채팅, 도구 카드, diff, 권한 프롬프트)
│   └── vscode/                # perdix-vscode — 터미널 런처 + IDE bridge 서버
│       └── src/
│           ├── extension.ts
│           ├── bridge/        # WS 서버, 락파일, openDiff/openFile/selection/diagnostics
│           └── terminal.ts
└── scripts/                   # dev 부트스트랩, 프로토콜 타입 동기화
```

## 6. 핵심 컴포넌트 설계

### 6.1 하네스 루프 (`harness/`)
상세는 [harness-loop.md](harness-loop.md). 요점:

- **3단 구조**: `Runner`(세션) → `Turn`(사용자 입력 1회) → `Step`(LLM 호출 1회).
- **내부 루프**: `Step`이 tool_use를 내면 권한 평가 → 훅 → 실행 → 결과를 append → 다음 `Step`.
- **외부 루프(검증 게이트)**: `build` 모드에서 모델이 턴을 끝내면 `Verifier`가 프로젝트 설정의 검증 명령(테스트/린트/타입체크)과 선택적 리뷰어 서브에이전트를 돌린다. 실패하면 실패 로그를 다음 user 메시지로 주입하고 재시도. 최대 반복/예산 초과 시 중단하고 사유를 보고.
- **세션 기록은 append-only event log**이고, 모델에 보내는 메시지 배열(ModelView)은 그 로그의 순수 투영이다. 압축은 `CompactionRecord`를 로그에 추가하고 투영만 바꾼다. 원본은 유지된다.
- **병렬 tool_use는 등장 순서의 연속 구간 단위로** 읽기 전용 구간만 동시 실행하고, 쓰기·실행 도구는 장벽으로 단독 실행한다. 결과는 투영에서 등장 순서로 재정렬하고, 와이어 형식은 어댑터가 정한다(harness-loop.md §3.3).
- **중단/재개**: `session/cancel`(알림)은 현재 Step 취소 + 진행 중 도구 프로세스 그룹 kill 후 prompt에 `stopReason: cancelled`로 정상 응답. Turn 진입 시 취소 이벤트를 동기적으로 등록하고 세션당 Turn 하나만 허용한다. assistant 레코드는 스트림 정상 종료 시, `tool_started`/`tool_result`는 각각 즉시 커밋되어 크래시 후에도 `session/load`가 복구할 수 있다.

### 6.2 모드 (`harness/modes.py`)
| 모드 | 도구 | 검증 게이트 | 용도 |
|---|---|---|---|
| `chat` | 전체 | 없음 | 일반 질의응답, 단발 작업 |
| `plan` | `is_read_only(input)`이 참인 도구 전체(read/grep/glob/ls/webfetch/websearch/session_search/skill/todo/task(explore·reviewer·security-reviewer)) | 없음 | 계획 수립, 분석 |
| `build` | 전체 | **있음** | 자율 구현. 완료 조건 만족까지 반복 |

모드는 ACP `session/set_mode`로 전환하고 `session/new` 응답의 `modes`로 광고한다. 모델/공급자 전환은 `session/set_config_option`(category `model`). OpenCode의 build/plan, Kilo의 Code/Architect에 대응.

### 6.3 LLM 공급자 추상화 (`llm/`)
- 내부 메시지 모델은 자체 정의(`context/messages.py`). assistant 레코드는 `items: [{kind, provider, model, opaque_raw}]` **순서 리스트**이고 kind는 `text`, `tool_use`, `thinking`, `reasoning`, `signature`, `summary`. 불투명 아이템은 원본 바이트를 보존하며, `(provider, model)`이 현재 요청과 다르면 투영에서 버린다(harness-loop.md §5.1).
- `Provider.stream(ModelRequest) -> AsyncIterator[ModelEvent]` 단일 인터페이스. 이벤트: `text_delta`, `tool_use_start/delta/end`, `thinking_delta`, `usage`, `stop(reason)`.
- 어댑터는 두 가지를 책임진다. (a) **와이어 형식**: 도구 결과를 한 user 메시지로 묶을지(Anthropic, `tool_result`가 content 맨 앞), `function_call_output` item으로 쪼갤지(OpenAI Responses), `role: tool` 메시지로 쪼갤지(chat/completions), functionResponse part로 넣을지(Gemini). (b) **`sanitize_tool_schema()`**: `$ref` 펼치기, 공급자가 거부하는 키 제거, strict 모드 조건 충족. 변환 실패 도구는 그 공급자에서만 제외하고 경고.
- 어댑터별 특이사항:
  - **anthropic**: 공식 `anthropic` SDK, 스트리밍 필수, adaptive thinking 기본, `cache_control` 브레이크포인트(시스템/도구 뒤, 마지막 user 앞), thinking/redacted_thinking 블록을 tool_use 앞 순서 그대로 회신, `refusal` stop reason 처리.
  - **openai**: Responses API, `store=false` + `include=["reasoning.encrypted_content"]`로 stateless 운용. reasoning item은 바로 뒤의 item과 함께 보낸다.
  - **openai_compat**: chat/completions. Ollama/vLLM/LM Studio/OpenRouter. 비어 오는 tool_call id는 생성해 채운다. 기능 플래그(도구 지원 여부, 병렬 호출)로 분기.
  - **gemini**: 공식 SDK. functionCall part의 `thoughtSignature`를 그대로 회신하고 part 순서를 바꾸지 않는다. functionResponse는 한 content에 호출 순서대로.
- `catalog.py`는 모델별 컨텍스트 창/출력 한도/기능 플래그를 가짐. 컴팩션 임계와 예산 계산의 입력.

### 6.4 도구 (`tools/`)
- 각 도구는 `name, description, input_schema, run(ctx, input) -> ToolResult` 의 단순 프로토콜.
- `ToolResult`는 모델용 텍스트 외에 클라이언트 렌더용 `content`(diff, terminal 출력 등)를 분리해서 들고, `acp/mapping.py`가 ACP `tool_call_update`로 변환.
- 파일 I/O는 **ACP 클라이언트가 `fs` capability를 광고하면 클라이언트 경유**, 아니면 로컬 직접. 기준은 항상 **디스크**다. 클라이언트는 읽기 결과에 dirty 경고만 덧붙이고, 쓰기는 디스크에 직접 하며 dirty 버퍼와 충돌하면 사용자에게 묻는다(protocol.md §2).
- 도구는 `is_read_only(input) -> bool`을 구현한다. 병렬 실행과 plan 모드 허용 여부의 근거다. MCP 도구는 기본 쓰기로 본다.
- 셸은 `sandbox/` 백엔드로만 실행. ACP `terminal/*`를 클라이언트가 지원하면 그쪽으로 위임 가능(옵션).

### 6.5 권한 (`permissions/`)
OpenCode 방식의 3단계: `allow | ask | deny`. 규칙은 도구 + 패턴 단위.
```json
{ "permissions": {
    "read": "allow", "edit": "ask", "bash": { "git status*": "allow", "rm -rf*": "deny", "*": "ask" },
    "webfetch": "ask" } }
```
`ask`는 ACP `session/request_permission`으로 전달. 옵션: `allow_once / allow_always / reject_once / reject_always`. `always` 응답은 세션 범위 규칙으로 승격되어 세션 레코드에 저장된다(재개 후 유지). 요청은 세션당 하나의 큐로 직렬화한다.
`--permission-mode`는 `interactive | deny | accept-edits | allowlist` 4종이고 의미는 protocol.md §1.3에 있다. 세션 `_meta`로는 argv보다 완화할 수 없다.
**M2 기본 정책(설정 파일 없을 때)**: `read/grep/glob/ls/webfetch`는 allow, 나머지는 ask.

### 6.6 지침·스킬·MCP 호환 (`context/instructions.py`, `context/skills.py`, `mcp/config.py`)
| 규약 | 읽는 경로 | 처리 |
|---|---|---|
| AGENTS.md | cwd에서 루트까지 상향 + 작업 중인 하위 디렉터리 | 가까운 것이 우선, 전부 병합 |
| CLAUDE.md | `./CLAUDE.md`, `.claude/CLAUDE.md`, `~/.claude/CLAUDE.md`, `CLAUDE.local.md` | `@path` 임포트 해석 |
| skills | `.claude/skills/*/SKILL.md`, `.agents/skills/*/SKILL.md`, `~/.claude/skills`, `~/.agents/skills` | frontmatter(name/description)만 시스템 프롬프트에 노출, 본문은 `skill` 도구로 지연 로드 |
| MCP | ACP `session/new`의 `mcpServers`, `.perdix/config.json`, `.mcp.json`, `.codex/config.toml [mcp_servers]`, 전역(`~/.perdix`, `~/.claude.json`) | 우선순위: ACP > `.perdix` > `.mcp.json` > `.codex` > 전역. 동일 이름은 상위가 덮어쓴다 (구현된 범위: stdio·tools, ACP > `.mcp.json`(신뢰 시) > `~/.perdix/mcp.json`. [ADR 0034](adr/0034-mcp-client.md), [MCP 사용법](mcp.md)) |

자체 설정은 `.perdix/config.json`(프로젝트) / `~/.perdix/config.json`(전역). AGENTS.md와 CLAUDE.md가 둘 다 있으면 둘 다 읽되 중복 문단은 제거하지 않는다(사용자 책임).

설정 병합 순서: 전역 → 프로젝트 → env → 서버 argv(`perdix-server --model … --config …`) → `session/new`의 `_meta.perdix`. CLI 플래그는 서버 argv로 전달한다. **API 키는 env 또는 `~/.perdix/credentials.json`(권한 0600)** 으로 전달한다. env가 우선하고, 파일 형식은 `{"anthropic": {"api_key": "…"}}` 처럼 공급자별 객체다. 키는 공급자 클라이언트에만 들어가고 하위 프로세스 env에는 전달되지 않는다(§6.6 비밀 env 격리). Zed 등 다른 클라이언트가 서버를 띄우는 경우도 동일.

**비밀 env 격리**: `sandbox/base.py`의 ExecBackend 계약에 따라 bash 도구, verify 명령, MCP stdio 서버, 서브프로세스 전부 `*_API_KEY`, `*_TOKEN`, `PERDIX_SECRET_*`, `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`을 제거한 환경으로 띄운다. MCP 서버에는 설정에 명시한 env만 추가로 넘긴다. 이것은 테스트 항목이다.

### 6.7 세션·체크포인트 (`session/`)
- 세션 간 참조는 자동으로 하지 않는다. 에이전트가 `session_search` 도구로 FTS5 검색해 필요한 구간만 가져온다. **기본 범위는 모든 프로젝트**이고, 결과마다 `project_path`, `session_id`, `created_at`을 붙여 출처를 구분한다. 같은 프로젝트 결과를 먼저 정렬하고, `scope: project`로 좁힐 수 있다. 전역 DB 하나를 쓰는 이유가 이 교차 검색이다. 사실 기록은 `memory/`의 MEMORY.md(프로젝트/전역)로 간다.
- SQLite 단일 파일 `~/.perdix/perdix.db`: `sessions`, `events`(append-only, `kind`에 `compaction` 포함), `usage`, `fts_events`, `meta(schema_version)`. WAL 모드 + `busy_timeout`, Step 단위 짧은 트랜잭션. 서버 버전이 여럿(VS Code 번들판, uv tool판) 같은 DB를 열 수 있으므로 `schema_version`보다 오래된 서버는 쓰기를 거부한다.
- 체크포인트: 작업 디렉터리를 `~/.perdix/checkpoints/<realpath-hash>/` 의 섀도 git 저장소로 스냅샷.
  - 세션별 ref(`refs/perdix/<session-id>`)와 **세션별 `GIT_INDEX_FILE`**(index는 git-dir당 하나라 세션 간 stage가 덮어쓰인다).
  - 프로젝트 단위 락은 파일 존재가 아니라 `fcntl.flock`(크래시 후 잔존 락 방지).
  - 섀도 git 명령은 GIT_* 변수를 지운 환경에서 `GIT_CONFIG_GLOBAL=/dev/null`과 명시적 `core.excludesFile`로 실행한다. 프로젝트 `.git/info/exclude`와 기본 제외(node_modules, .venv, dist, 5MB 초과 파일)를 섀도 `info/exclude`에 복사한다. 하위 디렉터리의 중첩 `.git`은 gitlink로만 기록되어 복원되지 않는다는 한계를 문서화한다.
  - Step당 첫 쓰기 도구 직전에 1회 커밋. 세션 종료 시 ref 정리, gc는 락 안에서.
- 복원 단위는 **세션 + 경로 집합**이다. edit/write는 경로를 기록하고, bash는 전후 스냅샷 diff(`git add -A` 후 `diff --name-only`)로 변경 경로를 기록한다. 동시 세션이 같은 파일을 고쳤을 수 있으므로 복원 시 현재 디스크 내용이 "그 세션이 마지막으로 쓴 내용"과 다르면 **충돌로 보고 사용자에게 확인**한다. 경로 귀속은 최선 노력이다.

### 6.8 서브에이전트 (`subagents/`)
- 같은 프로세스 안의 **격리된 하위 세션**. 부모 히스토리는 공유하지 않고 과업 설명만 전달.
- 프리셋: `explore`(읽기 전용, 저비용 모델), `executor`(쓰기 가능), `reviewer`(읽기 전용, 검증 게이트용).
- 병렬 스폰은 `explore`/`reviewer`/`security-reviewer`만 허용. `executor`/`debugger`는 직렬(병렬은 git worktree 격리로 2차). 부모 권한 규칙과 예산을 상속한다.
- ACP에는 부모 필드가 없으므로 진행 상황은 부모 `tool_call`의 `content`에 텍스트로 누적하고, `_meta.perdix.parentToolCallId`로 Perdix TUI에서만 중첩 표시.

### 6.9 프로젝트 신뢰 게이트
클론한 저장소의 `.mcp.json`(stdio 서버), `.perdix/config.json`의 `verify.commands`, 권한 완화 규칙을 그대로 읽으면 저장소를 여는 것만으로 임의 명령이 실행된다. 따라서:
- 프로젝트별 신뢰 여부를 `~/.perdix/trust.json`에 기록한다. 최초 1회 `elicitation/create`(form)로 승인받는다. 클라이언트가 elicitation을 광고하지 않으면 미신뢰로 유지하고 `session_info_update`로 안내한다. 헤드리스/CI는 `--trust-project` 플래그로 신뢰한다.
- 재승인 해시 대상: `.perdix/config.json`, `.mcp.json`, `.codex/config.toml`, `.claude/settings.json`의 hooks 항목.
- 신뢰 전: 프로젝트 MCP 서버 비활성, `verify.commands` 자동 실행 안 함, 프로젝트 설정의 권한 규칙은 **강화 방향(deny/ask)만** 적용.
- 신뢰 후: 전부 적용. 설정 파일 해시가 바뀌면 재승인.

### 6.10 역할별 모델 배치 (기본값)
모델은 세션 단위 속성이고 서브에이전트는 독립 세션이므로 역할마다 다른 모델을 쓸 수 있다. 설정의 `models` 별칭 테이블과 `agents` 매핑으로 정하며, `task` 도구의 `model?`과 `session/set_config_option`의 선택지는 이 테이블에서 나온다.

| 역할 | 기본 모델 | effort | 근거 |
|---|---|---|---|
| `main` (chat/build) | `claude-opus-5-5` | `xhigh` | 장기 자율 작업과 도구 사용의 품질이 비용보다 중요. 코딩 에이전트 권장 effort |
| `main` (plan 모드) | `claude-opus-5-5` | `max` | 계획 품질이 이후 전체 비용을 좌우 |
| `executor` | `claude-opus-5-5` | `high` | 부모가 쪼개 준 과업이라 main보다 한 단계 낮춰 토큰 절약 |
| `explore` | `claude-haiku-4-5` | (미지원) | 읽기 전용 탐색·요약. 양이 많고 판단은 얕음 |
| `reviewer` | `claude-sonnet-5-5` | `medium` | 완료 조건 판정에 판단력이 필요하지만 Opus까지는 과함. 구조화 출력 사용 |
| `debugger` | `claude-sonnet-5-5` | `medium` | 실패 원인 분석. 재현에 셸이 필요해 executor와 같은 쓰기 가능 등급(권한이 읽기 전용/전체 이분법이라). 직렬 실행 |
| `security-reviewer` | `claude-sonnet-5-5` | `medium` | 취약점·신뢰 경계 점검. reviewer와 같은 읽기 전용 등급, 병렬 스폰 가능 |
| `compactor` (히스토리 요약) | `claude-haiku-4-5` | (미지원) | 요약 전용, 빈번 호출 |
| `titler` (세션 제목, 스킬 추출 등 잡무) | `claude-haiku-4-5` | (미지원) | 단발 저비용 |

#### OpenAI(GPT) 프로파일
같은 역할을 OpenAI 모델로 채우는 기본값. 모델 ID와 가격은 2026-10-07 developers.openai.com 기준이며 `llm/catalog.py`에 같이 기록한다.

| 역할 | 기본 모델 | effort | 근거 |
|---|---|---|---|
| `main` (chat/build) | `gpt-6.1-sol` ($2/$10) | `xhigh` | Opus 5.5와 같은 가격대의 일상 플래그십. 1.05M 컨텍스트 |
| `main` (plan 모드) | `gpt-6-astra` ($10/$50) | `max` | 최상위 모델. 계획 단계만 쓰므로 비용 영향이 작다 |
| `executor` | `gpt-6.1-sol` | `high` | main과 동일 모델로 스타일 일관성 유지 |
| `explore` | `gpt-6-luna` ($0.10/$0.50) | `low` | 경량. effort `none`도 지원하지만 요약 품질 때문에 low |
| `reviewer` | `gpt-6.1-sol` | `medium` | 구조화 출력으로 완료 판정 |
| `debugger` | `gpt-6.1-sol` | `medium` | 원인 분석에 판단력이 필요. 쓰기 가능 등급 |
| `security-reviewer` | `gpt-6.1-sol` | `medium` | 읽기 전용 보안 검토 |
| `compactor` | `gpt-6-luna` | `low` | 요약 전용 |
| `titler` | `gpt-6-luna` | `none` | 단발 잡무 |

- `gpt-5.3-codex`($1.75/$14)는 에이전트 코딩 특화지만 이전 세대(5.x)라 기본값에서 제외한다. 별칭으로 두고 `executor`에만 실험적으로 쓴다.
- OpenAI 어댑터는 Responses API에 `store=false` + `include=["reasoning.encrypted_content"]`로 stateless 운용한다(§6.3). effort는 `reasoning.effort`로 전달한다.

```json
{ "models": {
    "gpt-strong": { "provider": "openai", "model": "gpt-6-astra" },
    "gpt-mid":    { "provider": "openai", "model": "gpt-6.1-sol" },
    "gpt-cheap":  { "provider": "openai", "model": "gpt-6-luna" },
    "gpt-codex":  { "provider": "openai", "model": "gpt-5.3-codex" } },
  "agents": {
    "main":      { "model": "gpt-mid", "effort": "xhigh", "plan_model": "gpt-strong", "plan_effort": "max" },
    "executor":  { "model": "gpt-mid", "effort": "high" },
    "explore":   { "model": "gpt-cheap", "effort": "low" },
    "reviewer":  { "model": "gpt-mid", "effort": "medium" },
    "compactor": { "model": "gpt-cheap", "effort": "low" },
    "titler":    { "model": "gpt-cheap", "effort": "none" } } }
```

#### 혼합 프로파일(권장)
공급자를 섞어도 서브에이전트는 히스토리를 공유하지 않으므로 문제없다. 비용 대비 품질로 보면 `main`/`executor`는 Claude Opus 5.5, `reviewer`는 GPT-6.1 Sol(다른 계열의 눈으로 검토), `explore`/`compactor`/`titler`는 GPT-6 Luna가 가장 싸다. 단 `main`의 공급자를 대화 중에 바꾸면 thinking 블록을 잃는다.

운영 규칙:
- main과 executor는 **같은 모델**을 기본으로 둔다. 부모가 쓴 thinking 블록은 공유되지 않으므로 호환 문제는 없지만, 같은 모델이면 코드 스타일 일관성이 좋다.
- Anthropic 공급자는 `fallbacks: "default"`(서버측 거부 폴백)를 기본으로 켠다. `refusal` stop reason 발생 시 자동으로 다른 모델로 넘어간다.
- 로컬/OpenAI/Gemini 모델은 별칭으로 추가해 `explore`나 `compactor`부터 교체하는 것을 권장한다. 판단이 필요한 `main`/`reviewer`를 바꿀 때는 검증 게이트 통과율을 먼저 비교한다.
- 가격·컨텍스트 창은 `llm/catalog.py`가 들고 있고, 예산 계산과 압축 임계의 입력이다.

```json
{ "models": {
    "strong":  { "provider": "anthropic", "model": "claude-opus-5-5" },
    "mid":     { "provider": "anthropic", "model": "claude-sonnet-5-5" },
    "cheap":   { "provider": "anthropic", "model": "claude-haiku-4-5" },
    "local":   { "provider": "openai_compat", "base_url": "http://localhost:11434/v1", "model": "qwen3" } },
  "agents": {
    "main":      { "model": "strong", "effort": "xhigh", "plan_model": "strong", "plan_effort": "max" },
    "executor":  { "model": "strong", "effort": "high" },
    "explore":   { "model": "cheap" },
    "reviewer":  { "model": "mid", "effort": "medium" },
    "compactor": { "model": "cheap" },
    "titler":    { "model": "cheap" } } }
```

## 7. 클라이언트 설계

### 7.1 `perdix` CLI (packages/cli)
- 역할: Python 서버 프로세스 기동(stdio) → ACP `initialize` → `session/new` → TUI 루프.
- 서버 발견 순서: `PERDIX_SERVER` env → PATH의 `perdix-server` → `uv tool run perdix-server` 폴백. (배포 방식은 미결, 8절)
- 실행 전 Node 버전 검사(Ink 8은 `node >= 22`). 사용자 PATH의 node로 돌기 때문이다.
- TUI(Ink): 메시지 스트림, 도구 카드(접기/펼치기), 인라인 diff, 권한 프롬프트, elicitation(form) 처리, 모드 전환, 세션 목록/재개, `/` 명령. 완료된 메시지는 `<Static>`으로 내보내고 도구 출력 높이를 제한한다(통합 터미널에서 화면 높이를 넘으면 깜빡임과 스크롤백 중복이 생긴다).
- 헤드리스 모드: `perdix -p "..." --json` 으로 TUI 없이 이벤트 스트림 출력(CI, 스크립트용). 이때 서버는 `--permission-mode deny`(기본) 또는 `allowlist`로 기동한다.

### 7.2 VS Code 확장 (packages/vscode)
- **현재 구현 (ADR 0008):** Perdix 사이드바 Webview와 `Perdix: Open Chat` 명령을 제공한다. 확장 호스트가 기존 ACP 연결·세션 어댑터를 재사용해 서버를 직접 구동하며 채팅, 권한 응답, 모델·모드 선택, 세션 불러오기를 처리한다. Windows 동봉 서버를 자동 탐색하고 신뢰한 작업 폴더에서만 실행한다. 아래 터미널·브리지 항목은 이전 설계이며 아직 구현하지 않은 에디터 연동 계획이다.
- 명령 `Perdix: Open`이 통합 터미널에 `perdix`를 띄움. 터미널을 여러 개 열면 세션도 여러 개이며, 세션 DB와 체크포인트는 6.7의 규칙으로 동시 쓰기를 처리한다. 환경변수 `PERDIX_IDE_PORT`, 락파일 `~/.perdix/ide/<port>.lock`(토큰 포함)로 CLI가 브리지를 발견.
- 브리지(WS, localhost, JSON-RPC): `ide/openDiff`, `ide/openFile`, `ide/getDirtyFiles`, `ide/revertFile`, `ide/getSelection`, `ide/getDiagnostics`, `ide/getOpenEditors`. Origin 검사, 락파일 0600, `extensionKind: ["workspace"]`. Ctrl+P 등 VS Code가 가로채는 키는 `sendSequence` 키바인딩으로 보완.
- CLI는 ACP 클라이언트로서 `fs/read_text_file`은 디스크에서 읽고 dirty면 경고를 덧붙이며, `fs/write_text_file`은 디스크에 직접 쓰고 dirty 충돌 시 사용자에게 묻는다(에디터 저장 경로는 formatOnSave 때문에 쓰지 않는다). 진단은 `_perdix/diagnostics` 알림으로 서버에 전달. 즉 **서버는 VS Code의 존재를 모른다**.
- 2차: Webview 채팅 패널을 두 번째 ACP 클라이언트로 추가(서버 변경 불필요).

### 7.3 `@perdix/protocol`
- ACP TS SDK 재수출 + `_perdix/*` 확장 메서드 타입 + IDE bridge 타입. CLI/확장이 공유.

## 8. 미결 사항 (구현 착수 전 결정 필요)

1. **Python 서버 배포 방식**: `uv tool install perdix-server`(권장, 사용자에게 uv 필요) vs PyInstaller 단일 바이너리를 npm 패키지에 동봉(크기 큼, 플랫폼별 빌드). **M2 착수 전 ADR로 확정.**
2. **검증 명령 자동 탐지 범위**: `package.json` scripts / `pyproject` / `Makefile`에서 test/lint 자동 추출할지, 설정에 명시만 허용할지.
3. **메모리 자동 기록**: [ADR 0014](adr/0014-automatic-memory.md)에 따라 유휴 대화에서 자동 추출·통합하고 다음 세션에서 참조한다. 프로젝트·전역 범위와 세션별 생성·참조 제어는 [메모리 사용법](memory.md)을 따른다.
4. **스킬 자동 추출**: Hermes의 "작업 완료 후 스킬 생성"을 1차에 넣을지.
5. **원격 실행**: ACP WebSocket 전송으로 서버를 원격 호스트에 두는 시나리오를 1차 범위에 둘지.

## 9. 단계별 구현 순서 (제안)

| 단계 | 범위 | 완료 기준 |
|---|---|---|
| M0 | 모노레포 부트스트랩, AGENTS.md/CLAUDE.md, 린트/테스트 파이프라인, 스모크 테스트 1개씩 | `pnpm -r build`, `pnpm -r test`, `uv run pytest` 통과(테스트 0개면 pytest는 exit 5) |
| M1 | Python 코어: event log + ModelView(아이템 순서 리스트, index 재정렬), anthropic 어댑터(sanitize_tool_schema 포함), read/edit/bash/grep 도구, 기본 tool-call 루프(연속 구간 병렬/장벽, tool_started/tool_result 커밋, 복구), 세션 락, 헤드리스 ACP stdio 서버, 하드코딩 기본 권한 정책, 비밀 env 격리 | Python SDK `connect_to_agent` + subprocess로 pytest 안에서 한 턴 완주 + 강제 종료 후 `session/load` 복구 테스트 + 모델 전환 후 thinking 블록 제거 테스트 통과 |
| M2 | CLI TUI(Ink): 스트리밍, 도구 카드, 권한 프롬프트, elicitation(form) 핸들러, 세션 저장/재개 | dev 모드(uv run)에서 터미널 실사용 가능. 배포 방식 ADR 확정 |
| M3 | 지침/스킬/MCP 호환 로더, 권한 규칙, 신뢰 게이트, 체크포인트 | 기존 Claude Code/Codex 프로젝트에서 신뢰 승인 1회 후 설정 없이 동작 |
| M4 | 검증 게이트 + build 모드 + 서브에이전트, 컨텍스트 압축 | 자율 루프로 테스트 통과까지 반복 |
| M5 | VS Code 확장(터미널 런처 + IDE 브리지) | diff 뷰/선택 영역 연동 |
| M6 | OpenAI/Gemini/호환 어댑터, 모델 카탈로그 | 설정만으로 공급자 교체 |
