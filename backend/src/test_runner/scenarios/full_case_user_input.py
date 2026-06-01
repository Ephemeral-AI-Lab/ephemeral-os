"""Dynamic full-case scenario driven by the rendered SWE-EVO user input."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome

from test_runner.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)
from test_runner.scenarios._scenario_helpers import (
    instruction_field,
    is_entry_origin_workflow,
    is_recursive_workflow,
)
from test_runner.scenarios.user_input import (
    UserInputPlan,
    WorkPackage,
    build_user_input_plan,
)


class FullCaseUserInput(ScenarioBase):
    """Exercise user-input parsing, dynamic DAGs, gate reducers, and recursion."""

    name = "full_case_user_input"

    def __init__(self) -> None:
        self._user_input_plan: UserInputPlan | None = None
        self._entry_prompt: str = ""
        self._recursive_package_id: str | None = None

    @property
    def requirement_ledger(self) -> list[dict[str, Any]]:
        plan = self._user_input_plan
        if plan is None:
            return []
        return [asdict(item) for item in plan.requirements]

    @property
    def package_plan(self) -> list[dict[str, Any]]:
        plan = self._user_input_plan
        if plan is None:
            return []
        return [asdict(package) for package in plan.packages]

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if is_recursive_workflow(ctx):
            return self._recursive_planner_response(ctx)
        return self._entry_origin_planner_response(ctx)

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        instruction = ctx.instruction or ctx.prompt or ""
        if "ACTION inspect_user_input" in instruction:
            return ("inspect_user_input",)
        if "ACTION request_recursive_workflow" in instruction:
            package_id = (
                instruction_field(instruction, "package")
                or self._recursive_package_id
                or ""
            )
            return (f"request_recursive_workflow:{package_id}",)
        if "ACTION execute_package" in instruction:
            package_id = instruction_field(instruction, "package") or "unknown"
            return (f"execute_package:{package_id}",)
        if "ACTION final_reconciliation" in instruction:
            return ("final_reconciliation",)
        if "ACTION recursive_" in instruction:
            return ("recursive_step",)
        return ("execute_package:generic",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if self._should_fail_reducer(ctx):
            return ToolCallSpec(
                submit_reducer_outcome,
                {
                    "status": "failed",
                    "outcome": (
                        "Reducer rejected the attempt: missing retry-only "
                        "evidence on the gated checkpoint."
                    ),
                },
            )
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": "Reducer accepted the gated executor-task evidence.",
            },
        )

    def recursive_handoff_goal(self, ctx: ScenarioContext) -> str | None:
        instruction = ctx.instruction or ""
        package_id = instruction_field(instruction, "package") or self._recursive_package_id
        if not package_id:
            return None
        plan = self._ensure_user_input_plan(ctx)
        package = next(
            (item for item in plan.packages if item.id == package_id),
            None,
        )
        if package is None:
            return f"Resolve oversized SWE-EVO package {package_id}."
        requirement_ids = ", ".join(package.item_ids[:12])
        return (
            "Resolve oversized SWE-EVO release package "
            f"{package.id}: {package.title}. "
            f"Representative requirements: {requirement_ids}."
        )

    def _entry_origin_planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        iteration = ctx.iteration
        attempt = ctx.attempt
        self._ensure_user_input_plan(ctx)
        if iteration.sequence_no == 1 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(submit_planner_outcome, _inventory_plan(kind="completes"))
        if iteration.sequence_no == 1:
            return ToolCallSpec(
                submit_planner_outcome,
                _inventory_plan(
                    kind="defers",
                    deferred_goal_for_next_iteration=(
                        "Execute the dynamic package DAG with gated checkpoints "
                        "and recursive workflow handling."
                    ),
                ),
            )
        if iteration.sequence_no == 2:
            args = self._implementation_plan(ctx)
            return ToolCallSpec(submit_planner_outcome, args)
        return ToolCallSpec(submit_planner_outcome, self._final_reconciliation_plan(ctx))

    def _recursive_planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(submit_planner_outcome, _recursive_full_only_plan())

    def _implementation_plan(self, ctx: ScenarioContext) -> dict[str, Any]:
        plan = self._ensure_user_input_plan(ctx)
        packages = tuple(plan.packages)
        recursive = next((pkg for pkg in packages if pkg.recursive_candidate), None)
        regular = tuple(pkg for pkg in packages if not pkg.recursive_candidate)
        if recursive is None and regular:
            recursive = regular[-1]
            regular = regular[:-1]
        self._recursive_package_id = recursive.id if recursive else None

        tasks: list[dict[str, Any]] = []
        task_specs: dict[str, str] = {}
        previous_guard: str | None = None
        for wave_no, wave in enumerate(_chunked(regular, 8), start=1):
            local_ids: list[str] = []
            needs = [previous_guard] if previous_guard else []
            for package in wave:
                local_id = f"exec_{package.id}"
                local_ids.append(local_id)
                tasks.append({"id": local_id, "agent_name": "executor", "needs": needs})
                task_specs[local_id] = _package_task_spec(package, wave_no)
            guard_id = f"verify_wave_{wave_no}"
            tasks.append({"id": guard_id, "agent_name": "executor", "needs": local_ids})
            task_specs[guard_id] = (
                f"VERIFY checkpoint=wave_{wave_no} wave={wave_no} dependency_count={len(local_ids)}"
            )
            previous_guard = guard_id

        final_needs: list[str] = [previous_guard] if previous_guard else []
        if recursive is not None:
            recursive_needs = [previous_guard] if previous_guard else []
            delegate_id = f"delegate_{recursive.id}"
            tasks.append({"id": delegate_id, "agent_name": "executor", "needs": recursive_needs})
            task_specs[delegate_id] = (
                f"ACTION request_recursive_workflow package={recursive.id} risk={recursive.risk}"
            )
            recursive_guard = "verify_recursive_return"
            tasks.append(
                {
                    "id": recursive_guard,
                    "agent_name": "executor",
                    "needs": [delegate_id],
                }
            )
            task_specs[recursive_guard] = "VERIFY checkpoint=recursive_return dependency_count=1"
            final_needs.append(recursive_guard)

        final_guard = "verify_final_pre_reduce"
        tasks.append({"id": final_guard, "agent_name": "executor", "needs": final_needs})
        task_specs[final_guard] = (
            f"VERIFY checkpoint=final_pre_reduce dependency_count={len(final_needs)}"
        )
        return {
            "tasks": tasks,
            "task_specs": task_specs,
            "reducers": [
                {
                    "id": "reduce",
                    "needs": [task["id"] for task in tasks],
                    "prompt": (
                        "Every generated executor wave is guarded; at least one "
                        "guard depends on multiple executor tasks; the recursive "
                        "package close report is available before the parent "
                        "guard."
                    ),
                }
            ],
            "deferred_goal_for_next_iteration": (
                "Run final release-bundle reconciliation after package evidence "
                "and recursive workflow output are available."
            ),
        }

    def _final_reconciliation_plan(self, ctx: ScenarioContext) -> dict[str, Any]:
        plan = self._ensure_user_input_plan(ctx)
        high_risk_count = sum(1 for item in plan.requirements if item.risk == "high")
        tasks = [
            {"id": "final_coverage_ledger", "agent_name": "executor", "needs": []},
            {
                "id": "final_readback_probe",
                "agent_name": "executor",
                "needs": ["final_coverage_ledger"],
            },
            {
                "id": "final_release_guard",
                "agent_name": "executor",
                "needs": ["final_coverage_ledger", "final_readback_probe"],
            },
        ]
        return {
            "tasks": tasks,
            "task_specs": {
                "final_coverage_ledger": (
                    f"ACTION final_reconciliation stage=coverage high_risk_count={high_risk_count}"
                ),
                "final_readback_probe": "ACTION final_reconciliation stage=readback",
                "final_release_guard": ("VERIFY checkpoint=final_release dependency_count=2"),
            },
            "reducers": [
                {
                    "id": "reduce",
                    "needs": [task["id"] for task in tasks],
                    "prompt": (
                        "High-risk requirement categories have evidence and the "
                        "reducer runs after the final release guard passes."
                    ),
                }
            ],
        }

    def _ensure_user_input_plan(self, ctx: ScenarioContext) -> UserInputPlan:
        if self._user_input_plan is not None:
            return self._user_input_plan
        prompt = ""
        if ctx.workflow is not None and is_entry_origin_workflow(ctx):
            prompt = str(ctx.workflow.workflow_goal or "")
        if not prompt:
            prompt = ctx.prompt or ctx.instruction or ""
        self._entry_prompt = prompt
        self._user_input_plan = build_user_input_plan(prompt)
        return self._user_input_plan

    def _should_fail_reducer(self, ctx: ScenarioContext) -> bool:
        if not is_entry_origin_workflow(ctx):
            return False
        iteration = ctx.iteration
        attempt = ctx.attempt
        return iteration.sequence_no in (1, 2) and attempt.attempt_sequence_no == 1


def _inventory_plan(
    *,
    kind: str,
    deferred_goal_for_next_iteration: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "tasks": [
            {"id": "requirement_inventory", "agent_name": "executor", "needs": []},
            {
                "id": "inventory_guard",
                "agent_name": "executor",
                "needs": ["requirement_inventory"],
            },
        ],
        "task_specs": {
            "requirement_inventory": "ACTION inspect_user_input",
            "inventory_guard": "VERIFY checkpoint=inventory dependency_count=1",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["requirement_inventory", "inventory_guard"],
                "prompt": (
                    "Requirement ledger was built from the already-rendered user "
                    "input and the package DAG policy can be derived from it."
                ),
            }
        ],
    }
    if kind == "defers":
        assert deferred_goal_for_next_iteration is not None
        args["deferred_goal_for_next_iteration"] = deferred_goal_for_next_iteration
    return args


def _recursive_full_only_plan() -> dict[str, Any]:
    tasks = [
        {"id": "recursive_inventory", "agent_name": "executor", "needs": []},
        {
            "id": "recursive_inventory_guard",
            "agent_name": "executor",
            "needs": ["recursive_inventory"],
        },
        {
            "id": "recursive_exec_a",
            "agent_name": "executor",
            "needs": ["recursive_inventory_guard"],
        },
        {
            "id": "recursive_exec_b",
            "agent_name": "executor",
            "needs": ["recursive_inventory_guard"],
        },
        {
            "id": "recursive_wave_guard",
            "agent_name": "executor",
            "needs": ["recursive_exec_a", "recursive_exec_b"],
        },
        {
            "id": "recursive_reconcile",
            "agent_name": "executor",
            "needs": ["recursive_wave_guard"],
        },
        {
            "id": "recursive_final_guard",
            "agent_name": "executor",
            "needs": ["recursive_reconcile"],
        },
    ]
    return {
        "tasks": tasks,
        "task_specs": {
            "recursive_inventory": "ACTION recursive_inventory",
            "recursive_inventory_guard": (
                "VERIFY checkpoint=recursive_inventory dependency_count=1"
            ),
            "recursive_exec_a": "ACTION recursive_execute slice=a",
            "recursive_exec_b": "ACTION recursive_execute slice=b",
            "recursive_wave_guard": ("VERIFY checkpoint=recursive_wave dependency_count=2"),
            "recursive_reconcile": "ACTION recursive_reconcile",
            "recursive_final_guard": ("VERIFY checkpoint=recursive_final dependency_count=1"),
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": [task["id"] for task in tasks],
                "prompt": ("Recursive package inventory, probes, and final guard all completed."),
            }
        ],
    }


def _package_task_spec(package: WorkPackage, wave_no: int) -> str:
    return (
        f"ACTION execute_package package={package.id} wave={wave_no} "
        f"subsystem={package.subsystem} risk={package.risk} "
        f"item_count={len(package.item_ids)}"
    )


def _chunked(
    packages: tuple[WorkPackage, ...],
    size: int,
) -> tuple[tuple[WorkPackage, ...], ...]:
    return tuple(tuple(packages[index : index + size]) for index in range(0, len(packages), size))


__all__ = ["FullCaseUserInput"]
