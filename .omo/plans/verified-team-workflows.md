# verified-team-workflows - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**Who this is for and what changes for them:** 코드 변경을 여러 에이전트에게 맡기는 개발자. 작업 진행과 산출물, 검증 근거를 확인하고 중단 이후에도 보존된 작업부터 이어간다.

**What you'll get:** 작업별 격리 실행, 팀 진행 그래프, 실패 복구, 통합 검증, 검증한 변경을 명시적으로 받아들이는 흐름.

**Why this approach:** 기존 에이전트의 도구·권한·대화 기록을 유지하고 팀 실행만 추가한다. 기존 웹 대시보드를 확장해 별도 앱 운영을 피한다.

**What it will NOT do:** 임의 워크플로우 편집기, 원격 분산 실행, 자동 push·PR·배포, 실행 결과가 불명확한 명령의 무조건 재시도.

**Effort:** XL
<!-- Effort is exactly ONE band, never hours/days. Quick = single edit, minutes of agent work; Short = one focused change, a few files; Medium = multi-file feature in one session; Large = several waves, one long session; XL = multi-session or architectural work. A written duration is rewritten to a band. -->
**Risk:** High - 프로세스 중단 시 외부 부작용과 영속 상태가 어긋나는 구간, 병렬 변경의 통합.
**Decisions to sanity-check:** 그래프는 기존 웹 대시보드에 제공한다. 로컬 Git 저장소를 대상으로 하고, 변경 반영은 사용자의 명시적 요청으로만 한다.

다음 단계: 이 계획을 검토한 뒤 별도로 구현 시작을 요청한다. 이 문서 작성은 구현 시작 승인이 아니다.

---

> TL;DR (machine): XL / High / LangGraph 팀 실행, worktree 격리, 검증 영수증, 안전한 재개, Gateway 그래프.

## Scope
### Affected user and ideal state
<!-- From the draft ledger: who this touches and how they use the result, then one IS row per property with its reason, then one GAP row per difference with its reason. Every todo below closes a GAP row; ## Success criteria proves every IS row. -->
**Affected user:** CLI·VS Code·Gateway에서 코딩 작업을 맡기는 개발자와 기존 ACP 클라이언트. 기존 단일 에이전트와 터미널 팀 동작을 보존한다.

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | 요청에서 작업 계획을 만들고 확인한 뒤 실행한다 | 원치 않는 실행과 작업 범위 확대 방지 |
| IS-2 | 병렬 작업이 원본 프로젝트를 변경하지 않는다 | 사용자 작업 보존 |
| IS-3 | 완료·검증됨·수락됨을 구분한다 | 모델의 완료 발언은 검증 증거가 아님 |
| IS-4 | 중단 후 완료 결과를 보존하고 불명확한 작업을 표시한다 | 중복 실행과 거짓 복구 방지 |
| IS-5 | 그래프에서 실제 담당·의존성·실행 이력·실패 이유를 확인한다 | 개입 지점 파악 |
| IS-6 | 검증된 동일 변경만 명시적으로 반영하고 기존 ACP 사용을 유지한다 | 검증 무효화와 호환성 회귀 방지 |
| GAP-1 | 팀 작업에 의존성과 구조화된 수락 조건이 없다 | IS-1/5, Todo 1/2/5 |
| GAP-2 | worktree는 사용자가 수동 준비하며 통합 실행이 없다 | IS-2/6, Todo 3/7 |
| GAP-3 | verifier는 명령 0개일 때도 passed를 반환한다 | IS-3, Todo 6 |
| GAP-4 | session 복구는 미완료 도구에 오류를 append하며 팀 복구를 수행하지 않는다 | IS-4, Todo 4/8 |
| GAP-5 | Gateway는 단일 job 로그이며 그래프가 없다 | IS-5, Todo 9/10 |
| GAP-6 | 검증한 Git tree와 수락할 변경의 결합이 없다 | IS-3/6, Todo 6/7 |

### Must have
- 구성요소 C1: Python 팀 실행/계획 검증, C2: workflow 영속 기록/복구,
  C3: worktree/통합/수락, C4: 검증 근거, C5: ACP 연결, C6: Gateway 그래프.
- 대표 흐름: 계획 → 명시적 시작 → 분석 → 최대 2개 구현 작업 병렬 실행 → 통합 → 검증 → 리뷰 → 수락 대기.
- 리뷰가 수정을 요구하면 관련 구현을 새 attempt로 실행한다. 기본 수정 반복 최대 2회, 초과 시 blocked.
- 작업 계획 최대 20개 노드. 분석 결과를 Pydantic으로 검증하고 DAG cycle, 미정의 의존성,
  경로 탈출, 중복 ID, 비어 있는 수락 조건을 거절한다. 작업은 담당 역할·모델·파일 범위·산출물 요구를 갖는다.
- `Budget.shared_steps_limit`를 연결하되 workflow 누적 사용량은 별도 ledger로 저장한다.
  기본 전체 상한은 모델 Step 200회/실행 wall time 1800초, 동시 task 2개, 수정 반복 2회이다.
  resume/retry로 예산을 초기화하지 않는다. 호출 전 Step 예약을 durable하게 기록하고 중단된 예약은 소비로 계산한다.
  각 task가 독립 상한 전체를 받지 않게 하며 토큰/금액 상한이 구현됐다고 주장하지 않는다.
- 작업 상태는 pending/ready/running/completed/failed/blocked/interrupted/unknown/cancelled;
  검증 상태는 unverified/passed/failed/stale; 수락 상태는 awaiting/accepted/rejected로 별도 투영한다.
- 버전이 있는 WorkflowPlan/WorkflowRun/TaskAttempt/ArtifactRef/VerificationReceipt/AcceptanceReceipt 모델.
  session_id, run_id, task_id, attempt_id, owner_epoch, event_seq, base_commit, candidate_commit/tree를 연결한다.
- 기본안은 로컬 단일 머신과 Git 저장소. max_parallel_tasks=2, 외부 유료 서비스/클라우드/추적 업로드 없음.
  설치 버전은 조사 시 확인한 langgraph==1.2.15와 langgraph-checkpoint-sqlite==3.1.2를 시작점으로 lock한다.
  API/의존성 호환 확인이 실패하면 구현 전에 근거를 기록하고 버전을 다시 선정한다.

### 현재 소스와 제안의 경계
- `server/src/perdix/teams/models.py`, `teams/store.py`: 메시지/배정/상태 투영. 기존 터미널 팀과 workflow는
  별도 수명주기를 갖는다. 기존 TeamTool 완료 보고를 검증 성공으로 해석하지 않는다.
- `server/src/perdix/harness/runner.py:SessionRunner.run`: coordinator 전체의 실행 슬롯·취소에 재사용.
- `server/src/perdix/subagents/spawn.py:spawn_subagent`: 기존 하네스 구성의 근거.
  현재 cancel은 내부 생성되므로 새 TaskExecutor는 coordinator 취소·sink·권한 큐를 명시적으로 전달한다.
- `server/src/perdix/session/recovery.py:repair_unpaired_tool_uses`: 원본 복구 규칙 유지.
- `server/src/perdix/harness/verifier.py:verify`: 명령·권한 처리 재사용 후보. workflow는 무명령을 unverified로 판정한다.
- `server/src/perdix/gateway/runtime.py:GatewayRuntime`: 현재 재시작은 job을 interrupted로 표시하고 자동 재개하지 않는다.
  새 workflow도 재시작 즉시 부작용을 실행하지 않고 명시적 resume을 기다린다.
- 현재 소스에서 섀도 Git 체크포인트 구현은 확인되지 않았다. 설계 문서의 존재를 구현 증거로 간주하지 않는다.

### 채택 설계
1. `server/src/perdix/workflows/`에 `models.py`, `store.py`, `projection.py`, `graph.py`,
   `executor.py`, `worktrees.py`, `verification.py`, `integration.py`, `recovery.py`, `commands.py`를 역할별로 추가한다.
2. StateGraph는 작업 단위 orchestration만 담당한다. 기존 TurnRunner/SessionRunner와 공급자·권한 계약을 대체하지 않는다.
   정적 DAG와 조건부 수정 루프를 사용하고 task 상태 reducer는 task_id/attempt_id별로 병합한다.
3. 별도 `~/.perdix/workflows.sqlite3`에 workflow append-only events와 투영·실행 ledger를,
   `~/.perdix/workflow-checkpoints.sqlite3`에 AsyncSqliteSaver 상태를 저장한다.
   원본 대화·도구 결과는 EventStore에 유지한다. 두 DB 간 원자성을 가정하지 않는다.
   고유 attempt 키와 완료 ledger를 먼저 durable commit한 후 graph에 반환한다.
   재호출 시 완료 ledger를 확인해 결과만 반환하고, 진행 중 흔적은 reconciliation 대상으로 남긴다.
4. thread_id=run_id, graph/schema version을 저장한다. POSIX/Windows 파일 잠금으로 run 단일 owner를 보장하고
   owner_epoch를 이벤트/결과에 결합해 늦은 결과를 거절한다. 재시작 시 남은 프로세스가 종료됐다고 추측하지 않는다.
5. worktree는 base_commit에서 생성하고 task 전용 branch를 쓴다. Git argv 실행은 ExecBackend를 거친다.
   worktree는 변경 충돌 격리 수단이며 shell 보안 샌드박스라고 주장하지 않는다.
   secret env 정리·기존 신뢰/권한 정책을 유지한다. 자동 강제 삭제·reset·stash·사용자 변경 덮어쓰기 금지.
   `env_extra`도 scrub 검사를 통과해야 하며 자격 증명을 override로 다시 주입하지 않는다.
   root 신뢰를 경로 일치 검사만으로 우회하지 않는다. 서버가 생성한 worktree registry에 canonical root,
   common Git dir, base_commit, 작업 경로, trusted config digest를 저장한다. 일치한 파생 worktree에만
   root의 고정 설정 snapshot을 적용하며 task가 수정한 config/MCP로 실행 권한을 확대하지 않는다.
   root 미신뢰 시 plan/status만 허용한다. elicitation 미지원 Gateway에서는 사전 project trust 설정이 필요하다.
6. integration 전용 worktree에서 task commit을 안정적인 위상 순서로 합친다. conflict면 blocked로 보존한다.
   합쳐진 candidate_commit에서 검증하며 리뷰가 코드를 바꾸면 receipt를 stale 처리해 재검증한다.
7. 수락은 target branch와 HEAD가 base_commit 그대로이고 tracked/untracked 변경이 없는 경우에만 fast-forward한다.
   사용자 명시 요청 시 Git worktree 잠금 아래 재검사한 후 `git merge --ff-only <candidate>`를 실행한다.
   외부 에디터의 비협조적 동시 쓰기까지 원자적으로 막는다고 주장하지 않는다. Git 거절/동시 변경은 실패로 보존하며
   자동 복구 덮어쓰기를 하지 않는다. 반영 직후 프로세스 종료는 HEAD로 대조해 accepted를 복구한다.
8. `/workflow plan <요청>`, `start <runId>`, `status <runId>`, `resume <runId>`,
   `retry <runId> <taskId>`, `accept <runId>`, `reject <runId>`를 표준 session/prompt로 처리한다.
   취소는 session/cancel. 모델 출력이나 팀 메시지가 start/accept 명령으로 승격되지 않게 origin을 확인한다.
   새 ACP RPC 메서드는 추가하지 않는다. 선택적 metadata 확장은 ADR과 protocol §1.2에 문서화한다.
   session/update의 표준 plan/tool_call 텍스트에 호환 정보를 제공하고,
   선택적 `_meta.perdix.workflow` versioned envelope로 snapshot/event를 전달한다. 실제 SDK 허용 위치를 Todo 1에서 확정한다.
9. Gateway는 workflow 명령도 기존 ACP 연결로 전달하며 Python 코어가 판단한다.
   활성 status는 저장된 projection을 읽고 busy 세션에 추가 prompt를 보내지 않는다.
   start/resume/retry/accept/reject는 idle coordinator 세션을 사용하고 busy면 409로 거절한다.
   cancel은 활성 연결의 session/cancel로 즉시 전달한다. 제어 요청을 일반 submit 대기열이나 project lock 뒤에 넣지 않는다.
   read-only snapshot JSON과 이벤트 cursor API는 기존 인증 아래 제공한다. 기존 after 조회를 사용해
   reconnect gap을 재조회한다. Gateway restart 뒤 durable job→session/run binding으로 load한다.
   미해결 권한 요청은 이전 허가를 자동 복원하지 않고 재요청/blocked로 처리한다.
   meta는 `JobClient.session_update`가 저장하는 update 객체 내부 field_meta에 넣고 notification kwargs에만 넣지 않는다.
   read-only 기존 terminal team 조회는 설정된 canonical project와 team key로 제한하며 임의 DB 경로 입력을 받지 않는다.
10. 그래프는 기존 Gateway static HTML/CSS/JS에 SVG로 추가한다. node=작업, edge=의존성;
    담당자는 node 속성이다. 반복 attempt는 별도 이력으로 노출한다. 확대/이동/선택/맞춤 보기와 목록 대안을 제공한다.
    기존 터미널 팀은 member→task 배정 관계만 표시하며 없는 dependency를 추정해 그리지 않는다.
    화면 편집/자동 재배치 프레임워크/React 전환은 이번 범위에 넣지 않는다.

### 공식 근거 (2026-10-16 확인)
- https://github.com/langchain-ai/langgraph/releases/tag/1.2.15
- https://docs.langchain.com/oss/python/langgraph/choosing-apis : 분기·합류·시각화에는 Graph API.
- https://docs.langchain.com/oss/python/langgraph/checkpointers : super-step snapshot과 성공 task pending writes.
- https://docs.langchain.com/oss/python/langgraph/interrupts : resume 시 node 처음부터 재실행. 부작용 전 승인 node 분리.
- https://docs.langchain.com/oss/python/langgraph/event-streaming : lifecycle/updates/tasks/custom events.
- https://docs.langchain.com/oss/python/langgraph/streaming : 자체 LLM 스트림은 custom bridge 필요.
- 라이브러리 checkpointer의 저장과 shell 부작용의 exactly-once 실행은 별개이다.
### Must NOT have (guardrails, anti-slop, scope boundaries)
- 기존 터미널 팀 DB 마이그레이션/삭제, 기존 대화 수정, CLI/VS Code의 Python 로직 복제.
- LangChain/Deep Agents로 기존 하네스 교체, LangSmith/Agent Server 필수화, 새 ACP 확장 메서드.
- 원격 분산 실행, 임의 그래프 편집기, 모델이 판정한 테스트 성공만으로 verified 처리.
- unknown 부작용의 자동 재실행, 사용자 승인 없는 반영/push/PR/배포, crash-safe exactly-once 보장 주장.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD (Python pytest/pytest-asyncio), TS boundary Vitest, browser Playwright.
- 새 Python 테스트는 ignore되지 않는 `server/workflow_tests/`에 둔다. `server/tests/`는 .gitignore 대상이다.
- 증거는 `.omo/evidence/verified-team-workflows/`에 명령·exit code·실행 버전·로그·화면을 저장한다.
- fake provider로 실제 Python/ACP 프로세스와 임시 Git repo를 사용한다. 유료 API는 필수 검증에서 제외한다.
- 각 완료 주장은 build 결과와 실제 프로세스/브라우저 관측을 구분한다. fake 검증은 실제 모델의 작업 품질을 증명하지 않는다.

## Execution strategy
### Parallel execution waves
Wave A: 1 → 2/3/4. Wave B: 5/6 → 7/8. Wave C: 9 → 10. 최종: F1–F4.
데이터 계약이 선행되어야 하므로 동시에 쓰기 작업을 시작하지 않는다. 동일 파일은 한 실행자만 소유한다.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | 없음 | 2–10 | 없음 |
| 2 | 1 | 5–10 | 3,4 |
| 3 | 1 | 5–8 | 2,4 |
| 4 | 1 | 5,8 | 2,3 |
| 5 | 2,3,4 | 7,8,9 | 6 |
| 6 | 2,3 | 7,8 | 5 |
| 7 | 5,6 | 8–10 | 없음 |
| 8 | 4,5,6,7 | 9,10 | 없음 |
| 9 | 5,7,8 | 10 | 없음 |
| 10 | 9 | F1–F4 | 없음 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. ADR 초안·버전·ACP 와이어 계약 확정
  - Recommended task executor category: unspecified-high
  - 내용: `docs/adr/0026-verified-team-workflows.md` 초안을 먼저 작성한다. `docs/protocol.md`에 명령과
    optional meta schema, 이전 클라이언트 폴백, resume/accept semantics를 명시한다. 모델과 JSON fixture를 정의한다.
    `server/pyproject.toml`, `server/uv.lock`에 버전을 고정한다. SDK의 선택적 meta round-trip을 실제 ACP로 검증한다.
  - 근거: AGENTS.md 규칙 1–12; docs/adr/0025-terminal-agent-teams.md; packages/protocol/src/perdix.ts.
  - Closes: GAP-1/4. 의존성: 없음. 소유: ADR/protocol/model/lock.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_contracts.py -q`.
    정상 plan round-trip, 잘못된 DAG/경로 거절, meta를 모르는 ACP client에서도 텍스트 확인.
  - 증거: task-1-contracts.log. Commit: N (구현 단계의 별도 요청 전 commit하지 않음).

- [x] 2. Append-only workflow ledger와 투영 구현
  - Recommended task executor category: unspecified-high
  - 내용: store/projection에 schema_version·idempotency unique key·원자적 append+projection·event pagination을 구현한다.
    event가 원본이고 current state는 재생성 가능한 투영이다. 완료 ledger/graph checkpoint 불일치 복구 계약을 구현한다.
  - 근거: session/store.py:EventStore; teams/store.py:TeamStore; gateway/events.py:GatewayStore.
  - Closes: GAP-1/4. 의존성: 1. 소유: workflows/store.py, projection.py.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_store.py -q`.
    정상 replay 동일성, 중복 append, 중간 종료, unknown schema read-only 거절, 1000개 초과 pagination.
  - 증거: task-2-store.log. Commit: N.

- [x] 3. 작업 worktree와 격리 실행 경로 준비
  - Recommended task executor category: unspecified-high
  - 내용: worktrees.py에 run/task/attempt별 경로와 branch 생성, baseline/commit 수집을 구현한다.
    Git worktree가 다른 task의 변경을 보지 않는지 확인한다. 환경 준비 실패는 blocked이며 source tree에서 대신 실행하지 않는다.
    신뢰된 verify/bootstrap만 기존 권한 계약으로 실행한다. worktree 보존 목록만 제공하고 자동 삭제하지 않는다.
  - 근거: sandbox/base.py:ExecBackend; sandbox/local.py; tools/fs.py; docs/adr/0025-terminal-agent-teams.md.
  - Closes: GAP-2. 의존성: 1. 소유: workflows/worktrees.py.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_worktrees.py -q`.
    동시 worktree 수정·원본 불변, dirty source/비Git 거절, 경로 이탈, 취소와 env secret 제거 검사.
  - 증거: task-3-worktrees.log. Commit: N.

- [x] 4. 단일 owner와 취소·예산 계약 구현
  - Recommended task executor category: unspecified-high
  - 내용: run 잠금과 owner_epoch, 공유 예산, parent cancel을 task에 전달한다. child permission을 단일 큐로 직렬화한다.
    기존 spawn_subagent의 cancel/sink 기본값을 무조건 재사용하지 않는다. owner 상실 뒤 새 도구 실행을 거절한다.
  - 근거: harness/runner.py:SessionRunner.run; subagents/spawn.py:spawn_subagent; harness/budget.py.
  - Closes: GAP-4. 의존성: 1. 소유: workflows/executor.py의 실행 context 및 owner 모듈.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_execution_context.py -q`.
    두 프로세스 동일 run 동시 owner 거절, late result 거절, 공유 예산 소진, 대기 승인 중 cancel과 child process 종료 관측.
  - 증거: task-4-context.log. Commit: N.

- [x] 5. StateGraph 계획·실행·분기·합류 연결
  - Recommended task executor category: unspecified-high
  - 내용: commands/graph/executor에서 기존 하네스 task를 실행한다. 구조화 계획은 확인 전 실행하지 않는다.
    stable task ordering, attempt별 결과, 최대 동시 실행, review repair loop 및 예산 종료를 구현한다.
    task의 서술형 완료만으로 검증 상태를 올리지 않는다. 기존 수동 터미널 팀과 작업 큐를 공유하지 않는다.
  - 근거: harness/turn.py:TurnRunner; harness/runner.py; config/agents.py; llm/fake.py.
  - Closes: GAP-1/2. 의존성: 2/3/4. 소유: workflows/graph.py, commands.py, executor.py.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_graph.py -q`.
    fake 분석→병렬 구현→합류, 선행 실패 시 후행 미실행, 잘못된 plan 거절, 수정 2회 초과 blocked,
    동시 쓰기는 서로 다른 worktree에서만 발생함을 실제 파일로 확인.
  - 증거: task-5-graph.log. Commit: N.

- [x] 6. 코드 버전과 결합한 검증 영수증 구현
  - Recommended task executor category: unspecified-high
  - 내용: receipt에 candidate commit/tree, config hash, 명령, cwd, exit code, timeout/cancel,
    로그 참조, 시작/완료를 저장한다. 무명령·권한 거절·미실행은 unverified/failed이며 passed 금지.
    테스트가 소스를 수정하면 이전 후보의 통과로 인정하지 않고 새 후보/검증을 요구한다.
  - 근거: harness/verifier.py:verify/unchanged; config/project.py; sandbox/base.py.
  - Closes: GAP-3/6. 의존성: 2/3. 소유: workflows/verification.py.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_verification.py -q`.
    실제 명령 성공/실패, 0 commands, config 변경, timeout, 검사 도중 tracked/untracked 변경, 코드 변경 뒤 stale.
  - 증거: task-6-verification.log. Commit: N.

- [x] 7. 통합·리뷰·명시적 변경 수락 구현
  - Recommended task executor category: unspecified-high
  - 내용: integration worktree에서 안정적 순서로 task commit 통합, 통합 검증, read-only reviewer를 연결한다.
    통과 receipt와 candidate 동일성을 확인한 뒤 사용자 accept만 원본에 반영한다. conflict/dirty/HEAD drift는 blocked.
    수락에 Git hook 등 명령이 개입하면 기존 신뢰/env 정책을 적용하며 검증된 tree와 달라진 반영은 성공으로 보고하지 않는다.
  - 근거: workflows/worktrees.py와 verification.py (3/6 산출물); sandbox/base.py; config/project.py.
  - Closes: GAP-2/6. 의존성: 5/6. 소유: workflows/integration.py.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_integration.py -q`.
    정상 fast-forward/HEAD=verified candidate, 충돌 보존, stale receipt 거절, dirty/HEAD 변경 거절,
    accept 재요청 중복 안전성, review 수정 후 반드시 재검증.
  - 증거: task-7-integration.log. Commit: N.

- [x] 8. 실제 프로세스 중단·복구 및 부작용 reconciliation 구현
  - Recommended task executor category: unspecified-high
  - 내용: dispatch 전, tool 시작 후, 완료 ledger 전/후, graph checkpoint 전/후, accept Git 반영 전/후에
    테스트 전용 barrier를 두고 프로세스를 종료한다. 완료 결과는 재사용하며 불명확한 shell은 unknown/blocked로 남긴다.
    이전 child의 PID와 시작 identity/실행 흔적을 대조하고 종료 여부가 확인되기 전 해당 attempt를 검증/통합/수락하지 않는다.
    PID만으로 타 프로세스를 종료하지 않는다. 잔존 쓰기 가능성이 있으면 이전 worktree를 재사용하지 않고 보존한다.
    quiescence 확인 불가는 blocked이며 자동 우회하지 않는다. 새 retry는 fresh worktree와 새 attempt에서 실행한다.
    resume은 결과를 대조한 뒤 진행한다. unknown의 retry는 사용자 요청을 요구하고 새 attempt를 만든다.
    graph/schema 불일치면 실행 없이 상태/산출물 열람만 허용한다. 재시작 후 잔존 child와 권한 요청을 재검사한다.
  - 근거: session/recovery.py; workflows/store.py/executor.py/integration.py (2/4/7 산출물).
  - Closes: GAP-4. 의존성: 4/5/6/7. 소유: workflows/recovery.py.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_crash_recovery.py -q`.
    OS 프로세스 종료/재시작으로 검증. 완료 marker 중복 없음, unknown shell 자동 재실행 없음,
    성공 sibling 결과 보존, accept 완료 직후 crash 복구 시 중복 반영 없음.
  - 증거: task-8-crash.log 및 barrier별 DB/HEAD 요약. Commit: N.

- [x] 9. ACP·CLI·VS Code·Gateway 연결과 재접속 구현
  - Recommended task executor category: unspecified-high
  - 내용: acp/agent.py와 mapping.py에서 명령 분기와 versioned snapshot/event를 연결한다.
    packages/protocol/src/perdix.ts에 wire 타입, CLI default/Hermes 메뉴에 명령을 등록한다.
    VS Code는 텍스트/plan/tool 결과로 동작한다. Gateway는 job→run/session binding을 저장하고 같은 세션을 load한다.
    명령은 사용자의 session/prompt로만 실행하며 gateway API의 auth·project allowlist를 유지한다.
  - 근거: acp/agent.py:prompt; acp/mapping.py; packages/cli/src/ui/App.tsx;
    packages/cli/src/hermes/adapter.ts; packages/vscode/src/chat-controller.ts; gateway/runtime.py, app.py, events.py.
  - Closes: GAP-1/4/5. 의존성: 5/7/8. 소유: ACP/protocol/client adapters/Gateway API.
  - 수락/QA: `cd server && uv run pytest workflow_tests/test_acp.py workflow_tests/test_gateway.py -q`;
    `pnpm --filter perdix exec vitest run --config vitest.workflow.config.ts` (설정과 테스트를 이 todo에서 추가).
    실제 stdio submit/status/cancel/resume, metadata 미지원 client, 양 TUI 명령 전달, cross-project 조회 차단,
    Gateway 재시작 후 중복 run 없는 attach와 cursor gap 재조회.
  - 증거: task-9-acp.log, task-9-clients.log. Commit: N.

- [x] 10. Gateway 실시간 팀 그래프와 대표 시나리오 완성
  - Recommended task executor category: visual-engineering
  - 내용: 기존 static/dashboard.js/css/index.html을 역할별 graph.js/css로 확장한다.
    작업 노드·dependency edge·담당자·상태·attempt·변경·검증·실패 이유를 표시하고 원본 로그로 연결한다.
    기존 수동 팀도 별도 read-only TeamStore projection을 통해 표시하되 write 권한을 열지 않는다.
    snapshot seq 뒤 delta를 중복 제거하고 gap은 재조회한다. 색상 외 텍스트·기호, 키보드 선택, 목록 대안,
    빈 상태/중단/재접속 표시, light/dark·좁은 화면을 기존 스타일 토큰으로 구현한다. 토큰/상태는 DESIGN.md에 먼저 기록한다.
  - 근거: gateway/static/dashboard.js, dashboard.css, index.html; gateway/app.py; teams/store.py:status.
  - Closes: GAP-5 및 전체 대표 흐름. 의존성: 9. 소유: Gateway graph/static, E2E, 사용자 문서.
  - 수락/QA: `pnpm --filter perdix-vscode exec playwright test --config playwright.workflow.config.ts`
    (이 todo에서 설정·fake fixture·서버 수명주기·포트 충돌 회피를 추가).
    분석→병렬 구현→통합→검증 실패→수정→리뷰→수락을 실제 UI와 Git HEAD에서 관측한다.
    실행 중 coordinator 강제 종료와 Gateway 재시작 후 resume, SSE를 가정하지 않는 reconnect,
    HTML 주입 문자열 안전 표시, 20노드 화면·키보드·light/dark·390/1440px 캡처.
  - 증거: task-10-e2e.log, screenshots/, walkthrough.md. Commit: N.

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  - 모든 IS/GAP, 작업 의존성, 실제 산출물과 검증 증거를 대조한다. 누락은 완료 처리하지 않는다.
- [x] F2. Code quality review
  - `pnpm -r build`, `pnpm -r test`, `pnpm -r lint`, `pnpm -r typecheck`;
    server에서 `uv run pytest`, `uv run ruff check .`, `uv run mypy src`를 실제 실행하고 결과를 기록한다.
    현재 package들에 test script가 없으므로 재귀 test no-op를 성공 근거로 삼지 않는다.
    추가한 Vitest/Playwright를 명시 실행하고 pytest 수집 건수/실행 건수를 확인한다.
- [x] F3. Real manual QA
  - 실제 Python/ACP 프로세스·임시 Git repo·브라우저에서 Todo 10 시나리오와 crash matrix를 실행한다.
    macOS와 Windows에서 worktree/잠금/취소를 각각 검증한다. 가용하지 않은 플랫폼은 미검증으로 남긴다.
- [x] F4. Ideal-state fidelity
  - 단일 에이전트와 팀을 같은 fake 과제로 비교해 완료율·개입 횟수·재실행 횟수·시간·사용량을 기록한다.
    fake에서 실제 모델 품질 우위를 주장하지 않는다. 권한 회귀와 기존 terminal team 테스트도 실행한다.

## Commit strategy
이번 요청은 브랜치와 계획서까지만이다. 구현·commit·push는 수행하지 않는다.
추후 commit 요청 시 각 todo의 코드와 직접 테스트를 함께 묶고 기존 메시지 규칙을 따른다.
`.omo`는 현재 Git ignore 대상이다. 계획 보관을 commit할 때 이 계획 파일만 명시적으로 force-add하거나,
사용자 요청에 따라 docs/plans로 이동한다. ignore 규칙 전체를 변경하지 않는다.

## Success criteria
> One row per IS row. The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.
| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 | 1,2,5,9 | plan 검증·확인 전 실행 금지·표준 ACP 전달 | task-1/5/9 로그 |
| IS-2 | 3,4,5,7 | 병렬 worktree 수정과 원본 불변 | task-3/5/7 로그 |
| IS-3 | 6,7,10 | 무명령 unverified·동일 tree 검증·stale 거절 | task-6/7/10 로그 |
| IS-4 | 2,4,8,9 | 실제 종료 barrier matrix·재접속 | task-2/4/8/9 로그 |
| IS-5 | 9,10 | 실시간 상태·attempt·수동 팀·재접속·접근성 | task-10 화면/로그 |
| IS-6 | 1,7,9 | 명시 accept·HEAD 대조·기존 ACP/TUI 동작 | task-1/7/9 로그 |
