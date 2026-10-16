"""계획 경계와 실제 ACP stdio의 선택 metadata 호환성을 검증한다."""

import json
import subprocess
import sys
from importlib.metadata import version

import pytest
from acp.schema import SessionNotification
from pydantic import ValidationError

from perdix.workflows.models import (
    AcceptanceReceipt,
    ArtifactRef,
    TaskAttempt,
    VerificationCommandResult,
    VerificationReceipt,
    WorkflowPlan,
    WorkflowRun,
    WorkflowTask,
)


def task(task_id: str = "a", dependencies: tuple[str, ...] = ()) -> WorkflowTask:
    return WorkflowTask(
        task_id=task_id,
        title="구현",
        role="implement",
        model="fake/model",
        paths=("src/example.py",),
        acceptance=("테스트 통과",),
        dependencies=dependencies,
    )


def test_plan_round_trip_and_frozen_contract() -> None:
    plan = WorkflowPlan(objective="검증", tasks=(task(), task("b", ("a",))))
    wire = plan.model_dump_json(by_alias=True)
    assert '"taskId"' in wire and '"sharedStepsLimit":200' in wire
    assert WorkflowPlan.model_validate_json(wire) == plan
    with pytest.raises(ValidationError):
        plan.objective = "변경"


@pytest.mark.parametrize(
    "tasks",
    [
        (task(), task()),
        (task("a", ("missing",)),),
        (task("a", ("b",)), task("b", ("a",))),
        tuple(task(str(i)) for i in range(21)),
        (),
    ],
)
def test_invalid_dag(tasks: tuple[WorkflowTask, ...]) -> None:
    with pytest.raises(ValidationError):
        WorkflowPlan(objective="검증", tasks=tasks)


@pytest.mark.parametrize("path", ["../x", "/tmp/x", "a/../../x", "C:\\x", "a\\..\\x", "\x00"])
def test_path_escape(path: str) -> None:
    with pytest.raises(ValidationError):
        WorkflowTask.model_validate(task().model_dump() | {"paths": (path,)})


@pytest.mark.parametrize("acceptance", [(), ("",), ("  ",)])
def test_empty_acceptance(acceptance: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        WorkflowTask.model_validate(task().model_dump() | {"acceptance": acceptance})


def test_passed_requires_successful_commands() -> None:
    receipt = VerificationReceipt(
        receipt_id="v",
        run_id="r",
        candidate_commit="c",
        candidate_tree="t",
        config_digest="d",
    )
    with pytest.raises(ValidationError):
        VerificationReceipt.model_validate(receipt.model_dump() | {"status": "passed"})
    commands = (VerificationCommandResult(command="pytest", exit_code=0),)
    assert (
        VerificationReceipt.model_validate(
            receipt.model_dump() | {"status": "passed", "commands": commands},
        ).status
        == "passed"
    )


@pytest.mark.parametrize("task_id", ["Upper", "has space", "../x", "a" * 129, ""])
def test_task_id_matches_worktree_contract(task_id: str) -> None:
    with pytest.raises(ValidationError):
        task(task_id)


def test_all_contracts_round_trip() -> None:
    plan = WorkflowPlan(objective="검증", tasks=(task(),))
    run = WorkflowRun(
        run_id="r",
        session_id="s",
        project_root="/repo",
        base_commit="b",
        target_branch="main",
        plan=plan,
    )
    artifact = ArtifactRef(artifact_id="a", path="results/log.txt")
    attempt = TaskAttempt(
        run_id="r", task_id="a", attempt_id="a1", owner_epoch=1, artifacts=(artifact,)
    )
    verification = VerificationReceipt(
        receipt_id="v", run_id="r", candidate_commit="c", candidate_tree="t", config_digest="d"
    )
    acceptance = AcceptanceReceipt(
        receipt_id="ac",
        run_id="r",
        verification_receipt_id="v",
        base_commit="b",
        candidate_commit="c",
        candidate_tree="t",
        target_branch="main",
    )
    for contract in (run, artifact, attempt, verification, acceptance):
        assert (
            type(contract).model_validate_json(contract.model_dump_json(by_alias=True)) == contract
        )
    assert run.status == "pending"
    assert run.verification_status == "unverified"
    assert run.acceptance_status == "awaiting"


def test_run_failure_reason_additive_wire_contract() -> None:
    legacy = {
        "runId": "r",
        "sessionId": "s",
        "projectRoot": "/repo",
        "baseCommit": "b",
        "targetBranch": "main",
        "plan": WorkflowPlan(objective="검증", tasks=(task(),)).model_dump(by_alias=True),
    }
    run = WorkflowRun.model_validate(legacy)
    assert run.failure_reason is None
    blocked = WorkflowRun.model_validate(
        legacy
        | {
            "status": "blocked",
            "failureReason": "리뷰 결과를 해석할 수 없습니다",
        }
    )
    wire = blocked.model_dump_json(by_alias=True)
    assert '"failureReason"' in wire and '"failure_reason"' not in wire
    assert WorkflowRun.model_validate_json(wire) == blocked
    assert blocked.failure_reason == "리뷰 결과를 해석할 수 없습니다"
    assert blocked.schema_version == run.schema_version == 1


def test_pinned_dependencies() -> None:
    assert version("langgraph") == "1.2.15"
    assert version("langgraph-checkpoint-sqlite") == "3.1.2"


ACP_AGENT = r"""
import asyncio
from acp import Agent, run_agent
from acp.helpers import update_agent_message_text
from acp.schema import InitializeResponse, PromptResponse
class ContractAgent(Agent):
    def on_connect(self, conn):
        self.conn = conn
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocol_version=protocol_version)
    async def prompt(self, session_id, prompt, **kwargs):
        update = update_agent_message_text("workflow r: pending")
        update.field_meta = {"perdix": {"workflow": {
            "schemaVersion": 1, "kind": "snapshot", "runId": "r", "seq": 1,
            "payload": {"status": "pending"}}}}
        await self.conn.session_update(session_id=session_id, update=update)
        return PromptResponse(stop_reason="end_turn")
asyncio.run(run_agent(ContractAgent()))
"""


def test_actual_acp_stdio_metadata_and_plain_client() -> None:
    with subprocess.Popen(
        [sys.executable, "-u", "-c", ACP_AGENT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": 1, "clientCapabilities": {}},
                }
            )
            + "\n"
        )
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["id"] == 1
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session/prompt",
                    "params": {"sessionId": "s", "prompt": [{"type": "text", "text": "status"}]},
                }
            )
            + "\n"
        )
        process.stdin.flush()
        raw = json.loads(process.stdout.readline())
        assert raw["method"] == "session/update"
        update = raw["params"]["update"]
        assert update["_meta"]["perdix"]["workflow"]["runId"] == "r"
        parsed = SessionNotification.model_validate(raw["params"])
        assert parsed.update.field_meta == update["_meta"]
        # metadata를 사용하지 않는 client는 표준 콘텐츠만으로 상태를 표시한다.
        assert update["sessionUpdate"] == "agent_message_chunk"
        assert update["content"]["text"] == "workflow r: pending"
        assert json.loads(process.stdout.readline())["result"]["stopReason"] == "end_turn"
        process.stdin.close()
        assert process.wait(timeout=10) == 0
