# Sandbox Deferred Review Implementation Report

Source review: `.planning/sandbox-REVIEW-DEFERRED.md`

## Current Baseline

- Starting dirty worktree contained pre-existing changes under `backend/src/task_center*`.
- Sandbox work will avoid those paths unless a later phase explicitly requires them.
- `.planning/sandbox-REVIEW-DEFERRED.md` is untracked in this checkout and treated as the source artifact for this pass.

## Phase 1 - Prep Guard

Status: complete

Scope:
- Inspect `git status --short`.
- Read `.planning/sandbox-REVIEW-DEFERRED.md`.
- Consult `.planning/sandbox-REVIEW.md` and `/tmp/sandbox_review/execution.md` only for the C2 blocker and implementation shape.
- Establish this report.

Selected implementation order:
1. C2 two-pipeline collapse.
2. S4 provider Daytona client collapse.
3. S5 OCC flattening.
4. S6 plugin runtime flattening with compatibility shim.
5. Deferred daemon depth decision.
6. Local cleanups S7-S10 and smaller wins.
7. Cross-cutting naming renames only after flattening phases are green.

Blocker review:
- The historical C2 blocker is `backend/tests/unit_test/test_sandbox/test_overlay/test_overlay_dependency_boundaries.py`, which asserts the overlay runner/pipeline/worker/mount files exist.
- The current task resolves the direction: collapse into `orchestrator.execute_command(..., occ_apply=False, mount_mode=MountMode.COPY_BACKED)` and rewrite/delete tests according to the new boundary.
- Public surface choice for C2: use `occ_apply: bool = True`, matching the deferred review's preferred flag.

Changed files:
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- None

Compatibility shims:
- None

Tests and guards run:
- `git status --short`
- `git diff --stat`
- `git diff --check`

Failures and fixes:
- None

Next phase recommendation:
- Proceed to C2. Start by migrating daemon overlay calls to the orchestrator with `occ_apply=False`, then remove obsolete overlay pipeline modules after tests are rewritten.

## Phase 2 - C2 Two-Pipeline Collapse

Status: complete

Scope:
- Merge snapshot-overlay execution into `sandbox.execution.orchestrator.execute_command`.
- Add `occ_apply: bool = True` and `mount_mode: MountMode | None = None` to the orchestrator path.
- Route daemon `overlay.run` through the orchestrator with `occ_apply=False` and `mount_mode=MountMode.COPY_BACKED`.
- Delete obsolete overlay runner/pipeline/worker/mount modules after callers and tests moved.
- Rewrite the listed unit tests around the new orchestrator boundary.

Implementation notes:
- `CommandExecResult` now carries stdout/stderr refs so no-OCC overlay callers can return readable artifacts.
- `WorkspaceCapture` now carries the snapshot manifest so daemon `overlay.run` can preserve the old `OverlayCapture` payload shape.
- No-OCC orchestrator runs keep capture artifacts while removing bulk runtime intermediates.
- `CommandExecPolicy` now supports optional host-env allowlists; daemon `overlay.run` uses the old minimal environment behavior while command-exec default behavior remains unchanged.
- A concurrent commit appeared during the guard: `4a5ad60b Reframe TaskCenter naming and collapse overlay execution`. It contains the C2 work plus pre-existing TaskCenter naming changes. The current working tree now has a separate dirty `backend/src/task_center/entry/coordinator.py` change that is not part of this sandbox phase.

Changed files:
- `backend/src/sandbox/daemon/handler/overlay.py`
- `backend/src/sandbox/execution/contract.py`
- `backend/src/sandbox/execution/orchestrator.py`
- `backend/src/sandbox/execution/policy.py`
- `backend/src/sandbox/execution/workspace_mount.py`
- `backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py`
- `backend/tests/unit_test/test_sandbox/test_overlay/test_namespace_command_env.py`
- `backend/tests/unit_test/test_sandbox/test_overlay/test_overlay_dependency_boundaries.py`
- `backend/tests/unit_test/test_sandbox/test_overlay/test_runtime_invoker_cleanup.py`
- `backend/tests/unit_test/test_sandbox/test_overlay/test_snapshot_overlay_runner.py`
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- `backend/src/sandbox/execution/overlay_mounts.py`
- `backend/src/sandbox/execution/overlay_pipeline.py`
- `backend/src/sandbox/execution/overlay_runner.py`
- `backend/src/sandbox/execution/overlay_worker.py`

Compatibility shims:
- None kept for the deleted execution internals. They were not public plugin surfaces per the deferred review.
- `sandbox.execution.overlay_request`, `overlay_result`, `overlay_capture`, and `overlay_change` remain.

Tests and guards run:
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_overlay/test_snapshot_overlay_runner.py backend/tests/unit_test/test_sandbox/test_overlay/test_runtime_invoker_cleanup.py backend/tests/unit_test/test_sandbox/test_overlay/test_namespace_command_env.py backend/tests/unit_test/test_sandbox/test_overlay/test_overlay_dependency_boundaries.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py -q` - 25 passed
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` - 545 passed, 1 skipped
- `rg -n "from sandbox\\.execution\\.overlay_(runner|pipeline|worker|mounts)|sandbox\\.execution\\.overlay_(runner|pipeline|worker|mounts)" backend/src backend/tests` - no hits
- `git diff --stat` - current uncommitted diff only shows unrelated `backend/src/task_center/entry/coordinator.py`
- `git show --stat --oneline HEAD` - phase changes are in `4a5ad60b`
- `git diff --check` - clean

Failures and fixes:
- Initial broad import scan found stale live-e2e native probe text importing `sandbox.overlay.OverlayRuntimeInvoker`. Those probes predate this execution package layout and do not import the deleted `sandbox.execution.overlay_*` modules. No C2 code/test blocker remains.

Next phase recommendation:
- Proceed to S4 provider Daytona client collapse. Keep watching for dirty TaskCenter changes and avoid touching them.

## Phase 3 - S4 Provider Daytona Client Collapse

Status: complete

Scope:
- Collapse `sandbox/provider/daytona/client/` into `sandbox/provider/daytona/client.py`.
- Rewrite Daytona provider internals and tests from `sandbox.provider.daytona.client.*` deep imports to the flat `sandbox.provider.daytona.client` surface.
- Replace adapter imports of private sync-client helper names with explicit public helper names on the flat client module.

Implementation notes:
- The flat client module now owns credential loading, sync client caching, async loop-local client caching, timeout wrapping, pagination, and async-client shutdown helpers.
- Public helper names were introduced for adapter use: `SANDBOX_TIMEOUT_SECONDS`, `HEALTH_TIMEOUT_SECONDS`, `normalize_dict`, `normalize_optional_text`, `creation_param_classes`, `paginate_all`, and `call_with_optional_timeout`.
- Tests now patch/import the flat client module directly.
- Current working tree also contains unrelated concurrent edits under `backend/src/db/stores/`, `backend/src/task_center/`, and `backend/src/task_center_runner/`; they were not edited for S4.

Changed files:
- `backend/src/sandbox/provider/daytona/client.py`
- `backend/src/sandbox/provider/daytona/adapter.py`
- `backend/src/sandbox/provider/daytona/context.py`
- `backend/tests/unit_test/test_sandbox/test_service.py`
- `backend/tests/unit_test/test_sandbox/test_daytona_client_cache.py`
- `backend/tests/unit_test/test_sandbox/test_lifecycle.py`
- `backend/tests/unit_test/test_sandbox/test_credentials.py`
- `backend/tests/unit_test/test_sandbox/test_async/test_client.py`
- `backend/tests/unit_test/test_sandbox/test_context.py`
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- `backend/src/sandbox/provider/daytona/client/__init__.py`
- `backend/src/sandbox/provider/daytona/client/sync_client.py`
- `backend/src/sandbox/provider/daytona/client/async_client.py`
- `backend/src/sandbox/provider/daytona/client/credentials.py`
- `backend/src/sandbox/provider/daytona/client/shutdown.py`

Compatibility shims:
- None kept. The deferred S4 item explicitly changes the internal provider client surface to the flat `sandbox.provider.daytona.client` module.

Tests and guards run:
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_service.py backend/tests/unit_test/test_sandbox/test_daytona_client_cache.py backend/tests/unit_test/test_sandbox/test_lifecycle.py backend/tests/unit_test/test_sandbox/test_credentials.py backend/tests/unit_test/test_sandbox/test_async/test_client.py backend/tests/unit_test/test_sandbox/test_context.py backend/tests/unit_test/test_sandbox/test_provider_registry.py backend/tests/unit_test/test_sandbox/test_workspace.py backend/tests/unit_test/test_sandbox/test_providers/test_daytona_adapter.py backend/tests/unit_test/test_sandbox/test_providers/test_daytona_bash_exit_code.py -q` - 73 passed
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` - 545 passed, 1 skipped
- `rg -n "from sandbox\\.provider\\.daytona\\.client\\.|import sandbox\\.provider\\.daytona\\.client\\." backend` - no hits
- `.venv/bin/ruff check backend/src/sandbox/provider/daytona/client.py backend/src/sandbox/provider/daytona/adapter.py backend/src/sandbox/provider/daytona/context.py backend/tests/unit_test/test_sandbox/test_service.py backend/tests/unit_test/test_sandbox/test_daytona_client_cache.py backend/tests/unit_test/test_sandbox/test_lifecycle.py backend/tests/unit_test/test_sandbox/test_credentials.py backend/tests/unit_test/test_sandbox/test_async/test_client.py backend/tests/unit_test/test_sandbox/test_context.py` - passed
- `git diff --stat` - shows S4 plus unrelated concurrent non-sandbox edits
- `git diff --check` - clean

Failures and fixes:
- None

Next phase recommendation:
- Proceed to S5 OCC flattening. Keep the S5 import rewrite mechanical and avoid mixing in the deferred OCC behavior cleanups.

## Phase 4 - S5 OCC Flattening

Status: complete

Scope:
- Flatten `sandbox.occ.stage`, `sandbox.occ.content`, and `sandbox.occ.changeset` subpackages into depth-3 modules.
- Delete pure re-export shims and the `occ.timing_keys` re-export.
- Keep `sandbox.occ.__init__` as a stable facade.
- Rewrite sandbox, daemon, task-center-runner, live-e2e, and test imports mechanically.

Implementation notes:
- Promoted `occ/stage/transaction.py` to `occ/commit_transaction.py`.
- Promoted `occ/stage/merge.py` to `occ/stage.py` and inlined `stage/_edit.py`.
- Promoted `occ/stage/policy.py` to `occ/stage_policy.py`.
- Merged `occ/changeset/{types,prepared}.py` into `occ/changeset.py`.
- Promoted `occ/content/hashing.py` to `occ/hashing.py`.
- Promoted `occ/content/gitignore_oracle.py` to `occ/gitignore.py`.
- Replaced `sandbox.occ.timing_keys.TimingKey` imports with `sandbox.timing_keys.TimingKey`.
- Updated runtime bundle assertions to require the flat OCC modules and reject the old paths.

Changed files:
- `backend/src/sandbox/occ/__init__.py`
- `backend/src/sandbox/occ/changeset.py`
- `backend/src/sandbox/occ/client.py`
- `backend/src/sandbox/occ/commit_queue.py`
- `backend/src/sandbox/occ/commit_transaction.py`
- `backend/src/sandbox/occ/gitignore.py`
- `backend/src/sandbox/occ/hashing.py`
- `backend/src/sandbox/occ/maintenance.py`
- `backend/src/sandbox/occ/overlay.py`
- `backend/src/sandbox/occ/router.py`
- `backend/src/sandbox/occ/service.py`
- `backend/src/sandbox/occ/stage.py`
- `backend/src/sandbox/occ/stage_policy.py`
- `backend/src/sandbox/daemon/*` files that imported deep OCC paths
- `backend/tests/live_e2e_test/sandbox/occ/*`
- `backend/tests/unit_test/test_sandbox/test_occ/*`
- OCC-related sandbox API, command-exec, daemon, and toolkit tests
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- `backend/src/sandbox/occ/changeset/__init__.py`
- `backend/src/sandbox/occ/changeset/prepared.py`
- `backend/src/sandbox/occ/changeset/types.py`
- `backend/src/sandbox/occ/content/__init__.py`
- `backend/src/sandbox/occ/content/gitignore_oracle.py`
- `backend/src/sandbox/occ/content/hashing.py`
- `backend/src/sandbox/occ/stage/__init__.py`
- `backend/src/sandbox/occ/stage/_edit.py`
- `backend/src/sandbox/occ/stage/direct.py`
- `backend/src/sandbox/occ/stage/gated.py`
- `backend/src/sandbox/occ/stage/merge.py`
- `backend/src/sandbox/occ/stage/policy.py`
- `backend/src/sandbox/occ/stage/transaction.py`
- `backend/src/sandbox/occ/timing_keys.py`

Compatibility shims:
- No deep-path shims kept for `occ.stage.*`, `occ.content.*`, `occ.changeset.*`, or `occ.timing_keys`; S5 explicitly removes these depth-4 surfaces.
- `sandbox.occ` facade remains and now exports `CommitTransaction`, `DirectStager`, `GatedStager`, `FileResult`, and `FileStatus`.

Tests and guards run:
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ backend/tests/unit_test/test_sandbox/test_api/test_gitignore_oracle_cache.py backend/tests/unit_test/test_sandbox/test_api/test_shell_staleness_telemetry.py backend/tests/unit_test/test_sandbox/test_api/test_shell_atomic_by_path_count.py backend/tests/unit_test/test_sandbox/test_api/test_guarded_result_status.py backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_changeset.py backend/tests/unit_test/test_sandbox/test_command_exec/test_edit_snapshot_byte_derivation.py backend/tests/unit_test/test_sandbox/test_daemon/test_overlay_capture.py -q` - 98 passed
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` - 545 passed, 1 skipped
- `rg -n "from sandbox\\.occ\\.(stage|changeset|content)\\.|import sandbox\\.occ\\.(stage|changeset|content)\\." backend/src backend/tests` - no hits
- `rg -n "sandbox\\.occ\\.timing_keys|from sandbox\\.occ\\.timing_keys" backend/src backend/tests` - no hits
- `.venv/bin/ruff check backend/src/sandbox/occ backend/tests/unit_test/test_sandbox/test_occ backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py` - passed after removing one unused import
- `git diff --stat` - shows S5 plus earlier S4 and unrelated concurrent rename edits
- `git diff --check` - clean

Failures and fixes:
- Ruff reported an unused `RouteDecision` import in `backend/src/sandbox/occ/stage.py`; removed it and reran the guard successfully.

Next phase recommendation:
- Proceed to S6 plugin/runtime flattening. Keep `plugin/runtime/__init__.py` as a deprecation shim as required by the deferred review.

## Phase 5 - S6 Plugin Runtime Flattening

Status: complete

Scope:
- Move `sandbox/plugin/runtime/context.py` to `sandbox/plugin/op_context.py`.
- Move `sandbox/plugin/runtime/registry.py` to `sandbox/plugin/op_registry.py`.
- Keep `sandbox/plugin/runtime/__init__.py` as a deprecation re-export shim.
- Update sandbox-internal imports and runtime bundle shipping.
- Preserve in-tree LSP plugin compatibility through `from sandbox.plugin.runtime import register_plugin_op`.

Implementation notes:
- `sandbox.plugin.handler` now imports `PluginOpContext` and registry helpers from `op_context` / `op_registry`.
- Runtime bundle now ships `sandbox/plugin/op_context.py`, `sandbox/plugin/op_registry.py`, and the `sandbox/plugin/runtime/__init__.py` shim.
- Plugin tests use `sandbox.plugin.op_registry` for registry internals while retaining `sandbox.plugin.runtime` imports where compatibility is intentional.
- The shim emits `DeprecationWarning` on import and re-exports the public runtime API.

Changed files:
- `backend/src/sandbox/plugin/op_context.py`
- `backend/src/sandbox/plugin/op_registry.py`
- `backend/src/sandbox/plugin/runtime/__init__.py`
- `backend/src/sandbox/plugin/handler.py`
- `backend/src/sandbox/host/runtime_bundle.py`
- `backend/tests/unit_test/test_sandbox/test_plugin_runtime_registry.py`
- `backend/tests/unit_test/test_sandbox/test_plugin_handler.py`
- `backend/tests/unit_test/test_sandbox/test_plugin_lifecycle_wedge.py`
- `backend/tests/unit_test/test_sandbox/test_runtime_bundle_includes_plugin.py`
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- `backend/src/sandbox/plugin/runtime/context.py`
- `backend/src/sandbox/plugin/runtime/registry.py`

Compatibility shims:
- Kept `backend/src/sandbox/plugin/runtime/__init__.py` as a deprecation shim for plugin authors and the in-tree LSP plugin.

Tests and guards run:
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_plugin_runtime_registry.py backend/tests/unit_test/test_sandbox/test_plugin_handler.py backend/tests/unit_test/test_sandbox/test_plugin_lifecycle_wedge.py backend/tests/unit_test/test_sandbox/test_runtime_bundle_includes_plugin.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py -q` - 29 passed, 1 expected deprecation warning
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` - 545 passed, 1 skipped, 1 expected deprecation warning
- `rg -n "from sandbox\\.plugin\\.runtime\\.(context|registry)|import sandbox\\.plugin\\.runtime\\.(context|registry)|sandbox\\.plugin\\.runtime\\.(context|registry)" backend/src backend/tests` - no hits
- `.venv/bin/ruff check backend/src/sandbox/plugin backend/tests/unit_test/test_sandbox/test_plugin_runtime_registry.py backend/tests/unit_test/test_sandbox/test_plugin_handler.py backend/tests/unit_test/test_sandbox/test_plugin_lifecycle_wedge.py backend/tests/unit_test/test_sandbox/test_runtime_bundle_includes_plugin.py` - passed after moving the warning below imports
- `git diff --stat` - shows S6 plus prior phases and unrelated concurrent rename edits
- `git diff --check` - clean

Failures and fixes:
- Ruff reported `E402` in the deprecation shim because the warning ran before re-export imports. Moved the warning below the imports and reran the guard successfully.

Next phase recommendation:
- Inspect current daemon handler/service imports and decide whether Option B is still the smallest boundary-preserving daemon-depth fix.

## Phase 6 - Deferred Daemon Depth Decision

Status: complete

Decision:
- Implemented Option B.
- Rationale: current imports still showed broad handler/service depth-4 coupling, but only four shared modules needed promotion. Option A would flatten ~24 daemon files into one directory, while Option C would leave the strict import-depth issue unresolved.

Scope:
- Promote shared daemon internals up one level.
- Rewrite daemon and test imports away from `sandbox.daemon.handler.request_context`, `sandbox.daemon.service.occ_backend`, `sandbox.daemon.service.result_projection`, and `sandbox.daemon.service.workspace_server`.
- Keep `service/` for remaining non-promoted services: `layer_stack_client.py`, `workspace_binding.py`, and `shell_runner.py`.

Changed files:
- `backend/src/sandbox/daemon/_toolbox.py`
- `backend/src/sandbox/daemon/_wire.py`
- `backend/src/sandbox/daemon/occ_backend.py`
- `backend/src/sandbox/daemon/workspace_server.py`
- Daemon handlers and services importing those modules
- Daemon, command-exec, OCC, and API tests importing those modules
- `backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py`
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- `backend/src/sandbox/daemon/handler/request_context.py`
- `backend/src/sandbox/daemon/service/occ_backend.py`
- `backend/src/sandbox/daemon/service/result_projection.py`
- `backend/src/sandbox/daemon/service/workspace_server.py`

Compatibility shims:
- None kept. These are daemon-internal modules and the deferred review frames this as a boundary cleanup, not a public API.

Tests and guards run:
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_daemon backend/tests/unit_test/test_sandbox/test_command_exec backend/tests/unit_test/test_sandbox/test_occ/test_mutation_gate.py backend/tests/unit_test/test_sandbox/test_api/test_shell_staleness_telemetry.py backend/tests/unit_test/test_sandbox/test_occ/test_shell_capture_atomicity.py -q` - 139 passed after fixing leftover test imports
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` - 545 passed, 1 skipped, 1 expected deprecation warning
- `rg -n "from sandbox\\.daemon\\.(handler\\.request_context|service\\.(occ_backend|result_projection|workspace_server))|sandbox\\.daemon\\.(handler\\.request_context|service\\.(occ_backend|result_projection|workspace_server))" backend/src backend/tests` - no hits
- `.venv/bin/ruff check backend/src/sandbox/daemon backend/tests/unit_test/test_sandbox/test_daemon backend/tests/unit_test/test_sandbox/test_occ/test_mutation_gate.py` - passed
- `git diff --stat` - shows daemon Option B plus prior phases and unrelated concurrent rename edits
- `git diff --check` - clean

Failures and fixes:
- First daemon-focused test run failed on stale `request_context` test imports and one indentation issue caused by mechanical rewrite. Updated tests to import `sandbox.daemon._toolbox` and reran successfully.

Next phase recommendation:
- Proceed to local cleanups S7-S10 one at a time. Start with S7 because it is a narrow one-caller deletion.

## Phase 7.1 - S7 Delete Host Context Preparer

Status: complete

Scope:
- Delete `backend/src/sandbox/host/context_preparer.py`.
- Keep the public `sandbox.api.context_preparer_for` compatibility surface.
- Remove the stale host package note for the deleted module.
- Add focused tests for the public factory behavior.

Implementation notes:
- `context_preparer_for` now lives in `sandbox.api._control`, alongside the other provider-facing public control helpers.
- `sandbox.api.__init__` only re-exports the factory from `_control`, preserving the API package import fence.
- The empty `SandboxRuntimeContext` and `SandboxContextPreparer` Protocol stubs were removed with the deleted host module.
- Removed one stale unused import from `sandbox.api._impl._results` after the phase ruff guard reported it.

Changed files:
- `backend/src/sandbox/api/__init__.py`
- `backend/src/sandbox/api/_control.py`
- `backend/src/sandbox/api/_impl/_results.py`
- `backend/src/sandbox/host/__init__.py`
- `backend/tests/unit_test/test_sandbox/test_context.py`
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- `backend/src/sandbox/host/context_preparer.py`

Compatibility shims:
- Kept `sandbox.api.context_preparer_for`.
- No shim kept for `sandbox.host.context_preparer`; S7 explicitly deletes that host-internal module.

Tests and guards run:
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_context.py -q` - 18 passed
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_context.py backend/tests/unit_test/test_sandbox/test_api/test_contract.py -q` - 40 passed
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` - 547 passed, 1 skipped, 1 expected deprecation warning
- `rg -n "sandbox\\.host\\.context_preparer|from sandbox\\.host import context_preparer|context_preparer.py" backend/src backend/tests` - no hits
- `.venv/bin/ruff check backend/src/sandbox/api backend/src/sandbox/host backend/tests/unit_test/test_sandbox/test_context.py` - passed after removing one stale unused import
- `git diff --stat` - shows S7 plus prior sandbox phases and unrelated concurrent rename edits
- `git diff --check` - clean

Failures and fixes:
- The first full sandbox guard failed because `sandbox.api.__init__` imported `sandbox.provider.registry`, violating the API import-boundary contract. Moved the factory implementation into `sandbox.api._control`, which already owns provider-facing control helpers, and reran the guard successfully.
- The first targeted ruff check found an unused `WriteFileResult` import in `sandbox.api._impl._results`; removed it and reran the guard successfully.

Next phase recommendation:
- Proceed to S8. Keep it scoped to `host/daemon_client.py`, preserve retry/readiness semantics, and run daemon-client focused tests before the full sandbox suite.

## Phase 7.2 - S8 Inline Daemon Client Dispatch Stack

Status: complete

Scope:
- Simplify `backend/src/sandbox/host/daemon_client.py`.
- Collapse the private `_exec_daemon_call`, `_should_retry_after_connect_failure`, `_check_daemon_readiness_after_spawn`, and `_readiness_request_for_original` chain into one `_dispatch_once_with_retry` helper.
- Keep `_call_daemon` as the stable internal entry point used by callers and tests.

Implementation notes:
- `_dispatch_once_with_retry` now owns the thin-client call, one reconnect retry after `_THIN_CLIENT_CONNECT_FAILED`, daemon spawn, runtime readiness probe, bootstrap readiness exception, and final retry of the original envelope.
- Readiness payload construction now uses the already-normalized `op` and `args` from `_call_daemon`, so the old JSON reparse helper is no longer needed.
- Spawn failure behavior remains fail-closed by returning the failed spawn result to `_call_daemon`, which raises the existing `RuntimeExecFailed` error.

Changed files:
- `backend/src/sandbox/host/daemon_client.py`
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- None

Compatibility shims:
- None needed. Removed helpers were private; `_call_daemon`, `call_daemon_api`, and `ensure_daemon_current` remain.

Tests and guards run:
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_daemon/test_daemon_transport.py backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py -q` - 13 passed
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_daemon backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py backend/tests/unit_test/test_sandbox/test_runtime_bootstrap.py -q` - 75 passed
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` - 547 passed, 1 skipped, 1 expected deprecation warning
- `rg -n "_exec_daemon_call|_should_retry_after_connect_failure|_check_daemon_readiness_after_spawn|_readiness_request_for_original" backend/src/sandbox/host/daemon_client.py backend/tests/unit_test/test_sandbox` - no hits
- `.venv/bin/ruff check backend/src/sandbox/host/daemon_client.py backend/tests/unit_test/test_sandbox/test_daemon/test_daemon_transport.py backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py` - passed
- `git diff --stat` - shows S8 plus prior sandbox phases and unrelated concurrent rename edits
- `git diff --check` - clean

Failures and fixes:
- None

Next phase recommendation:
- Proceed to S9. Keep it to the tuple-driven runtime bundle inclusion loop and bundle-content tests.

## Phase 7.3 - S9 Data-Driven Runtime Bundle Includes

Status: complete

Scope:
- Simplify repeated `_add_if_exists` blocks in `backend/src/sandbox/host/runtime_bundle.py`.
- Preserve the exact bundle file set and archive names.

Implementation notes:
- Replaced the root sandbox module `_add_if_exists` calls with a tuple-driven loop.
- Replaced the plugin module `_add_if_exists` calls with a tuple-driven loop.
- Did not change tree bundling, exclusions, pathspec vendoring, or upload behavior.

Changed files:
- `backend/src/sandbox/host/runtime_bundle.py`
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- None

Compatibility shims:
- None needed.

Tests and guards run:
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime_bundle_includes_plugin.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py -q` - 13 passed
- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` - 547 passed, 1 skipped, 1 expected deprecation warning
- `.venv/bin/ruff check backend/src/sandbox/host/runtime_bundle.py backend/tests/unit_test/test_sandbox/test_runtime_bundle_includes_plugin.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py` - passed
- `git diff --stat` - shows S9 plus prior sandbox phases and unrelated concurrent rename edits
- `git diff --check` - clean

Failures and fixes:
- None

Next phase recommendation:
- Proceed to S10. Work in `backend/src/sandbox/occ/commit_transaction.py` because S5 promoted the old transaction module there.
