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

### Plugin tool / service alignment (Steps 10–10g, approved consensus round 3)

Steps 1–9 deliver the overlay primitives + Protocol collapse. Steps 10a–10g align plugin tools with normal tools at the framework level (intent labeling) and document the plugin-service vs plugin-tool distinction.

#### Step 10a — `intent: Intent` required on `@tool`

`backend/src/tools/_framework/core/decorator.py`
- Add `intent: Intent` as REQUIRED kwarg on `@tool` (kw-only, no default).
- Decorator raises `TypeError("@tool requires intent=Intent...")` at import time if missing.
- Expose `BaseTool.intent` property.

Verify: `python -c "import tools; import plugins"` succeeds iff every `@tool` site is annotated (mechanical fail-fast).

#### Step 10b — Annotate all existing `@tool` callsites

- Write tools → `intent=Intent.WRITE_ALLOWED`:
  - `tools/sandbox/write_file/write_file.py:26`
  - `tools/sandbox/edit_file/edit_file.py:65`
  - `plugins/catalog/lsp/tools/rename.py:22`
  - `plugins/catalog/lsp/tools/format.py:25`
  - `plugins/catalog/lsp/tools/apply_code_action.py:20`
  - `plugins/catalog/lsp/tools/apply_workspace_edit.py:20`
- Read tools → `intent=Intent.READ_ONLY`:
  - All 6 LSP read tools: `hover`, `find_definitions`, `find_references`, `diagnostics`, `query_symbols`, `code_actions`
  - All non-plugin read tools (`read_file`, `list_dir`, etc.)

Verify: grep contract — no `@tool(` without `intent=` in the same expression.

#### Step 10c — Auto-injection through `BaseTool.execute → context → call_plugin`

`backend/src/tools/_framework/core/base.py`
- `BaseTool.execute` writes `context["__intent"] = self.intent` before invoking the wrapped function.

`backend/src/sandbox/ephemeral_workspace/plugin/session.py`
- `call_plugin` reads `context["__intent"]` and embeds in `payload_with_meta["intent"]`.

`backend/src/sandbox/ephemeral_workspace/plugin/handler.py`
- `_plugin_op_context_factory` reads `args["intent"]` and writes `PluginOpContext.intent: Intent`.

Tool authors NEVER manually pass intent.

Verify: trace test — `@tool(intent=READ_ONLY)` → handler receives `ctx.intent == Intent.READ_ONLY`.

#### Step 10d — Dispatch-runner selection at registration

`backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py`
- Registration tuple becomes `(plugin_name, op_name, handler, intent)`.

`backend/src/sandbox/ephemeral_workspace/plugin/handler.py:flush_plugin_registrations`
- Per-op dispatch_runner selection:
  - `intent == Intent.READ_ONLY` → in-process dispatch runner (handler invoked in daemon process; no `acquire_operation_overlay`, no namespace child, no `publish_cycle`).
  - `intent == Intent.WRITE_ALLOWED` → existing `run_plugin_op_with_workspace_overlay` (overlay + OCC publish path, UNCHANGED).
  - `intent == Intent.LIFECYCLE` → reject at registration time (TypeError); LIFECYCLE is for sandbox lifecycle ops, not plugin tools.

`backend/src/sandbox/ephemeral_workspace/plugin/op_context.py`
- `PluginOpContext` docstring contract: "Handlers invoked with `intent=Intent.READ_ONLY` MUST NOT perform direct filesystem I/O. All reads MUST go through a `PluginService` (today: `PyrightSession` via `session_manager.get_session`)."

Verify:
- READ_ONLY plugin op: assert `LeaseRegistry.active_count()` unchanged across the op.
- READ_ONLY plugin op: assert `active_manifest_version` unchanged across the op.
- WRITE_ALLOWED plugin op: assert OCC `apply_changeset` audit entry present, structurally equivalent to `api.shell` write OCC audit.

#### Step 10e — Document `PluginService` concept

`docs/design/plugin_runtime_contract.md` §3
- Add subsection: `PluginService` vs `PluginTool` distinction.
  - `PluginService` = long-lived, daemon-side, per-`(plugin, layer_stack_root)` resource. Holds long-cached overlay-mounted namespace for file-watch / stateful queries. Today's only implementation = `PyrightSession`. Future plugin services follow this pattern until the v3 §2 Design B trigger fires.
  - `PluginTool` = per-call, intent-labeled `@tool` entry point. READ_ONLY tools query their plugin service. WRITE_ALLOWED tools execute structurally identically to normal `api.shell` write tools (same OCC `apply_changeset` primitive, same `CommitOptions(atomic=...)`, same stale-snapshot detection).

#### Step 10f — DEFERRED

`call_plugin_write` belt-and-suspenders API (mislabeling impossible at API boundary). Ship 10a–10e first and measure whether mislabeling occurs in practice. Adopt 10f only if empirically warranted.

#### Step 10g — Drift contract test

`backend/tests/contracts/test_tool_intent_drift.py` (new)
- For every `BaseTool` registered in `tool_registry.list_tools()` whose name has a sibling in the daemon's handlers-table (`daemon/handlers.py` verbs: `shell`, `read_file`, `write_file`, `edit_file`, etc.), assert `tool.intent == handlers_table[verb].intent`.
- Asserts every `@tool` decoration has an `intent` attribute set (positive complement to Step 10a's import-time TypeError).

Verify: test fails if `daemon/handlers.py` declares `Intent.WRITE_ALLOWED` for a verb while the `@tool` declares `Intent.READ_ONLY` (or vice versa).

#### Step 10 acceptance (full block)

- `@tool` requires explicit `intent=`; missing intent raises `TypeError` at import.
- All write tools annotated `Intent.WRITE_ALLOWED`; all read tools annotated `Intent.READ_ONLY`.
- `intent` auto-injected end-to-end with no tool-author manual passing.
- Plugin dispatch_runner chosen at registration time, not inside `overlay_dispatch`.
- READ_ONLY plugin op: no overlay allocation, no namespace child, no publish; LSP integration test confirms.
- WRITE_ALLOWED plugin op: existing overlay+OCC path UNCHANGED.
- 10g drift test green; daemon-handlers-table and `@tool.intent` agree everywhere.
- `PluginService` documented in v3 design doc as the long-lived overlay session for file-watch.

#### Step 10 sequencing

- 10a → 10b → 10c → 10d → 10g land atomically in one block (partial state breaks dispatch).
- 10e is doc-only; lands in parallel.
- 10f is deferred.
- Step 10 lands AFTER Steps 1–9 because (a) `PluginOpContext` slimming happens in Step 9, (b) the unified `OverlayHandle` from Step 6 is required for the in-process read dispatcher's return type alignment.

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
