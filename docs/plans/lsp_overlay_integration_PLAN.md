# LSP Overlay Integration + Plugin Module Simplification — PLAN

Status: ralplan consensus APPROVED v2 (Planner → Architect → Critic, 2 rounds).

## Findings (Q1 + Q2)

### Q1 — "Dual-mode coexistence" claim is misleading

There is **no execution-mode duality** in `EphemeralPipeline`. All tool execution runs in per-call private-namespace overlays:

| Path | Per-call overlay acquired via | Call site |
|---|---|---|
| `api.shell` | `overlay.lifecycle.create()` → `run_in_namespace()` | `EphemeralPipeline.run_tool_call` ← `daemon/dispatch.py:44` |
| Plugin dispatch | `acquire_operation_overlay()` → child under `unshare -Urm` | `ephemeral_workspace/plugin/overlay_dispatch.py` |
| LSP `apply_workspace_edit` | `acquire_operation_overlay()` → child under `unshare -Urm` | `plugins/catalog/lsp/runtime/apply.py` |

The daemon's `/testbed` mount is a state mirror (freshness oracle + foreign-publish watcher + daemon-internal reads), not an execution surface.

### Q2 — LSP is the one genuine long-lived overlay consumer

`PyrightSession` runs `pyright-langserver` under its own `unshare -Urm`, holding `/testbed` overlay-mounted against a leased snapshot. Refreshes via `nsenter -t <child_pid>` invoking `plugins/catalog/lsp/runtime/namespace_remount.py` — **load-bearing cross-namespace entrypoint, must stay**.

### Q3 — No unified overlay/projection interface for plugins (today)

Three structurally near-identical handle types exist:
- `OverlayHandle` (`overlay/handle.py`) — per-call api.shell.
- `OperationOverlayHandle` (`helper/types.py`) — api.shell + plugin + LSP via `acquire_operation_overlay`. Carries `_overlay: EphemeralPipeline` back-ref.
- `OverlayProjectionHandle` (`plugin/projection.py`) — composes `ProjectionHandle`.

Three release semantics:
- Daemon-routed via `EphemeralPipeline._release_lease` (audit + `LeaseGuard`).
- Projection-direct via `LayerStack.release_lease` (no audit).
- Per-call via `OverlayHandle._release` closure.

Future plugin authors face a triple-fallback dispatch (`session_manager._acquire_session_view`) because no shared contract exists.

## Approved Steps (9 total)

### LSP integration (Steps 1–5)

#### Step 1 — Consolidate umount semantics

`backend/src/sandbox/overlay/kernel_mount.py`
- Extend signature: `umount(path, *, lazy: bool=False, raise_on_failure: bool=False)`.
- Default `(False, False)` preserves existing silent-return.

`backend/src/plugins/catalog/lsp/runtime/namespace_remount.py`
- Header docstring: `# Load-bearing: nsenter -t <child_pid> entrypoint for LSP private-namespace overlay remount. DO NOT DELETE — cross-namespace boundary.`
- `_detach_mount` body → `kernel_mount.umount(workspace_root, lazy=True, raise_on_failure=True)`.

**Verify**: unit tests for all four `(lazy, raise_on_failure)` combinations; LSP `session.evict()` integration test for raise-on-stuck-mount.

#### Step 2 — Typed workspace-change subscription

`backend/src/sandbox/ephemeral_workspace/plugin/op_context.py`
- Re-export `WorkspaceChangeEvent` so plugins do not import `sandbox.ephemeral_workspace.events`.

`EphemeralPipelineLike` Protocol additions:
```python
def subscribe_workspace_changes(
    self, subscriber_id: str
) -> AsyncQueue[WorkspaceChangeEvent]: ...
def unsubscribe_workspace_changes(self, subscriber_id: str) -> None: ...
```

`backend/src/sandbox/ephemeral_workspace/pipeline.py`
- `EphemeralPipeline` implements both by delegating to `self.event_bus.subscribe(...)` / `.unsubscribe(...)`. Queue semantics, pump ownership, cancellation — unchanged.

**Verify**: subscribe → emit → queue receives; unsubscribe stops; existing pump-task cancel on `evict_for_root` still works.

#### Step 3 — Migrate session_manager subscription

`backend/src/plugins/catalog/lsp/runtime/session_manager.py`
- Replace `getattr(overlay, "event_bus", None)` and `event_bus.subscribe`/`.unsubscribe` calls with `getattr(overlay, "subscribe_workspace_changes", None)` / `.unsubscribe_workspace_changes`.
- Pump-task ownership and `_event_subscriptions` / `_event_tasks` bookkeeping unchanged.

**Verify**: `grep -r 'getattr.*event_bus' backend/src/plugins/**/*.py` → 0; `grep -r 'from sandbox.ephemeral_workspace.events' backend/src/plugins/**/*.py` → 0; LSP plugin tests green.

#### Step 4 — Collapse `_acquire_session_view` dispatch

`backend/src/plugins/catalog/lsp/runtime/session_manager.py`

Three pipeline shapes enumerated:

| Shape | Dispatch |
|---|---|
| `EphemeralPipeline` ctx | `ctx.overlay.acquire_operation_overlay(invocation_id, workspace_root=...)` |
| `IsolatedPipeline` projection ctx | `ctx.projection.acquire_overlay(invocation_id, workspace_root=...)` |
| Degraded ctx (test stubs / legacy projections) | `ctx.projection.acquire("lsp-session")` |

New helper `_dispatch_lsp_overlay_acquire(ctx, *, invocation_id, workspace_root) -> _SessionView | None`. `_acquire_session_view` body ≤ 4 lines:
```python
def _acquire_session_view(ctx, *, active_key):
    view = _dispatch_lsp_overlay_acquire(ctx, invocation_id=..., workspace_root=...)
    return view or _SessionView(manifest_key=active_key, workspace_root=..., handle=None)
```

**Verify**: integration tests for all three shapes.

#### Step 5 — Observability for degraded path

`backend/src/plugins/catalog/lsp/runtime/session_manager.py`
- Rate-limited `logger.warning` when `_dispatch_lsp_overlay_acquire` returns `None`. Mechanism (token bucket vs first-N-per-interval) picked at execution time.

### Plugin module simplification (Steps 6–9)

#### Step 6 (S1) — Unify three handle types into one

`backend/src/sandbox/overlay/handle.py`
- Extend `OverlayHandle` with: `manifest_key: str = ""`, `manifest_version: int = 0`, `root_hash: str = ""`, `run_dir: Path` (explicit field, not `upperdir.parent` convention).
- `_release: Callable[[], None] | None` is the sole release slot. Closure captures both lease-release-fn and `rmtree(run_dir)`.

Deletions:
- `backend/src/sandbox/ephemeral_workspace/helper/types.py` → delete `OperationOverlayHandle`.
- `backend/src/sandbox/ephemeral_workspace/plugin/projection.py` → delete `OverlayProjectionHandle`. `ProjectionHandle` (non-overlay) stays for now (used by degraded fallback).

Migration notes:
- The `OperationOverlayHandle._overlay: EphemeralPipeline` back-pointer is replaced by the `_release` closure capturing `release_hook` from Step 7.
- `helper/operation.py:_attach_resource_timings` uses `handle.upperdir.parent` to derive run_dir — update to `handle.run_dir`.

`backend/src/sandbox/overlay/lifecycle.py`
- `destroy(handle)` → `rmtree(handle.run_dir)` (was `rmtree(handle.upperdir.parent)`).

**Verify**: type check; all overlay-related tests green; contract grep `class OperationOverlayHandle|class OverlayProjectionHandle` → 0 hits.

#### Step 7 (S2) — Single acquire primitive with release-strategy parameter

`backend/src/sandbox/overlay/lifecycle.py`
```python
def acquire(
    layer_stack,
    *,
    invocation_id: str,
    workspace_root: str,
    release_hook: Callable[[str], None] | None = None,
) -> OverlayHandle:
    """Lease snapshot + allocate writable dirs + assemble handle.

    Default release_hook=None binds to layer_stack.release_lease.
    Daemon callers pass self._release_lease for audit/LeaseGuard routing.
    On any post-snapshot exception, release lease AND rmtree(run_dir) before re-raising.
    """
```

Delegations:
- `EphemeralPipeline.acquire_operation_overlay` becomes a ≤10-line delegate passing `release_hook=self._release_lease` (preserves audit).
- `WorkspaceProjection.acquire_overlay` becomes a ≤10-line delegate passing `release_hook=None` (current direct-release semantics).

**Acceptance (promoted from pre-mortem to hard criterion)**:
- On any exception after `prepare_workspace_snapshot` succeeds, `acquire` releases the lease AND `rmtree(run_dir)` before re-raising. Unit test required.
- Daemon-path acquire+release emits same audit log entries as today's `_release_lease` path. Integration test required.

#### Step 8 (S4b) — Slim `WorkspaceProjection`

`backend/src/sandbox/ephemeral_workspace/plugin/projection.py`
- `acquire_overlay` becomes a ~5-line delegate to `overlay.lifecycle.acquire`.
- Remove `_prepare_snapshot_with_retry` and the `TypeError` legacy fallback (~60 lines deleted). They live in the body being replaced — removing them with the body avoids landing dead code.

**Pre-condition**: grep `backend/` confirms no external production caller depends on the legacy `TypeError`-fallback path. Test doubles that relied on it migrate to the typed signature.

**Verify**: `WorkspaceProjection` body ≤ 100 lines (down from 230); projection unit tests green.

#### Step 9 (S5) — Slim `PluginOpContext` Protocol triangle

`backend/src/sandbox/ephemeral_workspace/plugin/op_context.py`
- Tighten `ProjectionHandleLike` return-type annotations to `OverlayHandle` (after Steps 6–8 land).
- If `ProjectionHandleLike` becomes redundant with `OverlayHandle`'s public surface, delete it; otherwise retain with explicit rationale in commit message.
- Keep both `ctx.overlay` and `ctx.projection` fields (S4a — absorbing projection into pipeline — explicitly deferred).
- `WorkspaceProjectionLike.acquire_overlay` return type → `OverlayHandle`.
- `EphemeralPipelineLike.acquire_operation_overlay` return type → `OverlayHandle`.

**Ordering rule**: Step 9 runs LAST. Run `mypy backend/tests/unit_test/test_sandbox/` after each prior step; migrate `SimpleNamespace` stubs to typed fakes incrementally.

## REJECTED (deferred or wrong shape)

- **S3** — Collapse 3 unshare child entrypoints (`overlay_child.py`, `lsp/apply_child.py`, `namespace_remount.py`). Cross-process boundary; separate work item.
- **S4a** — Delete `WorkspaceProjection` outright, absorb into `EphemeralPipeline`. Touches `IsolatedPipeline` projection shape; defer.
- **`LongLivedOverlayHandle` as new type** — `OverlayHandle.namespace_pid is not None` + nullable `_release` already encode the distinction.
- **Routing `EphemeralPipeline._remount_active` through `overlay.lifecycle.refresh`** — namespace identity differs (daemon in-process vs LSP cross-process via `nsenter`).
- **Single un-parameterized acquire (always direct lease release)** — bypasses daemon audit and `LeaseGuard` invariants.

## Pre-mortem

1. **S1 release closure must absorb daemon-routed (audit + LeaseGuard) AND projection-direct release semantics.** Mitigation: `release_hook` parameter on `overlay.lifecycle.acquire` (Step 7).
2. **Error-cleanup asymmetry between daemon acquire (has try/except) and projection acquire (lacks it).** Mitigation: promoted to Step 7 acceptance criterion.
3. **S5 Protocol field tightening may surface `mypy` errors in test stubs using `SimpleNamespace`.** Mitigation: do S5 last; run `mypy` after each prior step; migrate stubs to typed fakes incrementally.

## Test plan

- **Unit**: `umount` 4-combo; `overlay.lifecycle.acquire` happy + error-cleanup; `subscribe_workspace_changes` lifecycle; unified `OverlayHandle.release` cleans lease + run_dir.
- **Integration**: LSP hover → remount → evict cycle; api.shell with unified handle; plugin `overlay_dispatch` end-to-end; **audit-bypass check** (daemon-path release emits expected `_release_lease`/`LeaseGuard` audit entries).
- **Contract**: `grep -r 'class OperationOverlayHandle\|class OverlayProjectionHandle' backend/src/` → 0; `grep -r 'getattr.*event_bus' backend/src/plugins/**/*.py` → 0; `grep -r 'from sandbox.ephemeral_workspace.events' backend/src/plugins/**/*.py` → 0.
- **Observability**: log assertion for degraded-dispatch WARNING (Step 5).

## Acceptance

- `OverlayHandle` is the sole overlay-handle dataclass.
- `overlay.lifecycle.acquire` is the sole "lease + writable_dirs + error-cleanup" sequence; `acquire_operation_overlay` and `WorkspaceProjection.acquire_overlay` are ≤ 10-line delegates.
- `release_hook` parameter exists on `overlay.lifecycle.acquire` and daemon path uses it.
- Audit integration test confirms daemon-path release emits `LeaseGuard`/audit entries.
- `WorkspaceProjection` body ≤ 100 lines (from ~230).
- `_acquire_session_view` body ≤ 4 lines.
- `run_dir` is an explicit field on `OverlayHandle` (no more `upperdir.parent` convention).
- Net deletion ≥ 200 lines across `helper/types.py`, `helper/operation.py`, `plugin/projection.py`, `plugin/op_context.py`.
- `EphemeralPipeline._remount_active` shape unchanged.
- `namespace_remount.py` present with load-bearing header.
- `grep` contract criteria above all hit 0.
- All existing tests pass; `mypy` clean; no new `skip`/`xfail`.

## ADR

**Decision**: Two-axis `umount(lazy, raise_on_failure)`; typed `subscribe_workspace_changes` / `unsubscribe_workspace_changes` on the pipeline Protocol delegating to the existing queue-based `event_bus`; collapse `_acquire_session_view` to a 4-line body via `_dispatch_lsp_overlay_acquire`; rate-limited WARNING on degraded dispatch. **Then**: unify three overlay handle types into a single `OverlayHandle` (with explicit `run_dir`); introduce `overlay.lifecycle.acquire(layer_stack, *, invocation_id, workspace_root, release_hook=None)` as the sole "lease + writable_dirs + error-cleanup" primitive; reduce `EphemeralPipeline.acquire_operation_overlay` and `WorkspaceProjection.acquire_overlay` to ≤ 10-line delegates; slim `WorkspaceProjection` body to ≤ 100 lines; tighten `PluginOpContext` Protocol field types last.

**Drivers**:
- Plugin layer must not import `sandbox.ephemeral_workspace.events`.
- LSP eviction must surface stuck mounts loudly while default callers stay silent.
- `IsolatedPipeline`'s divergent acquire signature must not be forced into a shared Protocol method.
- Degraded fallback must not be silent in prod.
- Three near-identical handle types create drift risk for `lease_id` / `manifest_key` semantics.
- Daemon-path release must continue to emit `LeaseGuard`/audit entries that the direct `layer_stack.release_lease` path does not produce.
- Projection-path acquire today silently leaks `run_dir` on post-lease exceptions.

**Alternatives considered**:
1. Callback-based subscribe API — rejected: `event_bus` is queue-based; would force a parallel pump and a new race window.
2. Generic `acquire_overlay` on every Pipeline Protocol — rejected: forces long-lived workspace coupling on `IsolatedPipeline`.
3. Single-axis `umount(force=True)` — rejected: conflates lazy semantics with error-propagation policy.
4. New `LongLivedOverlayHandle` type — rejected: duplicates `OverlayHandle` fields; `namespace_pid` already encodes the distinction.
5. Routing `EphemeralPipeline._remount_active` through `overlay.lifecycle.refresh` — rejected: namespace identity differs (daemon in-process vs LSP cross-process via `nsenter`).
6. Single un-parameterized `acquire` with always-direct lease release — rejected: bypasses daemon audit log; violates `LeaseGuard` invariants.
7. Delete `WorkspaceProjection` outright (S4a) — deferred: couples `IsolatedPipeline` migration into LSP-scoped refactor.
8. Collapse three `unshare` child entrypoints (S3) — deferred: cross-process boundary; separate work item.

**Why chosen**: Minimal Protocol surface; preserves existing pump ownership; opt-in error semantics; isolated, unit-testable dispatch; one overlay-handle dataclass; one acquire primitive with audit-aware opt-in; explicit `run_dir` removes `upperdir.parent` convention; projection error-path leaks closed; respects the cross-namespace boundary `namespace_remount.py` provides.

**Consequences**: Plugins gain a stable subscribe API; LSP eviction is loud-on-failure; degraded path observable; three projection branches still exist but isolated behind one helper; one overlay-handle dataclass for all future plugin authors; net deletion ≥ 200 lines.

**Follow-ups**:
- Specify rate-limit mechanism at execution time.
- Tighten acceptance greps to `backend/src/plugins/**/*.py`.
- S3 (collapse `unshare` child entrypoints) and S4a (delete `WorkspaceProjection` outright) remain candidate work items.
- Protocol field tightening in Step 9 may require updating test stubs from `SimpleNamespace` to typed fakes.

## Execution order

Recommended ordering: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9**, with each step atomic-commit. Steps 1–5 already approved as a self-contained landing block; Steps 6–9 form a second landing block where 9 strictly runs last (per pre-mortem #3).
