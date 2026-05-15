"""Backward-compat shim — re-exports from ``task_center_runner``.

This package was renamed to ``task_center_runner`` per
``.omc/plans/task_center_runner-restructure.md`` (Phase 1). This shim survives
one release so external imports ``from live_e2e.*`` continue to resolve to the
canonical modules in ``task_center_runner.*``. New code MUST import from
``task_center_runner``; this shim will be removed when all call sites are
migrated.

Silent (no ``DeprecationWarning``) per the plan's user-locked decision #9.
"""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

# Mirror every public ``task_center_runner`` submodule into ``sys.modules``
# under the ``live_e2e`` namespace. After this, ``from live_e2e.audit.bus
# import X`` resolves to the SAME module object as ``from
# task_center_runner.audit.bus import X``, so pytest fixture identity,
# ``isinstance`` checks, and module-level singletons all behave as if the
# rename never happened.
_MIRRORED_SUBMODULES = (
    "audit",
    "audit.bus",
    "audit.events",
    "audit.legacy",
    "audit.metrics",
    "audit.node_id",
    "audit.performance_report",
    "audit.recorder",
    "audit.sandbox_events",
    "audit.stream_bridge",
    "fixtures",
    "hooks",
    "hooks.builtins",
    "hooks.registry",
    "real_agent_bootstrap",
    "real_agent_run",
    "runner",
    "scenarios",
    "scenarios._utils",
    "scenarios._utils.inspectors",
    "scenarios._utils.goal_helpers",
    "scenarios._utils.plans",
    "scenarios.base",
    "scenarios.capacity",
    "scenarios.capacity.full_system_capacity_matrix",
    "scenarios.capacity.pack_catalog",
    "scenarios.context",
    "scenarios.correctness_testing",
    "scenarios.full_case_user_input",
    "scenarios.full_stack_adversarial",
    "scenarios.pipeline",
    "scenarios.pipeline.trial_budget_exhausted",
    "scenarios.pipeline.trial_retry_evaluator_failure",
    "scenarios.pipeline.trial_retry_generator_failure",
    "scenarios.pipeline.trial_retry_planner_failure",
    "scenarios.pipeline.dependency_blocked_descendants",
    "scenarios.pipeline.dependency_dag_diamond",
    "scenarios.pipeline.dependency_dag_mixed",
    "scenarios.pipeline.dependency_dag_parallel",
    "scenarios.pipeline.dependency_dag_serial",
    "scenarios.pipeline.iterative_continuation",
    "scenarios.pipeline.generator_failure_quiescence",
    "scenarios.pipeline.initial_goal",
    "scenarios.pipeline.nested_goal",
    "scenarios.pipeline.partial_parent_planner_full_only",
    "scenarios.planner_validation",
    "scenarios.planner_validation.cycle_in_deps",
    "scenarios.planner_validation.duplicate_local_id",
    "scenarios.planner_validation.empty_tasks",
    "scenarios.planner_validation.partial_without_continuation_goal",
    "scenarios.planner_validation.unknown_agent_name",
    "scenarios.planner_validation.unknown_dep",
    "scenarios.sandbox",
    "scenarios.sandbox._fixtures",
    "scenarios.sandbox._fixtures.lsp_expectations",
    "scenarios.sandbox._fixtures.refactor_passes",
    "scenarios.sandbox._fixtures.scheduler_demo_data",
    "scenarios.sandbox._metrics",
    "scenarios.sandbox.auto_squash_commit_resume",
    "scenarios.sandbox.complex_project_build",
    "scenarios.sandbox.complex_project_build_shell_edit_lsp",
    "scenarios.sandbox.occ_concurrent_conflicts",
    "scenarios.tools",
    "scenarios.user_input",
    "squad",
    "squad.capacity_actions",
    "squad.capacity_actions.metrics",
    "squad.capacity_actions.types",
    "squad.complex_project_build_probe",
    "squad.complex_project_build_shell_edit_lsp_probe",
    "squad.definitions",
    "squad.full_stack_tool_scripts",
    "squad.prompt_inspector",
    "squad.runner",
    "squad.sandbox_probe",
    "squad.tool_scripts",
    "stores",
    "sweevo_adapter",
)

# Phase 5 of the restructure rearranges several legacy top-level modules
# (``squad/`` → ``agent/mock/``, ``stores`` → ``core/stores``, etc.). The
# external ``live_e2e.*`` API is preserved for one release; this prefix
# remap table lets the shim's mirror list keep using the old paths.
_PREFIX_REMAPS = (
    ("squad", "agent.mock"),
    ("stores", "core.stores"),
    ("real_agent_bootstrap", "core.bootstrap"),
    ("sweevo_adapter", "benchmarks.sweevo.fixtures"),
    ("fixtures", "core.fixtures"),
    ("runner", "core.runner"),
    ("real_agent_run", "core.real_agent_run"),
)


def _remap(subname: str) -> str:
    for src, tgt in _PREFIX_REMAPS:
        if subname == src:
            return tgt
        if subname.startswith(src + "."):
            return tgt + subname[len(src) :]
    return subname


for _subname in _MIRRORED_SUBMODULES:
    _sys.modules[__name__ + "." + _subname] = _importlib.import_module(
        "task_center_runner." + _remap(_subname)
    )
del _subname

from task_center_runner import (  # noqa: E402,F401
    RunReport,
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
    run_scenario,
)

__all__ = [
    "RunReport",
    "TaskCenterStoreBundle",
    "create_per_test_task_center_stores",
    "run_scenario",
]
