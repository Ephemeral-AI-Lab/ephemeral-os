# Live E2E Capacity Suite Scenario Packs

This document is the human-readable counterpart of
`backend/src/live_e2e/scenarios/capacity/pack_catalog.py`. Each `###` heading
below is checked by `backend/src/live_e2e/tests/test_capacity_scenario_packs.py`.

## Pack A - TaskCenter Pipeline

### `pipeline.dependency_dag_parallel`

### `pipeline.dependency_dag_diamond`

### `pipeline.dependency_blocked_descendants`

### `pipeline.attempt_retry_planner_failure`

### `pipeline.attempt_retry_generator_failure`

### `pipeline.nested_goal`

### `pipeline.nested_goal_failure`

## Pack B - Sandbox Runtime

### `sandbox.setup_and_daemon`

### `sandbox.occ_basic_writes`

### `sandbox.occ_serial_merger`

### `sandbox.occ_stale_conflict`

### `sandbox.overlay_capture_changes`

### `sandbox.overlay_symlink_handling`

### `sandbox.layerstack_lease_protection`

### `sandbox.command_exec_routing`

## Pack C - Plugin And LSP Runtime

### `sandbox.lsp_plugin_install`

### `sandbox.lsp_diagnostics_refresh`

### `sandbox.lsp_cross_file_references`

### `sandbox.lsp_after_edit_refresh`

## Pack D - Tools And Hooks

### `tools.terminal_tool_exclusivity`

### `tools.notification_budget_warning`

### `tools.pre_post_hook_lifecycle`

## Pack E - Context Engine

### `context.planner_attempt_retry_overflow`

### `context.generator_with_dependencies`

### `context.evaluator_iterative_continuation`

### `context.helper_resolver_inheritance`

### `context.entry_executor_minimal`

## Pack F - Planner Validation

### `planner_validation.unknown_dep`

### `planner_validation.cycle_in_deps`

### `planner_validation.partial_without_continuation_goal`

### `planner_validation.unknown_agent_name`

### `planner_validation.empty_tasks`

## Pack G - Composite Capacity

### `capacity.full_system_capacity_matrix`

### `capacity.recursive_release_train`

### `capacity.workspace_churn_soak`

### `capacity.guardrail_recovery_gauntlet`
