"""목표 루프 상태. 매 전이는 새 이벤트로 저장하고 이전 상태는 수정하지 않는다."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from perdix.config.project import ProjectConfig

LoopStatus = Literal["running", "passed", "stopped"]
STATUS_LABELS: Final = {"running": "실행 중", "passed": "검증 통과", "stopped": "중단"}
REASON_LABELS: Final = {
    "started": "시작",
    "executing": "작업 수행",
    "resumed": "재개",
    "verified": "완료 기준 통과",
    "interrupted": "실행 중 연결 종료",
    "cancelled": "사용자 취소",
    "time_limit": "시간 한도",
    "step_limit": "Step 한도",
    "iteration_limit": "반복 한도",
    "no_progress": "같은 검증 실패 반복",
    "config_changed": "프로젝트 설정 변경",
    "permission_denied": "실행 권한 거부",
    "provider_error": "모델 연결 오류",
    "verification_error": "검증 실행 오류",
    "max_tokens": "모델 출력 한도",
    "refusal": "모델 요청 거절",
}


class LoopState(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    goal: str = Field(min_length=1, max_length=100_000)
    project_path: str
    config: ProjectConfig
    config_hash: str
    status: LoopStatus = "running"
    reason: str = "started"
    iteration: int = 0
    steps_used: int = 0
    elapsed: float = 0
    stagnant_iterations: int = 0
    failure_hash: str = ""
    feedback: str = ""

    def describe(self, active: bool) -> str:
        status = self.status
        reason = self.reason
        if status == "running" and not active:
            status, reason = "stopped", "interrupted"
        return (
            f"\n[목표 루프] {STATUS_LABELS[status]} · {REASON_LABELS.get(reason, reason)}\n"
            f"목표: {self.goal}\n"
            f"반복 {self.iteration}/{self.config.loop.max_iterations} · "
            f"Step {self.steps_used}/{self.config.loop.max_steps} · {self.elapsed:.1f}초\n"
        )
