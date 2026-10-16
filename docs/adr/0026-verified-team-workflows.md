# ADR 0026: 검증 가능한 팀 워크플로우

상태: 채택 (구현 진행)

## 결정

기존 Python 하네스 위에 작업 DAG orchestration만 LangGraph로 추가한다.
기존 terminal team과 대화 이벤트는 유지하고 workflow 이벤트는 별도 append-only ledger에 둔다.
계획·실행·attempt·artifact·검증·수락은 버전 1의 frozen Pydantic 계약을 사용한다.
내부 snake_case와 wire camelCase는 alias로 연결한다. 계획은 최대 20개 작업으로 제한하며
중복 ID, 미정의 의존성, 순환, 경로 탈출과 빈 수락 조건을 거절한다.

완료 상태, 검증 상태, 수락 상태는 서로 독립이다. 검증 receipt는 candidate commit/tree,
설정 digest, 명령 결과와 artifact를 묶는다. 수락은 사용자의 명시 요청에서만 진행한다.
Git worktree는 충돌 격리이며 보안 sandbox가 아니다. unknown 작업은 자동 재실행하지 않는다.

새 ACP RPC는 추가하지 않는다. `/workflow`는 표준 session/prompt, 취소는 session/cancel이다.
선택적 envelope는 notification 외곽이 아니라 `params.update._meta.perdix.workflow`에 둔다.
Python SDK에서는 update 객체의 `field_meta`로 설정한다. 일반 client는 표준 텍스트를 표시한다.

## 의존성

PyPI 공식 배포를 확인한 `langgraph==1.2.15`, `langgraph-checkpoint-sqlite==3.1.2`를 pin한다.
https://pypi.org/pypi/langgraph/1.2.15/json
https://pypi.org/pypi/langgraph-checkpoint-sqlite/3.1.2/json
LangSmith 업로드나 외부 Agent Server는 요구하지 않는다.

## 한계와 검증

두 DB와 외부 shell의 원자성을 가정하지 않는다. 실행 예약과 완료 ledger를 먼저 기록하고
재시작 시 명시 resume 및 reconciliation을 요구한다. 계약 테스트는 잘못된 DAG/경로,
wire round-trip 및 실제 stdio subprocess의 SDK metadata/일반 텍스트 전달을 검증한다.

`WorkflowRun.failure_reason`(wire `failureReason`)은 실행 전체의 차단 사유를 보존하는
선택 필드다. 생략된 기존 기록은 null로 읽으므로 additive 확장이며 schemaVersion 1을 유지한다.
