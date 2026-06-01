"""Focused scenarios for the 3.2 ephemeral workspace live tier."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)


def _plan(action_id: str, action_spec: str, summary_hint: str) -> dict[str, Any]:
    return {
        "tasks": [{"id": action_id, "agent_name": "executor", "needs": []}],
        "task_specs": {action_id: action_spec},
        "reducers": [
            {
                "id": "reduce",
                "needs": [action_id],
                "prompt": (
                    f"Confirm ephemeral-workspace probe '{action_id}' wrote its "
                    f"summary to {summary_hint} and that per-call overlay "
                    "lifecycle, OCC publish behavior, and runtime cleanup "
                    "matched the 3.2 live E2E contract."
                ),
            }
        ],
    }


SAME_PATH_CONFLICT_WRITER_COUNT = 4


def _same_path_conflict_plan() -> dict[str, Any]:
    writer_ids = [
        f"same_path_conflict_writer_{index}" for index in range(SAME_PATH_CONFLICT_WRITER_COUNT)
    ]
    tasks = [
        {"id": "same_path_conflict_seed", "agent_name": "executor", "needs": []},
        *(
            {
                "id": writer_id,
                "agent_name": "executor",
                "needs": ["same_path_conflict_seed"],
            }
            for writer_id in writer_ids
        ),
        {
            "id": "same_path_conflict_reconcile",
            "agent_name": "executor",
            "needs": writer_ids,
        },
    ]
    task_specs = {
        "same_path_conflict_seed": (
            "ACTION ephemeral_same_path_conflict_seed. Initialize the shared "
            "same-path target for the conflict fan-out."
        ),
        "same_path_conflict_reconcile": (
            "ACTION ephemeral_same_path_conflict_reconcile. Read all first-wave "
            "fragments, retry failed writers after fresh reads, verify final "
            "content, and write summary.json."
        ),
    }
    for index, writer_id in enumerate(writer_ids):
        task_specs[writer_id] = (
            f"ACTION ephemeral_same_path_conflict_writer index={index}. Race the "
            "shared same-path target and write a first-wave fragment."
        )
    return {
        "tasks": tasks,
        "task_specs": task_specs,
        "reducers": [
            {
                "id": "reduce",
                "needs": [
                    "same_path_conflict_seed",
                    *writer_ids,
                    "same_path_conflict_reconcile",
                ],
                "prompt": (
                    "Confirm the seed initialized the shared same-path target, "
                    "the four writers produced at least one success plus at "
                    "least one typed conflict or rejected write, the reconcile "
                    "retried failed writers after fresh reads, and the final "
                    "summary uses task_center_runner.ephemeral_workspace.v1."
                ),
            }
        ],
    }


class _EphemeralWorkspaceScenarioBase(ScenarioBase):
    action_id: str = ""
    action_spec: str = ""
    summary_path_hint: str = ""

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_planner_outcome,
            _plan(self.action_id, self.action_spec, self.summary_path_hint),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        instruction = ctx.instruction or ctx.prompt or ""
        if f"ACTION {self.action_id}" in instruction:
            return (self.action_id,)
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": f"{self.action_id} ephemeral-workspace scenario completed.",
            },
        )


def _scenario(
    class_name: str,
    *,
    action_id: str,
    action_spec: str,
    summary_path_hint: str,
) -> type[_EphemeralWorkspaceScenarioBase]:
    """Build a data-only ephemeral-workspace scenario leaf.

    ``name`` is derived as ``f"sandbox.{action_id}"`` — the invariant every
    former hand-written leaf class satisfied.
    """
    return type(
        class_name,
        (_EphemeralWorkspaceScenarioBase,),
        {
            "name": f"sandbox.{action_id}",
            "action_id": action_id,
            "action_spec": action_spec,
            "summary_path_hint": summary_path_hint,
        },
    )


EphemeralWorkspaceAllVerbs = _scenario(
    "EphemeralWorkspaceAllVerbs",
    action_id="ephemeral_workspace_all_verbs",
    action_spec=(
        "ACTION ephemeral_workspace_all_verbs. Run write_file, read_file, "
        "edit_file, grep, glob, and shell against /testbed/eph_case; inspect "
        "manifest versions and per-call runtime cleanup after every call."
    ),
    summary_path_hint="/testbed/.ephemeralos/sweevo-mock/ephemeral_workspace/all_verbs/summary.json",
)
EphemeralWorkspaceConcurrentWrites = _scenario(
    "EphemeralWorkspaceConcurrentWrites",
    action_id="ephemeral_workspace_concurrent_writes",
    action_spec=(
        "ACTION ephemeral_workspace_concurrent_writes. Launch 8 concurrent "
        "write_file calls to disjoint paths and 2 concurrent shell captures; read "
        "all outputs and assert typed/api versus overlay source tags."
    ),
    summary_path_hint="/testbed/.ephemeralos/sweevo-mock/ephemeral_workspace/concurrent_writes/summary.json",
)


class EphemeralWorkspaceSamePathConflict(_EphemeralWorkspaceScenarioBase):
    """Same-path conflict scenario promoted to a real generator fan-out."""

    name = "sandbox.ephemeral_workspace_same_path_conflict"
    action_id = "ephemeral_workspace_same_path_conflict"
    action_spec = (
        "ACTION ephemeral_workspace_same_path_conflict. Launch four same-path "
        "writes, require typed OCC conflicts, retry failed writes after fresh "
        "reads, and verify the final content."
    )
    summary_path_hint = (
        "/testbed/.ephemeralos/sweevo-mock/ephemeral_workspace/same_path_conflict/summary.json"
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _same_path_conflict_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        instruction = ctx.instruction or ctx.prompt or ""
        if "ACTION ephemeral_same_path_conflict_seed" in instruction:
            return ("ephemeral_same_path_conflict_seed",)
        if "ACTION ephemeral_same_path_conflict_reconcile" in instruction:
            return ("ephemeral_same_path_conflict_reconcile",)
        marker = "ACTION ephemeral_same_path_conflict_writer"
        if marker in instruction:
            return (f"ephemeral_same_path_conflict_writer:{_writer_index(instruction)}",)
        return ()


def _writer_index(instruction: str) -> int:
    marker = "index="
    if marker not in instruction:
        raise RuntimeError(f"missing same-path writer index in: {instruction!r}")
    raw = instruction.split(marker, 1)[1].split(".", 1)[0].strip()
    return int(raw)


EphemeralWorkspacePolicy = _scenario(
    "EphemeralWorkspacePolicy",
    action_id="ephemeral_workspace_policy",
    action_spec=(
        "ACTION ephemeral_workspace_policy. Read /etc/hosts, write /tmp, and "
        "attempt denied writes to /etc, /proc, /sys, and /boot through the same "
        "ephemeral request pipeline."
    ),
    summary_path_hint="/testbed/.ephemeralos/sweevo-mock/ephemeral_workspace/policy/summary.json",
)
EphemeralWorkspaceCancellation = _scenario(
    "EphemeralWorkspaceCancellation",
    action_id="ephemeral_workspace_cancellation",
    action_spec=(
        "ACTION ephemeral_workspace_cancellation. Cancel a long shell that is "
        "writing /testbed/eph_case/partial.bin, then verify no partial publish "
        "and a healthy foreground read/write cycle."
    ),
    summary_path_hint="/testbed/.ephemeralos/sweevo-mock/ephemeral_workspace/cancellation/summary.json",
)
EphemeralWorkspaceO1Disk = _scenario(
    "EphemeralWorkspaceO1Disk",
    action_id="ephemeral_workspace_o1_disk",
    action_spec=(
        "ACTION ephemeral_workspace_o1_disk. Run 100 sequential small "
        "write/edit/read calls, sample runtime disk after every 10 calls, and "
        "assert manifest advancement matches mutation count."
    ),
    summary_path_hint="/testbed/.ephemeralos/sweevo-mock/ephemeral_workspace/o1_disk/summary.json",
)

__all__ = [
    "EphemeralWorkspaceAllVerbs",
    "EphemeralWorkspaceCancellation",
    "EphemeralWorkspaceConcurrentWrites",
    "EphemeralWorkspaceO1Disk",
    "EphemeralWorkspacePolicy",
    "EphemeralWorkspaceSamePathConflict",
]
