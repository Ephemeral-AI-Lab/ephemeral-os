"""Capacity-suite scenario-pack catalog.

This catalog is the executable capacity scenario-pack matrix. Focused rows point
to an implemented scenario when the task_center_runner harness owns the behavior
directly, or to the existing unit/live test anchor that currently owns the
lower-level contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapacityPackSpec:
    """One scenario-pack row and its current implementation anchor."""

    name: str
    registry_name: str | None = None
    test_path: str | None = None
    superseded_by: str | None = None

    @property
    def implementation_anchor(self) -> str:
        """Return the scenario/test name that keeps this catalog row covered."""
        return self.registry_name or self.test_path or self.superseded_by or ""


def names(specs: Iterable[CapacityPackSpec] = ()) -> set[str]:
    """Return scenario names from *specs* or from the default catalog."""
    rows = tuple(specs) if specs else CAPACITY_PACK_SPECS
    return {spec.name for spec in rows}


CAPACITY_PACK_SPECS: tuple[CapacityPackSpec, ...] = (
    CapacityPackSpec(
        "pipeline.dependency_dag_parallel",
        registry_name="pipeline.dependency_dag_parallel",
    ),
    CapacityPackSpec(
        "pipeline.dependency_dag_diamond",
        registry_name="pipeline.dependency_dag_diamond",
    ),
    CapacityPackSpec(
        "pipeline.dependency_blocked_descendants",
        registry_name="pipeline.dependency_blocked_descendants",
    ),
    CapacityPackSpec(
        "pipeline.attempt_retry_planner_failure",
        registry_name="pipeline.attempt_retry_planner_failure",
    ),
    CapacityPackSpec(
        "pipeline.attempt_retry_generator_failure",
        registry_name="pipeline.attempt_retry_generator_failure",
    ),
    CapacityPackSpec(
        "pipeline.nested_workflow",
        registry_name="pipeline.nested_workflow",
    ),
    CapacityPackSpec(
        "pipeline.nested_workflow_failure",
        registry_name="pipeline.nested_workflow_failure",
    ),
    CapacityPackSpec(
        "sandbox.setup_and_daemon",
        test_path="backend/tests/unit_test/test_sandbox/test_daemon/test_runtime_ready.py",
    ),
    CapacityPackSpec(
        "sandbox.occ_basic_writes",
        registry_name="sandbox.occ_concurrent_conflicts",
    ),
    CapacityPackSpec(
        "sandbox.occ_serial_merger",
        test_path="backend/tests/live_e2e_test/sandbox/occ/test_serial_merger.py",
    ),
    CapacityPackSpec(
        "sandbox.occ_stale_conflict",
        registry_name="sandbox.occ_concurrent_conflicts",
    ),
    CapacityPackSpec(
        "sandbox.overlay_capture_changes",
        test_path="backend/tests/unit_test/test_sandbox/test_overlay/test_upperdir_capture.py",
    ),
    CapacityPackSpec(
        "sandbox.overlay_symlink_handling",
        test_path="backend/tests/unit_test/test_sandbox/test_tool_primitives_workspace_filesystem.py",
    ),
    CapacityPackSpec(
        "sandbox.layerstack_lease_protection",
        registry_name="sandbox.auto_squash_commit_resume",
    ),
    CapacityPackSpec(
        "sandbox.layerstack_overlay_occ_high_concurrency",
        registry_name="sandbox.high_concurrency_layerstack_overlay_occ",
    ),
    CapacityPackSpec(
        "sandbox.command_exec_routing",
        test_path="backend/tests/unit_test/test_sandbox/test_command_exec/test_command_exec_policy.py",
    ),
    CapacityPackSpec(
        "sandbox.lsp_plugin_install",
        test_path="backend/tests/unit_test/test_sandbox/test_plugin_install.py",
    ),
    CapacityPackSpec(
        "sandbox.lsp_diagnostics_refresh",
        registry_name="sandbox.complex_project_build_shell_edit_lsp_smoke",
    ),
    CapacityPackSpec(
        "sandbox.lsp_cross_file_references",
        registry_name="sandbox.complex_project_build_shell_edit_lsp_smoke",
    ),
    CapacityPackSpec(
        "sandbox.lsp_after_edit_refresh",
        registry_name="sandbox.complex_project_build_shell_edit_lsp_smoke",
    ),
    CapacityPackSpec(
        "tools.terminal_tool_exclusivity",
        test_path="backend/tests/unit_test/test_tools/test_tool_execution.py",
    ),
    CapacityPackSpec(
        "tools.notification_budget_warning",
        test_path="backend/tests/unit_test/test_notification/test_tool_call_budget_tier_reminders.py",
    ),
    CapacityPackSpec(
        "tools.pre_post_hook_lifecycle",
        test_path="backend/tests/unit_test/test_tools/test_tool_execution.py",
    ),
    CapacityPackSpec(
        "context.planner_attempt_retry_overflow",
        test_path="backend/tests/unit_test/test_task_center/test_context_engine/test_attempts.py",
    ),
    CapacityPackSpec(
        "context.generator_with_dependencies",
        test_path="backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_other.py",
    ),
    CapacityPackSpec(
        "context.reducer_iterative_deferral",
        test_path="backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_other.py",
    ),
    CapacityPackSpec(
        "context.workflow_entry_minimal",
        test_path="backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_other.py",
    ),
    CapacityPackSpec(
        "planner_validation.unknown_dep",
        registry_name="planner_validation.unknown_dep",
    ),
    CapacityPackSpec(
        "planner_validation.cycle_in_deps",
        registry_name="planner_validation.cycle_in_deps",
    ),
    CapacityPackSpec(
        "planner_validation.defers_without_deferred_goal",
        registry_name="planner_validation.defers_without_deferred_goal",
    ),
    CapacityPackSpec(
        "planner_validation.unknown_agent_name",
        registry_name="planner_validation.unknown_agent_name",
    ),
    CapacityPackSpec(
        "planner_validation.empty_tasks",
        registry_name="planner_validation.empty_tasks",
    ),
    CapacityPackSpec(
        "capacity.full_system_capacity_matrix",
        registry_name="capacity.full_system_capacity_matrix",
    ),
    CapacityPackSpec(
        "capacity.recursive_release_train",
        superseded_by="full_case_user_input",
    ),
    CapacityPackSpec(
        "capacity.workspace_churn_soak",
        registry_name="sandbox.complex_project_build",
    ),
    CapacityPackSpec(
        "capacity.guardrail_recovery_gauntlet",
        superseded_by="capacity.full_system_capacity_matrix",
    ),
)


__all__ = ["CAPACITY_PACK_SPECS", "CapacityPackSpec", "names"]
