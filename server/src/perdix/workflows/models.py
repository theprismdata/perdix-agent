"""영속 기록과 ACP wire에서 공유하는 버전 1 계약."""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

Nonempty = Annotated[str, Field(min_length=1, pattern=r"\S")]
TaskStatus = Literal[
    "pending",
    "ready",
    "running",
    "completed",
    "failed",
    "blocked",
    "interrupted",
    "unknown",
    "cancelled",
]
TaskRole = Literal["analyze", "implement", "review"]
VerificationStatus = Literal["unverified", "passed", "failed", "stale"]
AcceptanceStatus = Literal["awaiting", "accepted", "rejected"]


class WorkflowModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
    )
    schema_version: Literal[1] = 1


class WorkflowTask(WorkflowModel):
    task_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")]
    title: Nonempty
    role: TaskRole
    model: Nonempty
    paths: tuple[Nonempty, ...] = Field(min_length=1)
    acceptance: tuple[Nonempty, ...] = Field(min_length=1)
    dependencies: tuple[Nonempty, ...] = ()
    artifact_requirements: tuple[Nonempty, ...] = ()

    @field_validator("paths")
    @classmethod
    def relative_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        for path in paths:
            posix, windows = PurePosixPath(path), PureWindowsPath(path)
            if (
                posix.is_absolute()
                or windows.drive
                or windows.root
                or ".." in posix.parts
                or ".." in windows.parts
                or "\x00" in path
            ):
                raise PydanticCustomError(
                    "path_escape", "작업 경로는 프로젝트 내부 상대 경로여야 합니다"
                )
        return paths


class WorkflowPlan(WorkflowModel):
    objective: Nonempty
    tasks: tuple[WorkflowTask, ...] = Field(min_length=1, max_length=20)
    max_parallel_tasks: int = Field(default=2, ge=1, le=2)
    max_revision_rounds: int = Field(default=2, ge=0)
    shared_steps_limit: int = Field(default=200, ge=1)
    wall_time_limit_seconds: float = Field(default=1800, gt=0)

    @model_validator(mode="after")
    def valid_dag(self) -> Self:
        ids = {task.task_id for task in self.tasks}
        if len(ids) != len(self.tasks):
            raise PydanticCustomError("duplicate_task", "작업 ID가 중복되었습니다")
        if any(set(task.dependencies) - ids for task in self.tasks):
            raise PydanticCustomError("missing_dependency", "정의되지 않은 의존성입니다")
        try:
            tuple(TopologicalSorter({t.task_id: t.dependencies for t in self.tasks}).static_order())
        except CycleError as error:
            raise PydanticCustomError(
                "dependency_cycle", "작업 의존성에 순환이 있습니다"
            ) from error
        return self


class WorkflowRun(WorkflowModel):
    run_id: Nonempty
    session_id: Nonempty
    project_root: Nonempty
    base_commit: Nonempty
    target_branch: Nonempty
    plan: WorkflowPlan
    graph_version: int = Field(default=1, ge=1)
    owner_epoch: int = Field(default=0, ge=0)
    event_seq: int = Field(default=0, ge=0)
    status: TaskStatus = "pending"
    verification_status: VerificationStatus = "unverified"
    acceptance_status: AcceptanceStatus = "awaiting"
    candidate_commit: str | None = None
    candidate_tree: str | None = None
    failure_reason: str | None = None


class ArtifactRef(WorkflowModel):
    artifact_id: Nonempty
    path: Nonempty
    kind: Nonempty = "file"
    sha256: str | None = None
    description: str = ""


class TaskAttempt(WorkflowModel):
    run_id: Nonempty
    task_id: Nonempty
    attempt_id: Nonempty
    owner_epoch: int = Field(ge=0)
    session_id: str | None = None
    event_seq: int = Field(default=0, ge=0)
    status: TaskStatus = "pending"
    worktree_path: str | None = None
    base_commit: str | None = None
    candidate_commit: str | None = None
    candidate_tree: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    failure_reason: str | None = None


class VerificationCommandResult(WorkflowModel):
    command: Nonempty
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    duration_seconds: float = Field(default=0, ge=0)
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[ArtifactRef, ...] = ()


class VerificationReceipt(WorkflowModel):
    receipt_id: Nonempty
    run_id: Nonempty
    candidate_commit: Nonempty
    candidate_tree: Nonempty
    config_digest: Nonempty
    owner_epoch: int = Field(default=0, ge=0)
    event_seq: int = Field(default=0, ge=0)
    status: VerificationStatus = "unverified"
    commands: tuple[VerificationCommandResult, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def passed_has_evidence(self) -> Self:
        if self.status == "passed" and (
            not self.commands
            or any(c.exit_code != 0 or c.timed_out or c.cancelled for c in self.commands)
        ):
            raise PydanticCustomError("invalid_pass", "통과에는 성공한 검증 명령이 필요합니다")
        return self


class AcceptanceReceipt(WorkflowModel):
    receipt_id: Nonempty
    run_id: Nonempty
    verification_receipt_id: Nonempty
    base_commit: Nonempty
    candidate_commit: Nonempty
    candidate_tree: Nonempty
    target_branch: Nonempty
    status: AcceptanceStatus = "awaiting"
    owner_epoch: int = Field(default=0, ge=0)
    event_seq: int = Field(default=0, ge=0)
    accepted_commit: str | None = None
