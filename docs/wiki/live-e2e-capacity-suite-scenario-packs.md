---
title: "Live E2E Capacity Suite - Scenario Packs"
tags: ["live-e2e", "capacity", "scenario-catalog", "task-center", "sandbox", "tools", "context-engine", "lsp"]
created: 2026-05-12T00:00:00.000Z
updated: 2026-05-12T00:00:00.000Z
sources: ["live-e2e-capacity-suite-plan.md", "live-e2e-scenario-suite-design.md"]
links: ["live-e2e-capacity-suite-plan.md", "live-e2e-capacity-suite-harness-actions.md", "live-e2e-capacity-suite-verification.md"]
category: decision
confidence: high
schemaVersion: 1
---

# Live E2E Capacity Suite - Scenario Packs

## Pack A - Pipeline And Graph Capacity

### `pipeline.dependency_dag_parallel`

Purpose: close the focused fan-in gap.

Shape: planner emits `a,b,c -> d`, all executors use `preflight`, evaluator passes.

Assertions:

- `a`, `b`, and `c` can run before `d`.
- `d` prompt includes dependency summaries from all three parents.
- `d` is not launched until all parent task rows are terminal success.

### `pipeline.dependency_dag_diamond`

Purpose: validate diamond readiness and dependency-summary rendering.

Shape: `a -> b,c -> d`, with `b` and `c` producing distinct summaries.

Assertions:

- `b` and `c` both depend on `a`.
- `d` has both dependency summaries and no duplicate summary block.
- launch order respects the diamond without serializing independent siblings unnecessarily.

### `pipeline.dependency_blocked_descendants`

Purpose: prove downstream blocking and graph invariants after a failed ancestor.

Shape: `a -> b,c -> d`; `a` fails through terminal submission.

Assertions:

- `b`, `c`, and `d` do not launch.
- dependent tasks are marked blocked or equivalent terminal non-runnable state.
- attempt closes with a failure reason that names the failed task.

### `pipeline.attempt_retry_planner_failure`

Purpose: cover planner terminal failure and retry path.

Shape: attempt 1 planner returns a failure terminal; attempt 2 emits a valid full plan.

Assertions:

- attempt 1 records planner failure.
- no generator or evaluator launches for attempt 1.
- attempt 2 succeeds and sees the failed-attempt landscape in planner context.

### `pipeline.attempt_retry_generator_failure`

Purpose: close generator retry coverage separate from evaluator retry.

Shape: attempt 1 emits a valid plan; one generator fails; attempt 2 emits a revised plan.

Assertions:

- failed generator task has a terminal failure summary.
- retry attempt has a new attempt id and new task ids.
- generator context includes revised task specification and does not leak stale pending tasks.

### `pipeline.nested_mission`

Purpose: prove recursive mission success path.

Shape: parent generator uses `request_mission_solution` before any edit. Child mission plans two tasks and returns a close report. Parent continues with final reconciliation.

Assertions:

- child mission has `requested_by_task_id`.
- parent receives child close report in context before final task.
- parent mission succeeds only after child mission succeeds.

### `pipeline.nested_mission_failure`

Purpose: prove recursive failure propagation.

Shape: child mission exhausts attempts. Parent receives failure report and submits a controlled failure or alternate recovery plan.

Assertions:

- child mission status is failed.
- parent task sees child failure context.
- parent final status matches the scenario expectation instead of hanging.

## Pack B - Sandbox Filesystem And Runtime Capacity

### `sandbox.setup_and_daemon`

Purpose: isolate bootstrap, runtime bundle upload, daemon readiness, and OP table dispatch.

Tool script:

- call daemon readiness.
- run `api.runtime.ready`.
- call a cheap daemon API through the public client.
- restart the sandbox if supported by fixture, then probe readiness again.

Assertions:

- daemon socket path is reachable after setup.
- readiness result names workspace root.
- no hidden setup mutation escapes the workspace.

### `sandbox.occ_basic_writes`

Purpose: focused write/read/layer publish contract.

Tool script: create a nested file tree, read every file through `read_file`, then read selected files through direct `sandbox.api`.

Assertions:

- every write emits OCC changeset and commit events.
- manifest layer count increases.
- direct API and toolkit reads agree after stripping toolkit line numbers.

### `sandbox.occ_serial_merger`

Purpose: prove disjoint edits to the same file merge serially without conflict.

Tool script: write one module, apply edits to header, body, and footer, then read back.

Assertions:

- all edits apply.
- changed path metadata names the same file.
- no conflict event is emitted.

### `sandbox.occ_stale_conflict`

Purpose: isolate stale shell mutation followed by edit anchor conflict.

Tool script: write a file, mutate it through shell, then apply an edit anchored to pre-shell content.

Assertions:

- conflict is typed and carries non-empty `conflict_reason`.
- shell-changed content remains intact.
- subsequent fresh edit succeeds.

### `sandbox.overlay_capture_changes`

Purpose: prove multi-file shell capture.

Tool script: one shell command creates, modifies, deletes, and moves files under multiple directories.

Assertions:

- `ShellResult.changed_paths` covers every expected mutation.
- overlay capture timing exists.
- final projection reflects create/modify/delete/move.

### `sandbox.overlay_symlink_handling`

Purpose: test internal symlink and symlink escape behavior.

Tool script: create internal symlink, read through it, attempt symlink escape to outside workspace, mutate through shell.

Assertions:

- internal symlink is handled according to current sandbox policy.
- escape attempt is blocked or recorded as a safe no-op.
- audit detail identifies the blocked path without leaking host data.

### `sandbox.layerstack_lease_protection`

Purpose: prove in-flight lease safety.

Tool script: hold or simulate a materialization lease while publishing a later layer, then read through both lease and current projection.

Assertions:

- lease view remains stable.
- current view sees the new layer.
- no stale lowerdir error appears in logs.

### `sandbox.command_exec_routing`

Purpose: prove guarded cwd/env/subprocess policy for shell execution.

Tool script: run commands from valid cwd, invalid cwd, env allowlist, env denylist, and timeout paths.

Assertions:

- invalid cwd fails closed.
- changed paths are only committed for successful in-workspace commands.
- timeout reports a typed error and no partial commit unless policy says otherwise.

## Pack C - Plugin And LSP Semantic Capacity

### `sandbox.lsp_plugin_install`

Purpose: isolate plugin installation and idempotent readiness.

Tool script: call plugin ensure twice, then call every LSP tool once.

Assertions:

- second ensure is a cache hit or safe no-op.
- all tool calls route through the plugin runtime.
- no Node/Pyright provisioning failure is misreported as a scenario assertion failure.

### `sandbox.lsp_diagnostics_refresh`

Purpose: prove diagnostics detect and clear edits.

Tool script: write clean module, break it, wait for diagnostics, repair it.

Assertions:

- broken state reports the expected diagnostic.
- repaired state clears diagnostics.
- diagnostic timing is recorded separately for cold and warm calls.

### `sandbox.lsp_cross_file_references`

Purpose: prove cross-file symbol index correctness after edits.

Tool script: write 8-12 Python files with imports, class hierarchy, function references, and test references. Rename a symbol and update callers.

Assertions:

- definitions point to the renamed declaration.
- references include source and test files.
- query symbols sees new files and no stale old symbol.

### `sandbox.lsp_after_edit_refresh`

Purpose: prove workspace symbols include freshly written files and remove deleted files.

Tool script: create modules, query symbols, delete/move modules through shell, query again.

Assertions:

- symbol additions appear after write.
- deleted or moved symbols disappear or update path.
- hidden scratch dirs are not used as scenario roots.

## Pack D - Tools, Hooks, Guardrails, Notifications

### `tools.request_mission_before_edit_gate`

Purpose: prove `request_mission_solution` is blocked after mutation.

Shape: generator writes one file, then tries `request_mission_solution`.

Assertions:

- request is rejected by hook.
- `hookSpecificOutput` names `RequestMissionBeforeEditGate`.
- task exits through the expected failure/retry path.

### `tools.terminal_tool_exclusivity`

Purpose: prove terminal tools cannot be batched with sibling calls.

Shape: hand-crafted runner turn returns `submit_execution_success` plus a sibling `read_file`.

Assertions:

- batch validation rejects the turn.
- no terminal status transition is applied.
- audit includes tool-call error and no executor success.

### `tools.resolver_success_limit_gate`

Purpose: prove resolver terminal success limit.

Shape: helper/resolver path creates unresolved resolver calls until the limit, then attempts success terminal.

Assertions:

- success terminal is rejected.
- notification can still be emitted.
- later valid resolver result passes after state is reconciled if supported.

### `tools.notification_budget_warning`

Purpose: prove budget warnings fire once per threshold.

Shape: scenario injects synthetic budget counters or runs enough tool calls to cross 50, 75, and 90 percent thresholds.

Assertions:

- each threshold fires exactly once.
- messages contain `SystemNotificationBlock`.
- notifications do not mutate task state.

### `tools.pre_post_hook_lifecycle`

Purpose: prove hook composition can replace input and output.

Shape: temporary test tool with pre-hook replacement and post-hook result replacement.

Assertions:

- tool receives rewritten input.
- final `ToolResult` carries post-hook replacement.
- hook trace order is stable.

## Pack E - Context Engine Capacity

### `context.planner_attempt_retry_overflow`

Purpose: prove failed-attempt landscape compression.

Shape: create more than six failed attempts, then launch planner attempt N+1.

Assertions:

- oldest failures collapse to medium-priority summary block.
- recent failures remain high priority.
- rendered prompt stays under token budget policy.

### `context.generator_with_dependencies`

Purpose: prove dependency summary blocks and ordering.

Shape: plan `a,b,c -> d`; launch `d` after parents succeed with distinct summaries and artifact refs.

Assertions:

- `# Dependency Results` appears once.
- each parent summary appears once.
- `planned_task_spec` remains last and required.

### `context.evaluator_episodic_continuation`

Purpose: prove evaluator receives prior-episode context.

Shape: episode 1 closes with summary; episode 2 evaluator launches after generator completion.

Assertions:

- mission block and previous episode results are present.
- evaluator does not receive planner-only failed-attempt landscape.
- evaluation criteria block is required.

### `context.helper_resolver_inheritance`

Purpose: prove parent packet inheritance for helper agents.

Shape: generator asks resolver; resolver launch captures inherited parent context.

Assertions:

- `# Parent context` appears.
- inherited blocks are priority-demoted.
- metadata records parent packet id.

### `context.entry_executor_minimal`

Purpose: prove entry executor gets only user request content.

Shape: entry executor launch is captured before mission creation.

Assertions:

- packet contains only `entry_request`.
- no mission, episode, attempt, or dependency block appears.

## Pack F - Planner Validation Rejection

### `planner_validation.unknown_dep`

Plan: task `b` depends on unknown local id `z`.

Assertions: planner attempt fails; no generator/evaluator runs; invariant detail names unknown dependency.

### `planner_validation.cycle_in_deps`

Plan: `a -> b`, `b -> a`.

Assertions: planner attempt fails; graph is not persisted as runnable.

### `planner_validation.partial_without_continuation_goal`

Plan: partial submission with no continuation goal.

Assertions: attempt fails with planner validation error and no continuation episode is created.

### `planner_validation.unknown_agent_name`

Plan: generator task references unregistered agent.

Assertions: validation rejects before dispatch.

### `planner_validation.empty_tasks`

Plan: full plan with no tasks.

Assertions: outcome matches product contract: either planner validation failure or immediate evaluator path. The test should codify the intended behavior.

## Pack G - Composite Capacity Scenarios

### `capacity.full_system_capacity_matrix`

Purpose: one heavy scenario that exercises graph, sandbox, LSP, context, tools, and audit together.

Shape:

- planner emits 25-40 tasks with serial, diamond, fan-in, and parallel branches.
- some tasks mutate the same project through public tools.
- some tasks are verifier tasks.
- one branch requests a child mission.
- one branch intentionally fails and triggers retry/replan behavior.
- final task reconciles all artifacts and runs pytest.

Assertions:

- graph completes with expected mission/episode/attempt counts.
- every expected sandbox event class appears.
- every LSP tool has semantic pass/fail accounting.
- context packets cover planner retry, generator deps, evaluator criteria, and helper inheritance.
- only expected tool errors appear.

### `capacity.recursive_release_train`

Purpose: model a large release-bundle workflow with recursive decomposition.

Shape:

- entry prompt describes a multi-package release.
- planner produces inventory, core semantics, build integration, CLI/cache/deploy, docs, validation, and final release tasks.
- oversized package task requests a child mission.
- verifier tasks gate each package before final reconciliation.

Assertions:

- child mission summaries are routed back to parent tasks.
- final evaluator sees all package summaries.
- context stays local per generator and does not flood every task with the full bundle.

### `capacity.workspace_churn_soak`

Purpose: stress the sandbox filesystem over time.

Shape:

- 100-200 files across nested packages.
- repeated create/edit/shell/delete/move operations.
- layer-stack squash threshold crossed multiple times.
- periodic direct API and shell readback.

Assertions:

- projection remains consistent.
- no stale layer-stack, hidden path, or content-changed error appears.
- perf metrics separate write/edit/shell/LSP cost.

### `capacity.guardrail_recovery_gauntlet`

Purpose: prove illegal operations fail safely and recovery continues.

Shape:

- intentionally trigger request mission after edit, terminal batch conflict, helper role mismatch, resolver limit, max-step limit, and one sandbox conflict.
- after each expected failure, run a legal recovery action.

Assertions:

- each failure is typed and attributed to the right guardrail.
- no failed guardrail corrupts TaskCenter state.
- final mission succeeds or fails exactly as the scenario declares.
