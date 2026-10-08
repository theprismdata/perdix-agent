# Perdix Agent — 에이전트 작업 지침

이 파일은 Codex, Claude Code(CLAUDE.md가 임포트), 그리고 Perdix 자신이 읽는 정본 지침이다.
설계 전체: [docs/architecture.md](docs/architecture.md). 루프: [docs/harness-loop.md](docs/harness-loop.md). 프로토콜: [docs/protocol.md](docs/protocol.md). 결정 기록: [docs/adr/](docs/adr/).

## 프로젝트 한 줄 요약
Python 에이전트 코어(`server/`)를 TypeScript 클라이언트(`packages/cli`, `packages/vscode`)가 ACP(JSON-RPC over stdio)로 구동하는 코딩 에이전트.

## 저장소 구조
- `server/` — Python 3.12+, `uv` 관리. 패키지명 `perdix`, 진입점 `perdix-server`. 하네스 루프·LLM·도구·권한·세션 전부 여기.
- `packages/protocol/` — `@perdix/protocol`. ACP 타입 재수출 + `_perdix/*` 확장 + IDE bridge 타입.
- `packages/cli/` — `perdix` 바이너리. Ink TUI, 서버 스폰, ACP 클라이언트, IDE bridge 클라이언트.
- `packages/vscode/` — VS Code 확장. 터미널 런처 + IDE bridge 서버. Webview 없음(ADR 0003).
- `docs/` — 설계 문서. 구조를 바꾸는 결정은 `docs/adr/NNNN-*.md`로 남긴다.

## 경계 규칙 (반드시 지킬 것)
1. **에이전트 로직은 Python에만 둔다.** TS 쪽에 LLM 호출, 도구 실행, 권한 판단을 넣지 않는다.
2. **서버는 에디터를 모른다.** VS Code 연동은 `packages/cli`의 IDE bridge 클라이언트에서 끝낸다. 서버에 `vscode` 관련 코드가 들어가면 잘못된 것이다.
3. **클라이언트↔서버 통신은 ACP 표준 메서드 우선.** 새 기능이 필요하면 `_perdix/` 접두 확장 메서드로 추가하고 `docs/protocol.md` 표를 갱신한다. 표준 ACP 클라이언트가 확장을 몰라도 핵심 기능은 동작해야 한다.
4. **히스토리는 append-only.** 과거 메시지를 수정·삭제하는 코드는 금지. 압축은 모델에 보내는 뷰만 축약하고 원본은 DB에 남긴다.
5. **도구 입력은 실행 전 스키마 검증.** 검증 실패는 예외가 아니라 `is_error` 도구 결과로 모델에 돌려준다.
6. **병렬 tool_use 결과는 투영에서 등장 순서로 모아 어댑터에 넘긴다.** 와이어 형식(한 user 메시지, function_call_output, role: tool)은 어댑터가 정한다.
   실행은 등장 순서의 연속 구간 단위로 읽기 전용 구간만 동시, 쓰기·실행 도구는 장벽으로 단독. 체크포인트는 Step당 첫 쓰기 직전 1회.
7. **공급자별 특이사항은 `server/src/perdix/llm/<provider>.py` 안에 가둔다.** 하네스나 도구 코드에 공급자 분기를 두지 않는다.
8. **세션 기록은 event log + ModelView 투영.** 압축·복구는 레코드를 append해서 표현하고 투영만 바꾼다.
9. **프로젝트 설정은 신뢰 게이트 뒤에서만 실행 권한을 넓힌다.** 신뢰 전에는 MCP 서버/verify 명령을 자동 실행하지 않는다.
10. **ACP 표준에 있는 기능은 표준 메서드를 쓴다**(`session/list`, `session/set_config_option`, `usage_update`, `elicitation/create`). `_perdix` 확장은 docs/protocol.md §1.2의 목록으로 한정한다.
11. **와이어 키는 camelCase, 내부와 설정 파일은 snake_case.** ACP `_meta.perdix.*`와 IDE bridge 파라미터는 camelCase로 쓴다.
12. **하위 프로세스에 비밀 env를 넘기지 않는다.** bash, verify, MCP, 섀도 git 전부 ExecBackend의 env 정리 계약을 거친다.

## 명령
```bash
# 부트스트랩
pnpm install && (cd server && uv sync)

# 테스트
pnpm -r test                 # TS 전체
(cd server && uv run pytest) # Python

# 린트/타입
pnpm -r lint && pnpm -r typecheck
(cd server && uv run ruff check . && uv run mypy src)

# 로컬 실행
pnpm --filter perdix dev     # CLI (서버 자동 스폰)
(cd server && uv run perdix-server)  # 서버 단독 (stdio ACP)
```
위 명령은 M0 부트스트랩 완료 후 유효하다. 없으면 먼저 만든다.

## 코딩 규약
- Python: `ruff` + `mypy --strict`. 비동기는 `asyncio` 단일. 데이터 모델은 `pydantic` v2. 공개 함수는 타입 힌트 필수.
- TypeScript: `strict: true`, ESM, `tsup` 빌드, `vitest` 테스트. 런타임 검증은 `zod`.
- 파일/모듈 이름은 역할을 그대로 쓴다(`verifier.py`, `checkpoint.py`). 추상적인 이름(`utils`, `helpers`, `manager`) 금지.
- 설정 파일 키와 Python 내부는 `snake_case`. ACP/IDE bridge 와이어 키는 `camelCase`(규칙 11).
- 주석과 문서는 한국어, 식별자와 커밋 메시지는 영어.

## 작업 방식
- 구조를 바꾸는 변경(새 패키지, 프로토콜 메서드 추가, 저장 포맷 변경)은 먼저 ADR 초안을 쓰고 진행한다.
- 테스트 없는 기능 추가는 미완으로 본다. `test.skip`, `xfail` 남기기 금지.
- 완료 주장 전에 위 테스트/린트 명령을 실제로 실행하고 결과를 보고한다.
- 외부 SDK(anthropic, openai, google-genai, agent-client-protocol, ACP TS SDK) 사용법이 불확실하면 추측하지 말고 공식 문서를 확인한다.
