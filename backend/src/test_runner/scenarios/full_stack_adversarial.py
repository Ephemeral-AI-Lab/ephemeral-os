"""Full-stack adversarial SWE-EVO live scenario."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome

from task_center_runner.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)
from task_center_runner.scenarios._scenario_helpers import (
    instruction_field,
    is_entry_origin_workflow,
    is_recursive_workflow,
)
from task_center_runner.scenarios.user_input import (
    UserInputPlan,
    WorkPackage,
    build_user_input_plan,
)


@dataclass(frozen=True, slots=True)
class FullStackCell:
    """One named matrix cell emitted to the full-stack metrics artifact."""

    id: str
    subsystem: str
    tool_names: tuple[str, ...]
    package_id: str | None = None
    route: str = "gated"


class FullStackAdversarial(ScenarioBase):
    """Drive TaskCenter, sandbox, OCC, layer-stack, LSP, and recursion."""

    name = "full_stack_adversarial"

    def __init__(self) -> None:
        self._user_input_plan: UserInputPlan | None = None
        self._entry_prompt = ""
        self._forced_failure_seen = False
        self._recursive_package_id: str | None = None
        self._matrix_cells: list[FullStackCell] = []

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

    @property
    def matrix_plan(self) -> list[dict[str, Any]]:
        return [asdict(cell) for cell in self._matrix_cells]

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if is_recursive_workflow(ctx):
            return self._recursive_planner_response(ctx)
        return self._entry_origin_planner_response(ctx)

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        instruction = ctx.instruction or ctx.prompt or ""
        if "ACTION inspect_full_user_input" in instruction:
            return ("inspect_full_user_input",)
        if "ACTION occ_conflict_matrix" in instruction:
            return ("occ_conflict_matrix",)
        if "ACTION overlay_edge_matrix" in instruction:
            return ("overlay_edge_matrix",)
        if "ACTION layerstack_squash_lease" in instruction:
            return ("layerstack_squash_lease",)
        if "ACTION lsp_refresh_semantics" in instruction:
            return ("lsp_refresh_semantics",)
        if "ACTION request_recursive_matrix" in instruction:
            package_id = (
                instruction_field(instruction, "package")
                or self._recursive_package_id
                or ""
            )
            return (f"request_recursive_matrix:{package_id}",)
        if "ACTION recursive_oversized_matrix" in instruction:
            return ("recursive_oversized_matrix",)
        if "ACTION final_reconciliation" in instruction:
            return ("full_stack_final_reconciliation",)
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if (
            is_entry_origin_workflow(ctx)
            and ctx.iteration.sequence_no == 1
            and ctx.attempt.attempt_sequence_no == 1
        ):
            return ToolCallSpec(
                submit_reducer_outcome,
                {
                    "status": "failed",
                    "outcome": (
                        "Intentional inventory retry so the next planner sees "
                        "failed-attempt context before subsystem work."
                    ),
                },
            )
        if (
            is_entry_origin_workflow(ctx)
            and ctx.iteration.sequence_no == 2
            and ctx.attempt.attempt_sequence_no == 1
        ):
            return ToolCallSpec(
                submit_reducer_outcome,
                {
                    "status": "failed",
                    "outcome": (
                        "Subsystem wave gate failed once to force retry "
                        "evidence before recursive delegation."
                    ),
                },
            )
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": "Full-stack adversarial evidence accepted.",
            },
        )

    def recursive_handoff_goal(self, ctx: ScenarioContext) -> str | None:
        instruction = ctx.instruction or ""
        package_id = instruction_field(instruction, "package") or self._recursive_package_id
        if not package_id:
            return None
        plan = self._ensure_user_input_plan(ctx)
        package = next((item for item in plan.packages if item.id == package_id), None)
        if package is None:
            return f"Run oversized full-stack adversarial matrix for {package_id}."
        item_ids = ", ".join(package.item_ids[:12])
        return (
            "Run oversized full-stack adversarial matrix for package "
            f"{package.id}: {package.title}. Representative requirements: {item_ids}."
        )

    def _entry_origin_planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        iteration = ctx.iteration
        attempt = ctx.attempt
        self._ensure_user_input_plan(ctx)
        self._ensure_matrix_cells(ctx)
        if iteration.sequence_no == 1 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(submit_planner_outcome, _inventory_plan(kind="completes"))
        if iteration.sequence_no == 1:
            return ToolCallSpec(
                submit_planner_outcome,
                _inventory_plan(
                    kind="defers",
                    deferred_goal_for_next_iteration=(
                        "Execute the adversarial subsystem wave with OCC, "
                        "overlay, layer-stack, and LSP coverage."
                    ),
                ),
            )
        if iteration.sequence_no == 2 and attempt.attempt_sequence_no == 1:
            return ToolCallSpec(submit_planner_outcome, self._subsystem_wave_plan(ctx))
        if iteration.sequence_no == 2:
            return ToolCallSpec(submit_planner_outcome, self._retry_deferred_plan(ctx))
        return ToolCallSpec(submit_planner_outcome, self._final_plan(ctx))

    def _recursive_planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(submit_planner_outcome, _recursive_full_only_plan())

    def _subsystem_wave_plan(self, ctx: ScenarioContext) -> dict[str, Any]:
        plan = self._ensure_user_input_plan(ctx)
        recursive = _recursive_package(plan.packages)
        self._recursive_package_id = recursive.id if recursive is not None else None
        packages = {
            subsystem: _package_for_subsystem(plan.packages, subsystem)
            for subsystem in ("io", "distributed", "compat", "parquet")
        }
        tasks = [
            {"id": "occ_matrix", "agent_name": "executor", "needs": []},
            {"id": "overlay_matrix", "agent_name": "executor", "needs": []},
            {"id": "lsp_matrix", "agent_name": "executor", "needs": []},
            # The layer-stack script intentionally drives squash/GC behavior;
            # run it after the other matrices so it cannot invalidate their
            # active shell snapshots.
            {
                "id": "layerstack_matrix",
                "agent_name": "executor",
                "needs": ["occ_matrix", "overlay_matrix", "lsp_matrix"],
            },
            {
                "id": "subsystem_wave_guard",
                "agent_name": "executor",
                "needs": [
                    "occ_matrix",
                    "overlay_matrix",
                    "layerstack_matrix",
                    "lsp_matrix",
                ],
            },
        ]
        task_specs = {
            "occ_matrix": (f"ACTION occ_conflict_matrix package={_package_id(packages['io'])}"),
            "overlay_matrix": (
                f"ACTION overlay_edge_matrix package={_package_id(packages['distributed'])}"
            ),
            "layerstack_matrix": (
                f"ACTION layerstack_squash_lease package={_package_id(packages['compat'])}"
            ),
            "lsp_matrix": (
                f"ACTION lsp_refresh_semantics package={_package_id(packages['parquet'])}"
            ),
            "subsystem_wave_guard": ("VERIFY checkpoint=subsystem_wave_guard dependency_count=4"),
        }
        return {
            "tasks": tasks,
            "task_specs": task_specs,
            "reducers": [
                {
                    "id": "reduce",
                    "needs": [task["id"] for task in tasks],
                    "prompt": (
                        "OCC, overlay, layer-stack lease/squash, and LSP refresh "
                        "evidence were captured; the subsystem wave gate fails "
                        "once to force retry evidence."
                    ),
                }
            ],
            "deferred_goal_for_next_iteration": (
                "Retry with recursive oversized matrix delegation and final "
                "reconciliation after subsystem artifacts exist."
            ),
        }

    def _retry_deferred_plan(self, ctx: ScenarioContext) -> dict[str, Any]:
        plan = self._ensure_user_input_plan(ctx)
        recursive = _recursive_package(plan.packages)
        package_id = recursive.id if recursive is not None else "pkg_recursive_unknown"
        self._recursive_package_id = package_id
        tasks = [
            {
                "id": "request_recursive_matrix",
                "agent_name": "executor",
                "needs": [],
            },
            {
                "id": "final_reconciliation",
                "agent_name": "executor",
                "needs": ["request_recursive_matrix"],
            },
            {
                "id": "recursive_return_guard",
                "agent_name": "executor",
                "needs": ["request_recursive_matrix", "final_reconciliation"],
            },
        ]
        return {
            "tasks": tasks,
            "task_specs": {
                "request_recursive_matrix": (
                    f"ACTION request_recursive_matrix package={package_id}"
                ),
                "final_reconciliation": "ACTION final_reconciliation stage=retry",
                "recursive_return_guard": ("VERIFY checkpoint=recursive_return dependency_count=2"),
            },
            "reducers": [
                {
                    "id": "reduce",
                    "needs": [task["id"] for task in tasks],
                    "prompt": (
                        "Retry planner saw failed context, the recursive workflow "
                        "completed before the parent final guard, and final "
                        "reconciliation read subsystem and recursive artifacts."
                    ),
                }
            ],
            "deferred_goal_for_next_iteration": (
                "Run the final release guard and reducer after recursive close."
            ),
        }

    def _final_plan(self, ctx: ScenarioContext) -> dict[str, Any]:
        plan = self._ensure_user_input_plan(ctx)
        high_risk_count = sum(1 for item in plan.requirements if item.risk == "high")
        tasks = [
            {
                "id": "final_reconciliation_check",
                "agent_name": "executor",
                "needs": [],
            },
            {
                "id": "final_release_guard",
                "agent_name": "executor",
                "needs": ["final_reconciliation_check"],
            },
        ]
        return {
            "tasks": tasks,
            "task_specs": {
                "final_reconciliation_check": (
                    f"ACTION final_reconciliation stage=final high_risk_count={high_risk_count}"
                ),
                "final_release_guard": ("VERIFY checkpoint=final_release dependency_count=1"),
            },
            "reducers": [
                {
                    "id": "reduce",
                    "needs": [task["id"] for task in tasks],
                    "prompt": (
                        "Final metrics summary row exists with zero failed cells, "
                        "the final guard reads canonical reconciliation evidence, "
                        "and the reducer runs only after the final guard passes."
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

    def _ensure_matrix_cells(self, ctx: ScenarioContext) -> list[FullStackCell]:
        if self._matrix_cells:
            return self._matrix_cells
        plan = self._ensure_user_input_plan(ctx)
        package_by_subsystem = {
            subsystem: _package_id(_package_for_subsystem(plan.packages, subsystem))
            for subsystem in ("io", "distributed", "compat", "parquet")
        }
        cells: list[FullStackCell] = []
        cells.extend(
            FullStackCell(cell_id, "occ", tools, package_by_subsystem["io"])
            for cell_id, tools in _OCC_CELLS
        )
        cells.extend(
            FullStackCell(
                cell_id,
                "overlay",
                tools,
                package_by_subsystem["distributed"],
            )
            for cell_id, tools in _OVERLAY_CELLS
        )
        cells.extend(
            FullStackCell(cell_id, "layerstack", tools, package_by_subsystem["compat"])
            for cell_id, tools in _LAYERSTACK_CELLS
        )
        cells.extend(
            FullStackCell(cell_id, "lsp", tools, package_by_subsystem["parquet"])
            for cell_id, tools in _LSP_CELLS
        )
        cells.extend(
            FullStackCell(cell_id, "recursive", tools, self._recursive_package_id)
            for cell_id, tools in _RECURSIVE_CELLS
        )
        self._matrix_cells = cells
        return self._matrix_cells


def _inventory_plan(
    *,
    kind: str,
    deferred_goal_for_next_iteration: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "tasks": [
            {"id": "inspect_full_user_input", "agent_name": "executor", "needs": []},
            {
                "id": "inventory_guard",
                "agent_name": "executor",
                "needs": ["inspect_full_user_input"],
            },
        ],
        "task_specs": {
            "inspect_full_user_input": "ACTION inspect_full_user_input",
            "inventory_guard": "VERIFY checkpoint=inventory dependency_count=1",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["inspect_full_user_input", "inventory_guard"],
                "prompt": (
                    "Rendered prompt was parsed without reconstructing CSV data; "
                    "requirement ledger, package graph, and workspace proof were "
                    "written through tools."
                ),
            }
        ],
    }
    if kind == "defers":
        assert deferred_goal_for_next_iteration is not None
        args["deferred_goal_for_next_iteration"] = deferred_goal_for_next_iteration
    return args


def _recursive_full_only_plan() -> dict[str, Any]:
    """Single-attempt recursive plan for nested workflow launches."""
    tasks = [
        {"id": "recursive_oversized_a", "agent_name": "executor", "needs": []},
        {"id": "recursive_oversized_b", "agent_name": "executor", "needs": []},
        {
            "id": "recursive_closure_report",
            "agent_name": "executor",
            "needs": ["recursive_oversized_a", "recursive_oversized_b"],
        },
        {
            "id": "recursive_close_guard",
            "agent_name": "executor",
            "needs": ["recursive_closure_report"],
        },
    ]
    return {
        "tasks": tasks,
        "task_specs": {
            "recursive_oversized_a": "ACTION recursive_oversized_matrix slice=a",
            "recursive_oversized_b": "ACTION recursive_oversized_matrix slice=b",
            "recursive_closure_report": (
                "ACTION recursive_oversized_matrix slice=close close=true"
            ),
            "recursive_close_guard": "VERIFY checkpoint=recursive_final dependency_count=1",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": [task["id"] for task in tasks],
                "prompt": (
                    "Both recursive executor slices wrote evidence, the close "
                    "report was written after slice evidence, and the recursive "
                    "final guard read the close report."
                ),
            }
        ],
    }


def _package_for_subsystem(
    packages: tuple[WorkPackage, ...],
    subsystem: str,
) -> WorkPackage | None:
    return next((item for item in packages if item.subsystem == subsystem), None)


def _recursive_package(packages: tuple[WorkPackage, ...]) -> WorkPackage | None:
    return next((item for item in packages if item.recursive_candidate), None)


def _package_id(package: WorkPackage | None) -> str:
    if package is None:
        return "pkg_unknown"
    return package.id


_OCC_CELLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("same_path_concurrent_write", ("write_file", "write_file", "read_file")),
    ("disjoint_concurrent_writes", ("write_file", "write_file", "read_file")),
    ("same_file_disjoint_edits", ("write_file", "edit_file", "edit_file")),
    ("same_file_overlap_edits", ("write_file", "edit_file")),
    ("shell_stale_conflict", ("shell", "write_file", "read_file")),
    ("nonzero_shell_commits_side_effect", ("shell", "read_file")),
    ("tracked_and_ignored_mixed", ("shell", "read_file")),
    ("delete_vs_write", ("shell", "write_file", "read_file")),
)

_OVERLAY_CELLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("new_files", ("shell", "read_file")),
    ("modify_files", ("shell", "read_file")),
    ("delete_files", ("shell", "read_file")),
    ("mixed_kinds", ("shell", "read_file")),
    ("deep_paths", ("shell", "read_file")),
    ("special_chars", ("shell", "read_file")),
    ("long_filename", ("shell", "read_file")),
    ("symlink_inside", ("shell", "read_file")),
    ("symlink_escape", ("shell", "read_file")),
    ("whiteout_collision", ("shell", "read_file")),
    ("outside_workspace_write", ("shell",)),
    ("noop_shell", ("shell",)),
)

_LAYERSTACK_CELLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("initial_binding", ("shell",)),
    ("manifest_growth", ("write_file", "edit_file")),
    ("old_snapshot_evidence", ("write_file", "read_file")),
    ("auto_squash", ("write_file", "shell")),
    ("merged_readback", ("read_file", "shell")),
    ("lease_gc_safety", ("read_file",)),
)

_LSP_CELLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("initial_symbols", ("write_file", "lsp.hover", "lsp.query_symbols")),
    ("diagnostic_present", ("lsp.diagnostics",)),
    ("diagnostic_fixed", ("edit_file", "lsp.diagnostics")),
    ("signature_refresh", ("lsp.hover", "edit_file", "lsp.hover")),
    ("cross_file_reference_refresh", ("edit_file", "lsp.find_references")),
    ("workspace_edit_publish", ("lsp.apply_workspace_edit", "read_file")),
    ("config_refresh", ("write_file", "lsp.diagnostics")),
    ("opened_file_deleted", ("shell", "lsp.diagnostics")),
)

_RECURSIVE_CELLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("recursive_slice_a", ("write_file", "read_file")),
    ("recursive_slice_b", ("write_file", "read_file")),
)


__all__ = ["FullStackAdversarial", "FullStackCell"]
