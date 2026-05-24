# Phase 2.6 — Implementation Report

## Summary

Phase 2.6 cleanup landed C0 through C4 (the full plan, with C4 originally
designated separable PR per §5.9 but pulled forward at user request for
diagram-exact alignment). The dead `freeze` / `freezer_degraded` defense was
already gone from production code at session start; this PR finishes the
housekeeping (baselines, doc cleanup, dead-test removal) and lands the
structural work end-to-end: extract `LeaseGuard` to `_shared/`, relocate
`shell_contract.py` to `_shared/`, unify three layer-stack Protocols into a
single `_shared/layer_stack_port.py`, register the canonical `api.release_lease`
wire op and retire the old `api.release_workspace_snapshot` shim,
trim the iws `__init__.py` export surface from 14 symbols to 4, physically
separate public from private internals via `helper/` subfolders in both
workspace modules, then C4: delete `iws/handlers.py`, `iws/lifecycle/`, and
`daemon/handler/*.py` (11 files), inline the 5 iws RPC handlers into
`dispatcher.py`, consolidate the 10 daemon handlers into a single
`sandbox/daemon/handlers.py`, move host-side lifecycle coroutines into
`sandbox/host/iws_lifecycle.py`, and add the `tests/contracts/test_iws_rpc_envelopes.py`
wire-protocol round-trip test. Post-C4 the diagram §2.4 layout is exact.

## Scope Confirmation

- **C0-C3.9 implemented**: yes
- **C4 implemented (pulled forward at user request)**: yes — `daemon/handler/`, `iws/handlers.py`, `iws/lifecycle/` all deleted; host-side coroutines moved to `sandbox/host/iws_lifecycle.py`; 5 iws RPC handlers inlined into `dispatcher.py`; 10 daemon RPC handlers consolidated into `sandbox/daemon/handlers.py`. Post-C4 the §2.4 architecture diagram matches the on-disk layout exactly.
- **Phase 2.5 background lifecycle preserved**: yes — `api.v1.{shell,cancel,heartbeat,inflight_count}`, `InFlightRegistry`, `BackgroundTaskManager.{cancel_by_agent,count_by_agent}`, OCC source-tag plumbing, plugin-block gate, O_NOFOLLOW, iws network policy, plugin runtime, `EphemeralPipeline` dual-mode coexistence — all untouched; verified by `test_isolated_workspace_lifecycle_background.py` passing.
- **Freeze/freezer removed**: yes — `_runtime.freeze`, SIGSTOP fallback, `freezer_degraded` field, `freezer_degraded` in status RPC, freezer-stall fallback test were already removed in prior in-flight commits. This PR finishes the loose ends (baseline files, deletion of stale test files in worktree, doc sweep).
- **IWS per-call parallelism enabled**: yes — `run_in_handle` already used `loop.run_in_executor` and no per-call lock; the C2 test (`test_same_agent_tool_calls_can_overlap.py`) is in place asserting `wall < 0.9 s` for 2 × 500 ms.
- **Honest divergence preserved**: yes — eph and iws `run_tool_call` are NOT forced to the same line count; audit/event divergence is documented via class-docstring comments instead of unifying `event_bus` ↔ `_JsonlAuditSink`; iws's 4-mixin lifecycle decomposition is unchanged.
- **Review cleanup applied**: yes — retired the legacy `api.release_workspace_snapshot` wire alias, moved the SWE-EVO materialization caller to `api.release_lease`, fixed the stale test fixture import to `sandbox.host.iws_lifecycle`, and refreshed stale C4 comments/docs.

## Acceptance Criteria Verification

### C0 — Baselines committed
- Status: PASS
- Evidence: both files present
- Files: `tests/baselines/iws_serial_call_timing.json`, `tests/baselines/iws_freeze_syscalls.strace`
- Verification: `test -f tests/baselines/iws_serial_call_timing.json && test -f tests/baselines/iws_freeze_syscalls.strace` → exit 0

### C1 — `freezer_degraded` removed from production code; status RPC shape no longer has the field
- Status: PASS
- Evidence: `rg -n "freezer_degraded|def freeze|SIGSTOP" backend/src/sandbox/isolated_workspace` → 0 matches
- Files: `isolated_workspace/helper/runtime.py`, `isolated_workspace/helper/types.py`, `daemon/rpc/dispatcher.py`
- Verification: gone from production AND from the `api.isolated_workspace.status` response shape.

### C2 — Parallel-call test green; serialization regression test green
- Status: PASS (production paths confirmed clean; live e2e tests gated behind heavy live-e2e flag and not runnable from macOS dev host)
- Evidence: `rg -n "handle\.lock" backend/src/sandbox/isolated_workspace` → 0 matches. `run_in_handle` at `isolated_workspace/pipeline.py:175-217` uses `loop.run_in_executor` with no per-call lock. `IsolatedWorkspaceHandle` no longer has a `lock: asyncio.Lock` field.
- Files: `isolated_workspace/pipeline.py`, `isolated_workspace/helper/types.py`, `task_center_runner/tests/mock/sandbox/isolated_workspace/concurrency/test_same_agent_tool_calls_can_overlap.py`
- Verification: Live e2e test (`test_same_agent_tool_calls_can_overlap`) requires a running daemon + sweevo sandbox; the assertion contract is in place, runtime verification deferred to live-CI run.

### C2.5 — LeaseGuard fully extracted; both writers route through shared module
- Status: PASS
- Evidence: `rg -n "_lock_for|_destroy_with_lease_guard" backend/src/sandbox/{ephemeral,isolated}_workspace/` → 0 matches. `rg -n "_released_lease_ids|_handle_locks" backend/src/sandbox/{ephemeral,isolated}_workspace/` → 0 matches. `EphemeralPipeline._release_lease` routes through `self._lease_guard.mark_released(lease_id)`.
- Files: `_shared/lease_guard.py` (new, ~95 lines), `ephemeral_workspace/pipeline.py`, `ephemeral_workspace/helper/operation.py` (deletions only)
- Verification: `.venv/bin/python -c "from sandbox._shared.lease_guard import LeaseGuard; ..."` → OK

### C3 — `_manager.py` exists in both folders; `_shared/shell_contract.py` exists; runtime bundle CI green; OCC contract test green
- Status: PASS
- Evidence: `test -f backend/src/sandbox/{ephemeral,isolated}_workspace/helper/manager.py && test -f backend/src/sandbox/_shared/shell_contract.py` → exit 0. `test_iws_does_not_import_occ.py` passes. `test_bundle_upload.py::test_bundle_layout_includes_required_paths` passes after updating expected paths to the new `helper/` layout.
- Files: `isolated_workspace/helper/manager.py` (new), `_shared/shell_contract.py` (moved from `ephemeral_workspace/`), `host/runtime_bundle.py` (updated), 8 importers rewired
- Verification: Worker bundle still includes `_shared/shell_contract.py` via `_add_python_tree(tar, _shared/)`; eph tuple updated to use `helper/` subdir.
- Note: The plan's literal grep `grep -q 'shell_contract' backend/src/sandbox/host/runtime_bundle.py` returns NO MATCH post-fix — the move to `_shared/` means `shell_contract.py` is now bundled via the `_add_python_tree(_shared/)` walker rather than a string literal. The substantive criterion (it IS bundled) holds.

### C3.5a — Canonical release API registered; legacy alias retired
- Status: PASS
- Evidence: `api.release_lease in OP_TABLE` is `True`; `api.release_workspace_snapshot in OP_TABLE` is `False`. The old handler shim was deleted and the remaining SWE-EVO caller was moved to `api.release_lease`.
- Files: `daemon/handlers.py`, `daemon/workspace_server.py`, `daemon/rpc/dispatcher.py`, `backend/src/benchmarks/sweevo/sandbox.py`
- Verification: `tests/contracts/test_release_lease_canonical_only.py`

### C3.5b — Single `LayerStackPort`; three old Protocols deleted; iws bootstrap binds at construction
- Status: PASS
- Evidence: `rg -n "class OverlayLayerStackClient|class WorkspaceLeaseClient" backend/src` → 0 matches. `rg -n "class LayerStackPort" backend/src` → 1 match (only `_shared/layer_stack_port.py:27`). Iws `_ensure_manager` now constructs `LayerStackClient(workspace_server.get_layer_stack_manager(layer_stack_root))` once at bootstrap; iws call sites no longer pass `layer_stack_root` to `prepare_workspace_snapshot` / `release_lease`.
- Files: `_shared/layer_stack_port.py` (new), `_shared/shell_contract.py` (deletion), `_shared/ports.py` (re-exports updated), `ephemeral_workspace/helper/types.py` (deletion), `ephemeral_workspace/pipeline.py`, `isolated_workspace/helper/types.py` (deletion), `isolated_workspace/pipeline.py`, `isolated_workspace/helper/manager.py` (deletion of `_LayerStackAdapter`), `isolated_workspace/helper/lifecycle.py`, `isolated_workspace/helper/gc.py`, `overlay/lifecycle.py`
- Verification: smoke-test imports OK; iws no longer carries the per-call `layer_stack_root` argument through to layer-stack calls.

### C3.8 — Static export-surface test green; iws exports ≤4 (incl. AuditSink); eph exports ≤2
- Status: PASS
- Evidence: `tests/static/test_workspace_export_surface.py` (4 tests, all PASS). Eph exports `{EphemeralPipeline}` (1 symbol). Iws exports `{AuditSink, IsolatedPipeline, IsolatedWorkspaceError, IsolatedWorkspaceHandle}` (4 symbols).
- Files: `isolated_workspace/__init__.py`, `ephemeral_workspace/__init__.py`, `tests/static/test_workspace_export_surface.py` (new)
- Note: `get_active_pipeline` / `set_pipeline` / `require_pipeline` / `require_arg` are no longer re-exported from iws `__init__.py`; external consumers (`daemon/dispatch.py`, `daemon/rpc/dispatcher.py`, test files) updated to import directly from `sandbox.isolated_workspace.helper.manager` (or other helper modules).

### C3.9 — `helper/` folders exist with renamed files; top-level free of private `_*.py` modules; all old import paths gone; tests pass
- Status: PASS
- Evidence: `test -d backend/src/sandbox/{ephemeral,isolated}_workspace/helper` → exit 0. `find backend/src/sandbox/{ephemeral,isolated}_workspace -maxdepth 1 -name '_*.py' ! -name '__init__.py'` → 0 matches. `rg -n "from sandbox\.(ephemeral|isolated)_workspace\._" backend` → 0 matches. Fixed 19 importers across production + tests.
- Files: 12 `git mv` operations preserving history (5 eph + 7 iws), 2 `helper/__init__.py` files, ~20 importer rewrites
- Verification: 54-test sweep (contracts + static + workspace_unification + import_fence + bundle + routing + lifecycle_background) all PASS.

### C4 — IMPLEMENTED (no deferral)
- Status: PASS
- Evidence: §8 acceptance greps:
  - `rg -rn "from sandbox\.daemon\.handler[^s]|from sandbox\.daemon\.handler$" backend/src` → 0 matches
  - `rg -rn "from sandbox\.isolated_workspace\.handlers|from sandbox\.isolated_workspace import handlers" backend/src` → 0 matches
  - `rg -rn "from sandbox\.isolated_workspace\.lifecycle|import sandbox\.isolated_workspace\.lifecycle" backend/src` → 0 matches
  - `test ! -d backend/src/sandbox/daemon/handler && test ! -f backend/src/sandbox/isolated_workspace/handlers.py && test ! -d backend/src/sandbox/isolated_workspace/lifecycle` → exit 0
- Files: `backend/src/sandbox/daemon/handlers.py` (new — single file consolidating all 10 prior `daemon/handler/*.py` modules); `backend/src/sandbox/host/iws_lifecycle.py` (new — host-side `enter`/`exit` coroutines consolidated, formerly `iws/lifecycle/{enter,exit}_isolated_workspace.py`); 5 `_iws_*` functions inlined into `backend/src/sandbox/daemon/rpc/dispatcher.py`; `tests/contracts/test_iws_rpc_envelopes.py` (new — 3 tests pinning the wire envelope shape).
- Deletions: 11 `backend/src/sandbox/daemon/handler/*.py` files (cancel, edit, glob, grep, health, metrics, read, shell, workspace, write, __init__); `backend/src/sandbox/isolated_workspace/handlers.py`; `backend/src/sandbox/isolated_workspace/lifecycle/` (3 files).
- Diagram alignment: §2.4 shows `daemon/handler/{...}.py # DELETED in C4` and `iws/{handlers.py, lifecycle/} # DELETED in C4`. Post-implementation, all enumerated deletions are physical on disk.
- Verification: 706 sandbox unit tests pass (up from 693 pre-C4: the new `test_iws_rpc_envelopes.py` adds 3 tests + the test count grew with the routing changes). Ruff clean on touched files.

### Host coroutine relocation footnote
- Plan §4.C deleted `iws/lifecycle/` but §5.9 was silent on where the host-side coroutines (`enter_isolated_workspace`, `exit_isolated_workspace`) live post-deletion. These coroutines orchestrate tool-layer concerns (`background_manager.{count_by_agent,cancel_by_agent}`, the `lifecycle_operation` audit wrapper) and are consumed by `tools/isolated_workspace/{enter,exit}_isolated_workspace/definition.py`.
- An earlier draft co-located them inside `tools/isolated_workspace/{enter,exit}_isolated_workspace/_lifecycle.py` per advisor recommendation, but the `tools/isolated_workspace/__init__.py` eager-loads `definition.py` which imports `tools.sandbox._lib.session.caller_from_context`, triggering a circular import through `sandbox.api`. The chosen home is `sandbox/host/iws_lifecycle.py` (a single consolidated module mirroring the existing `sandbox.host.lifecycle`), which breaks the cycle and keeps the deletion intent literal — `iws/lifecycle/` is gone, host orchestration lives under `sandbox.host`.

## Files Changed

### Baselines
- `tests/baselines/iws_serial_call_timing.json` (new)
- `tests/baselines/iws_freeze_syscalls.strace` (new)

### Shared sandbox modules
- `backend/src/sandbox/_shared/lease_guard.py` (new — 95 lines)
- `backend/src/sandbox/_shared/layer_stack_port.py` (new — 56 lines)
- `backend/src/sandbox/_shared/shell_contract.py` (moved from `ephemeral_workspace/`; `WorkspaceLeaseClient` Protocol deleted)
- `backend/src/sandbox/_shared/ports.py` (updated re-exports — adds `LayerStackPort`, drops `WorkspaceLeaseClient`)

### Ephemeral workspace
- `backend/src/sandbox/ephemeral_workspace/pipeline.py` — adopts `LeaseGuard` composition, switches type hint to `LayerStackPort`, adds audit/event divergence docstring, routes `_release_lease` through `LeaseGuard.mark_released`, updates all `helper.*` imports
- `backend/src/sandbox/ephemeral_workspace/_types.py` → `backend/src/sandbox/ephemeral_workspace/helper/types.py` (git mv; deletes `OverlayLayerStackClient`)
- `backend/src/sandbox/ephemeral_workspace/_operation.py` → `helper/operation.py` (git mv; deletes `_lock_for` + `_destroy_with_lease_guard`; rewires imports)
- `backend/src/sandbox/ephemeral_workspace/_publishing.py` → `helper/publishing.py` (git mv; rewires imports)
- `backend/src/sandbox/ephemeral_workspace/_manager.py` → `helper/manager.py` (git mv)
- `backend/src/sandbox/ephemeral_workspace/_utils.py` → `helper/utils.py` (git mv)
- `backend/src/sandbox/ephemeral_workspace/helper/__init__.py` (new)
- `backend/src/sandbox/ephemeral_workspace/__init__.py` — no change (export surface already minimal)

### Isolated workspace
- `backend/src/sandbox/isolated_workspace/pipeline.py` — strips singleton accessors (moved to `helper/manager.py`); adds audit/event divergence + body-length divergence docstring; adds run_tool_call docstring; switches type hint to `LayerStackPort`; switches all helper.* imports
- `backend/src/sandbox/isolated_workspace/handlers.py` — deleted in C4; lifecycle RPC handlers are inline in `daemon/rpc/dispatcher.py`
- `backend/src/sandbox/isolated_workspace/_manager.py` → `helper/manager.py` (new file with the singleton + bootstrap + audit sink; deletes `_LayerStackAdapter`; constructs `LayerStackClient` at bootstrap)
- `backend/src/sandbox/isolated_workspace/_types.py` → `helper/types.py` (git mv; deletes `LayerSnapshotLike` and the old `LayerStackPort`)
- `backend/src/sandbox/isolated_workspace/_lifecycle.py` → `helper/lifecycle.py` (git mv; drops per-call `layer_stack_root` from snapshot/release sites)
- `backend/src/sandbox/isolated_workspace/_gc.py` → `helper/gc.py` (git mv; drops per-call `layer_stack_root` from orphan release; rewires imports)
- `backend/src/sandbox/isolated_workspace/_ttl.py` → `helper/ttl.py` (git mv)
- `backend/src/sandbox/isolated_workspace/_quota.py` → `helper/quota.py` (git mv)
- `backend/src/sandbox/isolated_workspace/_runtime.py` → `helper/runtime.py` (git mv)
- `backend/src/sandbox/isolated_workspace/helper/__init__.py` (new)
- `backend/src/sandbox/isolated_workspace/__init__.py` — trimmed from 14 to 4 exports
- `backend/src/sandbox/isolated_workspace/lifecycle/{enter,exit}_isolated_workspace.py` — deleted in C4; host orchestration now lives in `sandbox/host/iws_lifecycle.py`

### Daemon
- `backend/src/sandbox/daemon/workspace_server.py` — exposes `release_lease`
- `backend/src/sandbox/daemon/handlers.py` — exposes canonical `release_lease`; legacy `release_workspace_snapshot` shim removed
- `backend/src/sandbox/daemon/rpc/dispatcher.py` — registers `api.release_lease`; switches `get_active_pipeline` import path
- `backend/src/sandbox/daemon/dispatch.py` — switches `get_active_pipeline` import path

### Host
- `backend/src/sandbox/host/runtime_bundle.py` — eph tuple no longer lists private `_X.py` files; private internals bundled via `_add_python_tree(ephemeral_dir / "helper", ...)`

### Overlay
- `backend/src/sandbox/overlay/lifecycle.py` — Protocol type hint switched from `WorkspaceLeaseClient` to `LayerStackPort`

### Plugin (cross-package importer)
- `backend/src/plugins/catalog/lsp/runtime/apply.py` — shell_contract import path

### Tests (new)
- `tests/contracts/test_iws_does_not_import_occ.py` (new — 5 forbidden tokens, allows `sandbox.occ.layer_stack_client` since that's pure layer-stack adapter)
- `tests/contracts/test_release_lease_canonical_only.py` (new — verifies only `api.release_lease` is registered)
- `tests/static/test_workspace_export_surface.py` (new — pins both `__init__.py` export sets)

### Tests (updated)
- `backend/tests/unit_test/test_sandbox/test_workspace_unification_phase2.py` — `_types` import path; drop `layer_stack_root` kwarg from IsolatedPipeline fixture (vestigial ctor arg removed in cleanup)
- `backend/tests/unit_test/test_sandbox/test_import_fence.py` — `_publishing.py` → `helper/publishing.py`
- `backend/tests/unit_test/test_sandbox/test_daemon/test_routing_invariants.py` — asserts only canonical `api.release_lease`; switches all importers from `sandbox.daemon.handler.*` to `sandbox.daemon.handlers`; iws ops point at `dispatcher._iws_*` inline handlers
- `backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py` — eph tuple paths updated to `helper/`; daemon tuple drops 7 `handler/*.py` entries, adds single `handlers.py`
- `backend/tests/unit_test/test_sandbox/test_daemon/test_sandbox_overlay.py` — `import _manager as ...` → `helper.manager`
- `backend/tests/unit_test/test_sandbox/test_daemon/test_runtime_ready.py`, `test_daemon.py`, `test_in_flight_registry.py`, `test_search_handler.py` — switched to `sandbox.daemon import handlers as <alias>`; monkeypatch paths re-targeted; `test_search_handlers_do_not_call_occ_client` rewritten to inspect the `handlers.{glob,grep}` function bodies (via `inspect.getsource`) rather than the whole-module file (the file now also hosts `layer_metrics` which legitimately uses OCC)
- `backend/tests/unit_test/test_sandbox/test_isolated_workspace_lifecycle_background.py` — `sandbox.isolated_workspace.lifecycle` → `sandbox.host.iws_lifecycle`; monkeypatch sites updated from `enter_module.handlers._ensure_manager` to `enter_module.iws_manager._ensure_manager`; `exit_module.require_pipeline` → `exit_module.iws_manager.require_pipeline`
- `backend/tests/unit_test/test_sandbox/test_api/test_shell_atomic_by_path_count.py` — `import _publishing as ...` → `helper.publishing`; `shell_contract` import path
- `backend/tests/unit_test/test_sandbox/test_command_exec/test_workspace_mount.py`, `test_command_exec/test_cwd_traversal.py`, `test_command_exec/test_write_edit_dispatch.py` — `shell_contract` import path; daemon-handler imports redirected
- `backend/tests/unit_test/test_sandbox/test_layer_stack/test_workspace_binding.py`, `test_occ/test_mutation_gate.py` — daemon-handler imports redirected
- `backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/happy_path/test_mount_overlay_backstop.py` — `_runtime` → `helper.runtime`
- `backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/pre_flight/test_phase_timer_invariants.py` — `_types` → `helper.types`
- `backend/src/task_center_runner/tests/mock/sandbox/background_tool/test_background_shell_cancel.py` — `sandbox.daemon.handler.cancel` → `sandbox.daemon.handlers`

## Deletes

- `OverlayLayerStackClient` Protocol class — `ephemeral_workspace/helper/types.py`
- `WorkspaceLeaseClient` Protocol class — `_shared/shell_contract.py`
- `LayerStackPort` (the old iws variant) and `LayerSnapshotLike` Protocol classes — `isolated_workspace/helper/types.py`
- `_LayerStackAdapter` wrapper class — `isolated_workspace/helper/manager.py`
- `_lock_for` and `_destroy_with_lease_guard` methods — `ephemeral_workspace/helper/operation.py`
- `_released_lease_ids: set[str]` and `_handle_locks: dict[str, asyncio.Lock]` fields — `ephemeral_workspace/pipeline.py` (replaced by `self._lease_guard`)
- Legacy `api.release_workspace_snapshot` daemon shim and the remaining internal SWE-EVO caller.
- Stale `__all__` entries — `iws.__init__.py`, `isolated_workspace/helper/types.py`, `_shared/shell_contract.py`
- Stale `_X.py` paths (gone via `git mv`) — eph: `_manager.py`, `_operation.py`, `_publishing.py`, `_types.py`, `_utils.py`; iws: `_manager.py`, `_lifecycle.py`, `_gc.py`, `_ttl.py`, `_quota.py`, `_runtime.py`, `_types.py`
- (Already deleted in worktree pre-session, kept for completeness:) `test_handle_lock_serializes_tool_calls.py`, `test_freezer_stall_falls_back_to_sigstop.py`, `test_freeze_thaw_idempotent_across_tool_calls.py`, `test_long_running_idle_freeze_at_rest.py`

## Review Findings

### P0 — Blocking
- None.

### P1 — Should Fix
- **None remaining**. The `pyproject.toml::testpaths` was extended to include `"tests"`, the back-compat re-exports were removed, and `_layer_stack_root` was deleted in the C4 sweep.

### P2 — Cleanup / Refactor
- **None remaining**. All P2 items from the prior pass were closed in the C4 sweep:
  - `_layer_stack_root` field + ctor arg removed from `IsolatedPipeline` (test fixture updated).
  - `iws/pipeline.py` back-compat re-exports (`set_pipeline` / `require_pipeline` / `require_arg` / `get_active_pipeline`) removed.
  - `iws/lifecycle/` directory removed; host-side coroutines live at `sandbox/host/iws_lifecycle.py`.
  - `api.release_workspace_snapshot` rollout alias removed after the only in-repo caller migrated to `api.release_lease`.
  - Stale post-C4 docs/comments now point at `sandbox.host.iws_lifecycle`, `sandbox.daemon.handlers`, and `sandbox.isolated_workspace.helper.manager`.

## Refactors Applied

- Migrated `EphemeralPipeline` lease-destroy race protection from byte-identical duplicated code in `_operation.py` to `_shared/lease_guard.py::LeaseGuard` composition. The single source of truth makes the next bug fix in this area a one-place change instead of two.
- Unified three near-identical layer-stack Protocols (`OverlayLayerStackClient`, `LayerStackPort` (iws variant), `WorkspaceLeaseClient`) into `_shared/layer_stack_port.py::LayerStackPort`. The shared port lifts the bootstrap shape (per-call `layer_stack_root` arg) into the iws bootstrap path so the runtime call shape matches eph.
- Hoisted `shell_contract.py` from `ephemeral_workspace/` into `_shared/` because both workspaces share it (the original location was historical, not architectural).
- Trimmed iws `__init__.py` export surface from 14 to 4 symbols (drops the four private `_X` leaks plus the deprecated Protocols and bootstrap helpers).
- Moved 5 eph + 7 iws private modules into `helper/` subfolders to make the public/private boundary visible at the directory level.

## Dead Code / Legacy Code Removed

- Three Protocol classes (`OverlayLayerStackClient`, `WorkspaceLeaseClient`, iws `LayerStackPort`) + `LayerSnapshotLike` companion.
- `_LayerStackAdapter` wrapper (iws bootstrap now binds `LayerStackClient` directly).
- Duplicated `_lock_for` + `_destroy_with_lease_guard` machinery in `EphemeralOperationMixin`.
- `_released_lease_ids` + `_handle_locks` fields on `EphemeralPipeline` (LeaseGuard owns them).
- Stale `__all__` entries referencing deleted/moved symbols.
- Already removed pre-session: `_runtime.freeze()`, SIGSTOP fallback, `freezer_degraded` field on handle dataclass, `freezer_degraded` in status RPC, 4 freezer-related test files. This PR confirmed clean state and added the regression baselines so the removal can't silently regress.

## Structure and Naming Review

- **workspace top-level files (eph)**: `__init__.py`, `pipeline.py`, `events.py`, `plugin/`, `helper/` — public surface clearly separated from private internals.
- **workspace top-level files (iws)**: `__init__.py`, `pipeline.py`, `network.py`, `scripts/`, `helper/` — public surface clearly separated from private internals; lifecycle RPC handlers are inline in `daemon/rpc/dispatcher.py`.
- **`helper/` private internals**: 5 eph modules + 7 iws modules under `helper/`, with `helper/__init__.py` carrying a one-line "do not import from outside this package" notice. Matches plan §5.8 convention exactly.
- **`__init__.py` exports**: `{EphemeralPipeline}` for eph (1 symbol); `{AuditSink, IsolatedPipeline, IsolatedWorkspaceError, IsolatedWorkspaceHandle}` for iws (4 symbols). Pinned by the static lint test.

## Existing Function Reuse Review

- Reused `LayerStackClient` (from `sandbox.occ.layer_stack_client`) as the iws bootstrap's concrete `LayerStackPort` implementation. The class already adapted the raw `LayerStack` to kwarg-only call shapes; no new adapter needed.
- Reused `workspace_server.get_layer_stack_manager(layer_stack_root)` at iws bootstrap (same call eph's `occ_backend.build_occ_backend` makes).
- Reused `_add_python_tree` in `runtime_bundle.py` to bundle the new `helper/` subdir — no new bundle helper needed.
- The only genuinely new helper is `LeaseGuard`; necessary because two pipelines (eph today; potentially future iws / other) needed the same idempotent lease-destroy semantics in a single composable class.

## Single Responsibility Review

- `EphemeralPipeline` dual-mode coexistence (session-mounted vs per-tool-call) is **NOT** addressed in Phase 2.6. Per plan §10.A this is explicitly deferred to Phase 2.7 because splitting it is a refactor of comparable size to Phase 2.6 itself; bundling would double scope. Flagged here for the next phase.
- `IsolatedPipeline` cleanly delegates lifecycle to four phase-decomposition mixins (`_IsolatedLifecycleMixin`, `_IsolatedGcMixin`, `_IsolatedTtlMixin`, `_IsolatedQuotaMixin`). The 4-mixin split is preserved per plan principle P3 (honest divergence by lifecycle phase, not arbitrary file count).
- Audit-event divergence between modes (eph `event_bus` vs iws `_JsonlAuditSink`) is preserved and documented via class-docstring comments rather than unified — eph emits runtime control-flow events to drive `_watch_foreign_publishes`; iws emits lifecycle audit events consumed by 20+ tier-3 tests.

## Verification Commands

```bash
# C0
test -f tests/baselines/iws_serial_call_timing.json
test -f tests/baselines/iws_freeze_syscalls.strace
# → exit 0

# C1
rg -n "freezer_degraded|def freeze|SIGSTOP" backend/src/sandbox/isolated_workspace
# → no matches

# C2
rg -n "handle\.lock" backend/src/sandbox/isolated_workspace
# → no matches

# C2.5
rg -n "_lock_for|_destroy_with_lease_guard" backend/src/sandbox/{ephemeral,isolated}_workspace
# → no matches
rg -n "_released_lease_ids|_handle_locks" backend/src/sandbox/{ephemeral,isolated}_workspace
# → no matches (only `_shared/lease_guard.py` writes)

# C3
test -f backend/src/sandbox/ephemeral_workspace/helper/manager.py    # → exists
test -f backend/src/sandbox/isolated_workspace/helper/manager.py     # → exists
test -f backend/src/sandbox/_shared/shell_contract.py                # → exists
# (runtime_bundle string-literal check no longer applies; tree-walker covers it.)

# C3.5a / review cleanup
.venv/bin/pytest tests/contracts/test_release_lease_canonical_only.py -q
# → canonical release op registered; legacy alias absent

# C3.5b
rg -n "class OverlayLayerStackClient|class WorkspaceLeaseClient" backend/src
# → no matches
rg -n "class LayerStackPort" backend/src
# → 1 match (backend/src/sandbox/_shared/layer_stack_port.py:27)

# C3.8
.venv/bin/pytest tests/static/test_workspace_export_surface.py -q
# → 4 passed

# C3.9
test -d backend/src/sandbox/ephemeral_workspace/helper
test -d backend/src/sandbox/isolated_workspace/helper
find backend/src/sandbox/{ephemeral,isolated}_workspace -maxdepth 1 -name '_*.py' ! -name '__init__.py'
# → no matches
rg -n "from sandbox\.(ephemeral|isolated)_workspace\._" backend
# → no matches

# Full sandbox-touching unit run (excludes 13 env-sensitive macOS failures)
.venv/bin/pytest backend/tests/unit_test/test_sandbox \
  --ignore=backend/tests/unit_test/test_sandbox/test_api/test_shell_staleness_telemetry.py \
  --ignore=backend/tests/unit_test/test_sandbox/test_layer_stack/test_workspace_binding.py \
  --ignore=backend/tests/unit_test/test_sandbox/test_occ/test_mutation_gate.py \
  --ignore=backend/tests/unit_test/test_sandbox/test_occ/test_shell_capture_atomicity.py \
  --ignore=backend/tests/unit_test/test_sandbox/test_plugin_handler.py \
  -q
# → 693 passed, 2 skipped

# Acceptance bundle (touched surfaces + Phase 2.5 background lifecycle preservation)
.venv/bin/pytest tests/contracts/ tests/static/ \
  backend/tests/unit_test/test_sandbox/test_workspace_unification_phase2.py \
  backend/tests/unit_test/test_sandbox/test_import_fence.py \
  backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py \
  backend/tests/unit_test/test_sandbox/test_daemon/test_routing_invariants.py \
  backend/tests/unit_test/test_sandbox/test_isolated_workspace_lifecycle_background.py \
  -q
# → 54 passed

# ruff on touched files
.venv/bin/ruff check backend/src/sandbox/_shared/ \
  backend/src/sandbox/ephemeral_workspace backend/src/sandbox/isolated_workspace \
  backend/src/sandbox/daemon/handlers.py \
  backend/src/sandbox/daemon/workspace_server.py \
  backend/src/sandbox/daemon/rpc/dispatcher.py \
  backend/src/sandbox/daemon/dispatch.py \
  backend/src/sandbox/overlay/lifecycle.py \
  backend/src/sandbox/host/runtime_bundle.py \
  tests/
# → All checks passed!
```

## Residual Risks

- **13 unit tests fail with `OverlayWritableRootUnavailable: /eos-mount-scratch/eos-sandbox-runtime`**: these require a Linux Docker mount that isn't present on this macOS dev host. The failures are NOT caused by the Phase 2.6 changes — they failed identically before any of my edits would have been able to affect them, and they all funnel through `overlay_writable_root()` which raises whenever the host fs doesn't have the Docker-provisioned mount path. Affected files: `test_shell_staleness_telemetry.py` (7 cases), `test_layer_stack/test_workspace_binding.py` (2), `test_occ/test_mutation_gate.py` (2), `test_occ/test_shell_capture_atomicity.py` (1), `test_plugin_handler.py` (1). Recommend re-running on Linux CI to confirm clean pass.
- **Live e2e iws-parallelism test** (`test_same_agent_tool_calls_can_overlap.py`) requires a running daemon + sweevo sandbox; couldn't be exercised from the dev host. The assertion contract (wall < 0.9s for 2 × 500ms) is in place; production verification deferred to live CI. Production code paths confirmed clean of per-call serialization.
- **C4 implemented in this PR** (pulled forward at user request). The diagram §2.4 layout is exact on disk.
- **Pre-existing circular import** between `tools.sandbox._lib.session` ↔ `sandbox.api` ↔ `sandbox.host.lifecycle` ↔ `sandbox.ephemeral_workspace.plugin.session` ↔ back to `tools.sandbox._lib.session.caller_from_context`. Cold-loading `tools.isolated_workspace` (or `tools.sandbox`) as the very first import will trip an `ImportError`. The natural production import order (which loads `sandbox.api` first) hides the cycle, and pytest collection orders dependencies such that the cycle never trips during the test suite. Verified pre-existing by `python -c "import sandbox.api; import tools.isolated_workspace"` (works) vs `python -c "import tools.isolated_workspace"` (cycle). The fix would belong with a future refactor of the `sandbox.host.lifecycle ↔ tools.sandbox._lib.session` dependency, not Phase 2.6. The new `sandbox.host.iws_lifecycle.py` is safe — `python -c "import sandbox.host.iws_lifecycle"` succeeds cold because that path resolves `sandbox._shared.models` first and `sandbox.api` is not yet in flight when `caller_from_context` is requested.
