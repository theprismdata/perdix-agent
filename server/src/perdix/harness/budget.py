"""Turn 예산 (harness-loop.md §5)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BudgetExceeded(Exception):
    """Turn 예산 초과. 하네스는 `stopReason: max_turn_requests`로 끝낸다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Budget(BaseModel):
    max_steps: int = 200
    max_verify_iterations: int = 3
    # 모델 컨텍스트 창 대비 압축 시작 비율(압축은 M4).
    context_ratio: float = 0.75
    # 목표 루프에서만 설정한다. 부모·자식과 여러 Turn이 같은 객체를 소비한다.
    shared_steps_limit: int | None = Field(default=None, ge=1)
    steps_used: int = 0
    permission_denied: bool = False

    def check_steps(self, steps: int) -> None:
        """이미 실행한 Step 수가 상한에 닿았으면 BudgetExceeded."""
        if steps >= self.max_steps:
            raise BudgetExceeded(f"step budget exhausted ({self.max_steps} steps)")
        if self.shared_steps_limit is not None and self.steps_used >= self.shared_steps_limit:
            raise BudgetExceeded(f"shared step budget exhausted ({self.shared_steps_limit} steps)")

    def consume_step(self) -> None:
        self.check_steps(0)
        self.steps_used += 1
