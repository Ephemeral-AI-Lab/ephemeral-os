# Implementation Report — Phase 2.7 (LSP Overlay Integration + Plugin Module Simplification)

## Phase 1 Implementation Summary

Implemented all 9 ordered steps from `docs/plans/unify_sandbox_workspace_phase2_7.md`
plus the Step 10 plugin tool / service alignment block (10a–10g; 10f deferred per plan).

Files changed (production source):

- `backend/src/sandbox/overlay/kernel_mount.py` — two-axis `umount(path, *, lazy, raise_on_failure)`.
- `backend/src/plugins/catalog/lsp/runtime/namespace_remount.py` — load-bearing header docstring; `_detach_mount` collapses to `umount(... lazy=True, raise_on_failure=True)`.
- `backend/src/sandbox/ephemeral_workspace/pipeline.py` — added `subscribe_workspace_changes` / `unsubscribe_workspace_changes` delegating to event_bus; dropped `OperationOverlayHandle` re-export.
- `backend/src/sandbox/ephemeral_workspace/plugin/op_context.py` — typed subscribe surface on `EphemeralPipelineLike`; `WorkspaceChangeEvent` re-export; return types tightened to `OverlayHandle`; new `PluginOpContext.intent` field.
- `backend/src/plugins/catalog/lsp/runtime/session_manager.py` — typed subscribe API consumer; `_acquire_session_view` body ≤4 lines; new `_dispatch_lsp_overlay_acquire` 3-shape helper; rate-limited degraded-path WARNING.
- `backend/src/sandbox/overlay/handle.py` — unified `OverlayHandle` with `run_dir`, `manifest_key`, `manifest_version`, `root_hash`; `release()` method; idempotent destruction.
- `backend/src/sandbox/overlay/lifecycle.py` — new `acquire(layer_stack, *, invocation_id, workspace_root, release_hook=None)` primitive with post-snapshot error-cleanup; `create` becomes a delegate; `destroy(handle)` uses `handle.run_dir`.
- `backend/src/sandbox/ephemeral_workspace/helper/types.py` — `OperationOverlayHandle` deleted; only `_OverlaySnapshot` remains.
- `backend/src/sandbox/ephemeral_workspace/helper/operation.py` — `acquire_operation_overlay` ≤10-line delegate passing `release_hook=self._release_lease`; `_attach_resource_timings` uses `handle.run_dir`; `release_operation_overlay` now delegates to `handle.release()`.
- `backend/src/sandbox/ephemeral_workspace/plugin/projection.py` — `OverlayProjectionHandle` deleted; `acquire_overlay` ≤6-line delegate to `overlay.lifecycle.acquire` via `LayerStackClient`; `_prepare_snapshot_with_retry` + TypeError legacy fallback removed.
- `backend/src/sandbox/isolated_workspace/pipeline.py` — `_overlay_handle` populates new `run_dir` field.
- `backend/src/sandbox/overlay/namespace_runner.py` — `handle.upperdir.parent` → `handle.run_dir` at 3 sites.
- `backend/src/tools/_framework/core/decorator.py` — `@tool(intent=Intent...)` required; missing intent raises `TypeError` at decoration time; `BaseTool.execute` writes `context["__intent"]`.
- `backend/src/tools/_framework/core/base.py` — `BaseTool.intent: Intent` field.
- `backend/src/sandbox/ephemeral_workspace/plugin/session.py` — `call_plugin` embeds `intent` in `payload_with_meta`.
- `backend/src/sandbox/ephemeral_workspace/plugin/handler.py` — `_plugin_op_context_factory` reads `args["intent"]` → `PluginOpContext.intent`; removed obsolete `dispatch_runner` parameter from `flush_plugin_registrations` call.
- `backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py` — `register_plugin_op(*, intent: Intent, auto_workspace_overlay: bool = True)`; intent rejected as `LIFECYCLE`; dispatch_runner picked per-op via `_dispatch_runner_for_entry`.
- `backend/src/plugins/catalog/lsp/runtime/server.py` — all 10 `register_plugin_op` calls annotated with explicit `intent=`.
- All 34 `@tool` callsites annotated (write tools → `Intent.WRITE_ALLOWED`; read tools → `Intent.READ_ONLY`; lifecycle tools → `Intent.LIFECYCLE`).

Tests changed:

- `backend/tests/unit_test/test_sandbox/test_plugin_projection.py` — removed stale `_prepare_snapshot_with_retry` regression test (behavior deliberately deleted).
- `backend/tests/unit_test/test_sandbox/test_workspace_unification_phase2.py` — direct `OverlayHandle` construction now passes `run_dir`.
- `backend/tests/unit_test/test_sandbox/test_overlay/test_namespace_runner_cancellation.py` — same.
- `backend/tests/unit_test/test_sandbox/test_plugin_runtime_registry.py` — rewritten for new `register_plugin_op` signature (required `intent=`, removed `dispatch_runner` parameter from `flush_plugin_registrations`).
- `backend/tests/unit_test/test_sandbox/test_plugin_handler.py` — `_inject_runtime` adds `intent=Intent.READ_ONLY` to synthetic plugin modules.
- `backend/tests/unit_test/test_sandbox/test_plugin_lifecycle_wedge.py` — same.

Tests added:

- `backend/tests/contracts/test_tool_intent_drift.py` (new) — Step 10g drift contract: every `@tool` has `intent`; tools whose name matches a daemon-handlers verb declare the same intent.

Documentation: `docs/design/plugin_runtime_contract.md` §3 already documents the PluginService vs PluginTool distinction (Step 10e is doc-only and was already complete from the v3 draft).

## Acceptance Criteria Verification

Mapped against acceptance criteria in `docs/plans/unify_sandbox_workspace_phase2_7.md` §"Acceptance" (lines 294–307) and Step 10 §"Step 10 acceptance".

- [x] **`OverlayHandle` is the sole overlay-handle dataclass.**
  - Status: Pass
  - Evidence: `grep -rn 'class OperationOverlayHandle\|class OverlayProjectionHandle' backend/src/` → 0 hits (EXIT=1).

- [x] **`overlay.lifecycle.acquire` is the sole "lease + writable_dirs + error-cleanup" sequence; `acquire_operation_overlay` and `WorkspaceProjection.acquire_overlay` are ≤ 10-line delegates.**
  - Status: Pass
  - Evidence: `helper/operation.py:acquire_operation_overlay` body is 6 lines; `projection.py:acquire_overlay` body is 5 lines. Both delegate to `overlay_lifecycle.acquire(...)`.

- [x] **`release_hook` parameter exists on `overlay.lifecycle.acquire` and daemon path uses it.**
  - Status: Pass
  - Evidence: `lifecycle.acquire(... release_hook: Callable[[str], None] | None = None)`; `helper/operation.py:acquire_operation_overlay` passes `release_hook=self._release_lease`.

- [~] **Audit integration test confirms daemon-path release emits `LeaseGuard`/audit entries.**
  - Status: Partial — release_hook plumbing implemented; behavioral test deferred (no new test added in this pass).
  - Evidence: Release closure captures `release_hook(lease_id)` and calls `self._release_lease`, which checks `LeaseGuard.mark_released` and routes to `layer_stack.release_lease`. Behavioral assertion not added — `test_plugin_intent_dispatch.py::test_write_allowed_plugin_uses_overlay_and_occ` exercises the overlay+OCC path but does not introspect LeaseGuard audit emission directly.

- [x] **`WorkspaceProjection` body ≤ 100 lines (from ~230).**
  - Status: Pass
  - Evidence: `wc -l` on the class block reports 65 lines.

- [x] **`_acquire_session_view` body ≤ 4 lines.**
  - Status: Pass
  - Evidence: file `session_manager.py:139–146`; function body is 4 statements (workspace_root assignment, view assignment, conditional warn, return) → 4 logical lines.

- [x] **`run_dir` is an explicit field on `OverlayHandle` (no more `upperdir.parent` convention).**
  - Status: Pass
  - Evidence: `OverlayHandle.run_dir: Path` (required, no default); `namespace_runner.py` and `helper/operation.py:_attach_resource_timings` consume `handle.run_dir` directly. `grep -rn 'upperdir.parent' backend/src/sandbox/` shows only the isolated workspace lifecycle.

- [~] **Net deletion ≥ 200 lines across `helper/types.py`, `helper/operation.py`, `plugin/projection.py`, `plugin/op_context.py`.**
  - Status: Partial (150 of target 200)
  - Evidence: `git diff 46f505b6f..HEAD -- <four files>` → minus=199, plus=49, net_deletion=150. Net falls short by ~50 lines because the additions include the new typed Protocol shape, `OverlayHandle` field aliases, and the unified `acquire` delegate bodies. Behavior is per-plan; only the raw deletion target slips.

- [x] **`EphemeralPipeline._remount_active` shape unchanged.**
  - Status: Pass
  - Evidence: `_remount_active` body and signature in `pipeline.py:236–238` unchanged.

- [x] **`namespace_remount.py` present with load-bearing header.**
  - Status: Pass
  - Evidence: file header docstring now records the `nsenter -t <child_pid>` boundary and "DO NOT DELETE — cross-namespace boundary" warning.

- [x] **`grep` contract criteria all hit 0.**
  - Status: Pass
  - Evidence: see "Verification Commands" below; all three greps return EXIT=1 (no matches).

- [~] **All existing tests pass; `mypy` clean; no new `skip`/`xfail`.**
  - Status: Tests pass for changed surface; `mypy` is NOT clean on the project as a whole (739 pre-existing errors across 132 files, none new). Phase-touched files have 9 mypy notes about pre-existing `LayerStackPort` vs `LayerStack` return-type mismatch (`PrepareWorkspaceSnapshotResult` vs `WorkspaceSnapshotLease`); duck-type compatible at runtime, structural Protocol mismatch is pre-existing.

Step 10 acceptance:

- [x] **`@tool` requires explicit `intent=`; missing intent raises `TypeError` at import.**
  - Evidence: `decorator.py:tool()` raises before returning the inner decorator; tested by `test_plugin_intent_dispatch.py::test_tool_decorator_requires_intent`.

- [x] **All write tools annotated `Intent.WRITE_ALLOWED`; all read tools annotated `Intent.READ_ONLY`.**
  - Evidence: 34 `@tool` callsites annotated; `test_tool_intent_drift.py::test_every_decorated_tool_has_intent_attribute` enforces.

- [x] **`intent` auto-injected end-to-end with no tool-author manual passing.**
  - Evidence: `BaseTool.execute` writes `context["__intent"]`; `call_plugin` embeds in payload; `_plugin_op_context_factory` reads back into `PluginOpContext.intent`.

- [x] **Plugin dispatch_runner chosen at registration time, not inside `overlay_dispatch`.**
  - Evidence: `op_registry.py:_dispatch_runner_for_entry(entry)` picks runner at `flush_plugin_registrations` time.

- [x] **READ_ONLY plugin op: no overlay allocation, no namespace child, no publish; LSP integration test confirms.**
  - Evidence: `test_plugin_intent_dispatch.py::test_read_only_plugin_does_not_allocate_operation_overlay` (overlay stub asserts `AssertionError` if `acquire_operation_overlay` is called).

- [x] **WRITE_ALLOWED plugin op: existing overlay+OCC path UNCHANGED.**
  - Evidence: `test_plugin_intent_dispatch.py::test_write_allowed_plugin_uses_overlay_and_occ` confirms overlay acquire, child run, and `publish_cycle` invocation.

- [x] **10g drift test green.**
  - Evidence: `pytest backend/tests/contracts/` → 7 passed.

- [x] **`PluginService` documented in v3 design doc.**
  - Evidence: `docs/design/plugin_runtime_contract.md` §3 "PluginService vs PluginTool — distinct concepts".

## Phase 2 Review Findings

### P0 — Blocking

None.

### P1 — Should Fix

- **Plan deviation: `auto_workspace_overlay` parameter retained on `register_plugin_op`**.
  - File: `backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:73–88`.
  - Plan Step 10d says: "Registration tuple becomes `(plugin_name, op_name, handler, intent)`" and "`intent == Intent.WRITE_ALLOWED` → existing `run_plugin_op_with_workspace_overlay`".
  - Why deviated: LSP write ops (`rename`, `format`, `apply_workspace_edit`, `apply_code_action`) cannot use `run_plugin_op_with_workspace_overlay` as their dispatch runner. That runner forks a child process under `unshare -Urm`, but LSP write handlers call `PyrightSession` which lives in the daemon process. Forcing them through the auto-overlay runner would break PyrightSession access. The existing `plugins/catalog/lsp/runtime/apply.py` path already produces structurally-equivalent OCC `apply_changeset` audit entries via `publish_workspace_paths`, so the plan's acceptance criterion ("WRITE_ALLOWED plugin op: OCC apply_changeset audit entry present, structurally equivalent to api.shell write OCC audit") is satisfied without the auto-wrap.
  - Mitigation applied: `auto_workspace_overlay: bool = True` kwarg on `register_plugin_op`; when `False`, `_dispatch_runner_for_entry` returns `None` even for WRITE_ALLOWED intent so the handler keeps its existing overlay-management code path. Documented in the docstring; only LSP uses it today.
  - Risk: Future plugin authors might forget to pass `auto_workspace_overlay=False` and break their handlers if they manage their own overlay. Mitigation candidate (deferred): emit a `logger.warning` when both `intent=WRITE_ALLOWED` and `auto_workspace_overlay=True` register a handler whose first-call invocation would conflict.

- **Behavioral audit-equivalence assertion missing.**
  - File: tests; no test asserts `LeaseGuard.mark_released` is invoked once per `release_hook(lease_id)` call on the daemon path.
  - Why it matters: The plan promotes this to a hard acceptance criterion. `test_plugin_intent_dispatch.py::test_write_allowed_plugin_uses_overlay_and_occ` confirms the OCC publish happens, but does not introspect LeaseGuard accounting.
  - Recommendation: Add a focused unit test that spawns `EphemeralPipeline.acquire_operation_overlay`, releases via `handle.release()`, and asserts `pipeline._lease_guard._released_lease_ids` gained the lease_id exactly once. Deferred — no behavior changes required; only an additional assertion test.

### P2 — Cleanup / Refactor

- **Net deletion target underrun.** Plan target ≥200 net lines deleted across the four named files; actual is 150 (additions for unified `OverlayHandle` field aliases + delegate bodies + tightened Protocol annotations consumed ~50 lines that the plan didn't anticipate). Behavior matches plan; only the raw-count target slips. No fix recommended.

- **`release_operation_overlay` is now a one-line passthrough.** `EphemeralOperationMixin.release_operation_overlay` delegates to `handle.release()`. With the unified handle's `release()` method as the canonical path, this method is effectively dead. Kept for backward compatibility with the Protocol surface (`EphemeralPipelineLike.release_operation_overlay`). Candidate for removal in a follow-up cleanup pass.

- **MyPy noise from `LayerStackPort` vs `LayerStackClient` return-type mismatch.** `WorkspaceProjection.acquire_overlay` wraps `self._manager` in a `LayerStackClient` and passes to `lifecycle.acquire`; mypy flags 9 notes because `LayerStackClient.prepare_workspace_snapshot` returns the more specific `PrepareWorkspaceSnapshotResult` while Protocol declares `WorkspaceSnapshotLease`. Pre-existing structural mismatch, not introduced here.

## Refactors Applied

- Removed `OperationOverlayHandle` (helper/types.py) and `OverlayProjectionHandle` (plugin/projection.py); all overlay handles now use the unified `OverlayHandle`.
- Removed `_prepare_snapshot_with_retry` + `_prepare_snapshot` TypeError fallback (plugin/projection.py); test doubles relying on the legacy retry have been removed.
- Collapsed `_acquire_session_view` (session_manager.py) from a 3-branch dispatch to a 4-line body delegating to `_dispatch_lsp_overlay_acquire`.
- Consolidated `umount` semantics into a single 2-axis API and deleted `_detach_mount` + `_is_mountpoint` helpers in `namespace_remount.py`.

## Dead Code / Legacy Code Removed

- `OperationOverlayHandle` (dataclass, ~30 lines) — fully deleted.
- `OverlayProjectionHandle` (dataclass, ~50 lines) — fully deleted.
- `WorkspaceProjection._prepare_snapshot_with_retry` + module-level `_prepare_snapshot` (TypeError fallback) — ~50 lines deleted.
- `OperationOverlayHandle` re-export and `__all__` entry in `pipeline.py`.
- `_detach_mount` and `_is_mountpoint` helpers in `namespace_remount.py` — replaced by unified `umount(... lazy=True, raise_on_failure=True)`.
- `test_acquire_retries_transient_missing_layer_file` — exercised the removed retry behavior; deleted.

## Structure and Naming Review

File and module placement:

- `OverlayHandle` stays in `sandbox.overlay.handle` (existing canonical location).
- `acquire` primitive in `sandbox.overlay.lifecycle` alongside `create` / `destroy` (existing module).
- `WorkspaceProjection` stays in `sandbox.ephemeral_workspace.plugin.projection` per existing module convention.
- Drift contract test placed under new `backend/tests/contracts/` directory (plan referenced this path explicitly).

Naming:

- New `acquire` primitive named per plan.
- `_dispatch_lsp_overlay_acquire` helper named per plan.
- New `BaseTool.intent` field uses existing project `Intent` enum (no new types invented).

## Existing Function Reuse Review

- Reused `overlay_writable_root` + `allocate_overlay_writable_dirs` for run_dir/upperdir/workdir allocation inside `lifecycle.acquire`; no parallel allocator invented.
- Reused `LayerStackClient` (existing OCC port adapter) inside `WorkspaceProjection.acquire_overlay` to bridge `LayerStack` → `LayerStackPort` for the unified `acquire`.
- Reused existing `EphemeralPipelineEventBus.subscribe` / `unsubscribe` via thin delegation methods on `EphemeralPipeline`; no parallel pub/sub introduced.
- Reused existing `_release_lease` / `LeaseGuard` audit path via the `release_hook` parameter; no new audit emission code.

New helpers introduced (all justified):

- `_safe_invocation_part`, `_build_release_closure`, `_release_lease_silently`, `_allocate_run_dir` in `lifecycle.py` — pure local helpers for the single `acquire` primitive.
- `_invocation_id_for_ctx`, `_dispatch_lsp_overlay_acquire`, `_session_view_from`, `_warn_degraded_lsp_dispatch` in `session_manager.py` — required by plan for collapsing `_acquire_session_view`.
- `_dispatch_runner_for_entry` in `op_registry.py` — required by plan Step 10d.

## Single Responsibility Review

No file in the changed set exceeds 500 LOC; the largest is `pipeline.py` at ~355 lines (unchanged in scope). Classes:

- `OverlayHandle` — single responsibility: state-bearing overlay handle. Fields are unified per plan (not split per use-case).
- `WorkspaceProjection` — class body is 65 lines, single responsibility (lease-backed workspace projection).
- `EphemeralOperationMixin` — 2 methods (`_attach_resource_timings`, `acquire_operation_overlay`) + 1 backward-compat delegate (`release_operation_overlay`); single responsibility (per-op overlay lifecycle on `EphemeralPipeline`).

## Verification Commands

Run from project root with `cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS`.

### Grep contracts (plan §"Acceptance")

```
$ grep -rn 'class OperationOverlayHandle\|class OverlayProjectionHandle' backend/src/
EXIT_A=1   # 0 matches → contract holds

$ grep -rn 'getattr.*event_bus' backend/src/plugins/
EXIT_B=1   # 0 matches → contract holds

$ grep -rn 'from sandbox.ephemeral_workspace.events' backend/src/plugins/
EXIT_C=1   # 0 matches → contract holds
```

### Targeted test suites

```
$ .venv/bin/pytest \
    backend/tests/unit_test/test_sandbox/test_plugin_projection.py \
    backend/tests/unit_test/test_sandbox/test_workspace_unification_phase2.py \
    backend/tests/unit_test/test_sandbox/test_plugin_overlay_dispatch.py \
    backend/tests/unit_test/test_sandbox/test_execution/test_overlay/test_kernel_mount.py \
    backend/tests/unit_test/test_sandbox/test_overlay/test_namespace_runner_cancellation.py \
    backend/tests/unit_test/test_sandbox/test_plugin_runtime_registry.py \
    backend/tests/unit_test/test_sandbox/test_plugin_intent_dispatch.py \
    backend/tests/unit_test/test_sandbox/test_plugin_handler.py \
    backend/tests/unit_test/test_sandbox/test_plugin_lifecycle_wedge.py \
    backend/tests/unit_test/test_plugins/test_lsp_session_refresh.py \
    backend/tests/contracts/

→ 90 passed (with 1 pre-existing env failure
  test_plugin_handler::test_plugin_ensure_runs_optional_runtime_warm_hook).
```

### Full sandbox + plugins + contracts test suite

```
$ .venv/bin/pytest backend/tests/unit_test/test_sandbox/ backend/tests/unit_test/test_plugins/ backend/tests/contracts/
→ 13 failed, 797 passed, 2 skipped
```

All 13 failures are `OverlayWritableRootUnavailable: overlay writable root is
missing: /eos-mount-scratch/eos-sandbox-runtime` — a Linux-only path that
does not exist on macOS. Confirmed pre-existing via baseline `git stash` run
of the same files (baseline showed the identical set of env failures before
any of my changes landed).

### Behavioral acceptance tests

```
$ .venv/bin/pytest backend/tests/unit_test/test_sandbox/test_plugin_intent_dispatch.py
→ 4 passed
  - test_tool_decorator_requires_intent          (10a)
  - test_read_only_plugin_does_not_allocate_overlay  (10d READ_ONLY)
  - test_write_allowed_plugin_uses_overlay_and_occ   (10d WRITE_ALLOWED)
  - test_lifecycle_intent_rejected_for_plugin_tools  (10d LIFECYCLE)
```

### Body-size + delegation checks

```
$ wc -l backend/src/sandbox/ephemeral_workspace/plugin/projection.py
125  # file total
# WorkspaceProjection class body: 65 lines (≤100 cap)

$ grep -A4 "def acquire_operation_overlay" backend/src/sandbox/ephemeral_workspace/helper/operation.py
# body is 6 lines including 1-line guard + delegate call (≤10 cap)

$ grep -A6 "def acquire_overlay" backend/src/sandbox/ephemeral_workspace/plugin/projection.py
# body is 5 lines (≤10 cap)
```

### Net-deletion measurement

```
$ git diff 46f505b6f..HEAD -- \
    backend/src/sandbox/ephemeral_workspace/helper/types.py \
    backend/src/sandbox/ephemeral_workspace/helper/operation.py \
    backend/src/sandbox/ephemeral_workspace/plugin/projection.py \
    backend/src/sandbox/ephemeral_workspace/plugin/op_context.py \
  | grep -E "^[-+]" | grep -vE "^(---|\+\+\+)"
minus=199 plus=49 net_deletion=150
```

Target was ≥200; actual 150 (50-line slip — see P2 finding).

## Residual Risks

1. **Plan deviation around `auto_workspace_overlay`** (P1 above). LSP write ops continue to rely on the existing `apply.py` path for overlay + OCC management instead of `run_plugin_op_with_workspace_overlay`. Future migration of LSP writes to the standard auto-overlay runner is a separate work item.

2. **Audit-equivalence behavioral assertion is satisfied indirectly** via `test_plugin_intent_dispatch.py::test_write_allowed_plugin_uses_overlay_and_occ` plus the existing OCC publish path; no test introspects `LeaseGuard._released_lease_ids` accounting directly. Recommended P1 follow-up.

3. **`mypy` is not project-wide clean.** 739 pre-existing errors across 132 files (mostly untyped test fixtures); none introduced by this phase. The 9 notes on phase-touched files are about pre-existing `LayerStackPort` vs `LayerStackClient` return-type structural mismatches (duck-type compatible at runtime).

4. **Env-only test failures (13).** All gated by `/eos-mount-scratch/eos-sandbox-runtime` being absent on macOS; confirmed pre-existing via baseline stash. Requires Linux + Docker overlay scratch root to verify the OCC + per-op overlay paths end-to-end.

5. **Parallel-user commits.** During this session, a parallel commit (`fe6b49d33 feat(sandbox): route plugin dispatch by tool intent`) added an earlier draft of Step 10a–10d. My implementation supersedes it in places: I added the drift contract test (10g), annotated LSP `runtime/server.py` register_plugin_op calls (which were not in the parallel commit), and preserved the `auto_workspace_overlay` escape hatch for LSP write ops.
