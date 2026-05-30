"""Focused scenarios for the 3.2 ephemeral workspace live tier."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)


def _plan(action_id: str, action_spec: str, summary_hint: str) -> dict[str, Any]:
    return {
        "plan_spec": (
            f"Single-task plan that drives the {action_id} ephemeral-workspace "
            "probe through the mock-agent harness."
        ),
        "evaluation_criteria": [
            f"Ephemeral-workspace probe '{action_id}' wrote its summary to {summary_hint}.",
            "Per-call overlay lifecycle, OCC publish behavior, and runtime "
            "cleanup matched the 3.2 live E2E contract.",
        ],
        "tasks": [{"id": action_id, "agent_name": "executor", "deps": []}],
        "task_specs": {action_id: action_spec},
    }


SAME_PATH_CONFLICT_WRITER_COUNT = 4


def _same_path_conflict_plan() -> dict[str, Any]:
    writer_ids = [
        f"same_path_conflict_writer_{index}"
        for index in range(SAME_PATH_CONFLICT_WRITER_COUNT)
    ]
    tasks = [
        {"id": "same_path_conflict_seed", "agent_name": "executor", "deps": []},
        *(
            {
                "id": writer_id,
                "agent_name": "executor",
                "deps": ["same_path_conflict_seed"],
            }
            for writer_id in writer_ids
        ),
        {
            "id": "same_path_conflict_reconcile",
            "agent_name": "executor",
            "deps": writer_ids,
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
        "plan_spec": (
            "Seed one shared file, launch four executor writers concurrently "
            "against the same path, then reconcile fragments into the existing "
            "ephemeral-workspace same-path summary contract."
        ),
        "evaluation_criteria": [
            "The seed task initializes the shared same-path target.",
            "Four writer generators launch after the seed and produce at least "
            "one success plus at least one typed conflict or rejected write.",
            "The reconcile task retries failed writers after fresh reads.",
            "The final summary uses task_center_runner.ephemeral_workspace.v1.",
        ],
        "tasks": tasks,
        "task_specs": task_specs,
    }


class _EphemeralWorkspaceScenarioBase(ScenarioBase):

    action_id: str = ""
    action_spec: str = ""
    summary_path_hint: str = ""

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_plan_closes_goal,
            _plan(self.action_id, self.action_spec, self.summary_path_hint),
        )

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ctx.prompt or ""
        if f"ACTION {self.action_id}" in context_message:
            return (self.action_id,)
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": f"{self.action_id} ephemeral-workspace scenario completed.",
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
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
        "/testbed/.ephemeralos/sweevo-mock/ephemeral_workspace/"
        "same_path_conflict/summary.json"
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _same_path_conflict_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ctx.prompt or ""
        if "ACTION ephemeral_same_path_conflict_seed" in context_message:
            return ("ephemeral_same_path_conflict_seed",)
        if "ACTION ephemeral_same_path_conflict_reconcile" in context_message:
            return ("ephemeral_same_path_conflict_reconcile",)
        marker = "ACTION ephemeral_same_path_conflict_writer"
        if marker in context_message:
            return (
                f"ephemeral_same_path_conflict_writer:{_writer_index(context_message)}",
            )
        return ()


def _writer_index(context_message: str) -> int:
    marker = "index="
    if marker not in context_message:
        raise RuntimeError(f"missing same-path writer index in: {context_message!r}")
    raw = context_message.split(marker, 1)[1].split(".", 1)[0].strip()
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
