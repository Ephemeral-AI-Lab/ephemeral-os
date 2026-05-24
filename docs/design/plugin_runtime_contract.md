# Plugin Runtime Contract — Design

Status: DRAFT v3 (post-Critic round 1). Companion to `docs/plans/unify_sandbox_workspace_phase2_7.md` (approved implementation steps 1–9).

This document captures four design questions and the answers:
1. Disk-usage invariant for plugin overlays under N sessions × M ops.
2. Long-cached session pattern.
3. Unified `PluginRuntime` Protocol for future plugin extension.
4. Reuse of existing infrastructure.

**Critic round-1 caught that the v2 draft over-built.** v2 proposed a `PluginSession` abstraction with new helper scripts (`setns_persistent_exec.py`), a new `namespace_holder.py` module, and a migration of `PyrightSession` — ~210–240 new lines for ONE current consumer. CLAUDE.md §2 ("Simplicity First — no abstractions for single-use code") rejects this. v3 demotes that abstraction to a deferred option and lands on a minimal alternative.

## 1. Disk-usage invariant — VERIFIED

### Formal statement

Let:
- N = concurrent long-cached plugin sessions
- M = concurrent per-op plugin overlays
- L_active = layers in the active manifest
- L_pinned_stale = layers pinned by leases on superseded manifests not yet released

The on-disk lowerdir cost is:

    |L_active ∪ L_pinned_stale|   layer directories under storage_root/layers/

**Steady-state, independent of N and M:**

    disk_lowerdir(steady-state) = |L_active|

### Why it holds

- `LayerStack` storage is content-addressed: `layer_path = storage_root / "layers" / layer_id` (`stack.py:319`). One directory per `LayerRef`, shared across all leases.
- `LeaseRegistry._refcounts: Counter[LayerRef]` refcounts pinning across leases (`lease.py:36, 53, 61`).
- `LayerStack._unreferenced_layers` GCs only when `layer ∉ current_manifest.layers ∪ pinned_layers` (`stack.py:322–329`).
- `prepare_workspace_snapshot` returns paths into shared storage (`stack.py:114–116`); overlay mounts use them as `lowerdir+` lines (`kernel_mount.py:60`).
- Per-session upperdir/workdir under `OVERLAY_WRITABLE_ROOT` (`writable_dirs.py:13`) is small, write-only state. For read-mostly sessions (LSP-style), upperdir stays near-empty.

### Conditions (all currently enforced in code)

| # | Invariant | Enforced at |
|---|---|---|
| I1 | Long-cached session releases OLD lease after refresh-swap completes. | `pyright_session.py:115` — `_release_handle(old_handle)` after `_install_overlay_handle(new)`. |
| I2 | Refresh error paths release the new lease (no orphan). | `session_manager.py:233` — `_release_handle(session_view.handle)` on `PyrightOverlayRefreshError`/`OSError`/`TimeoutError`. |
| I3 | Per-op overlays release on op return/raise. | `helper/operation.py:82–85` — try/except releases on post-snapshot exception. After PLAN.md Step 7 this becomes a single primitive `overlay.lifecycle.acquire(release_hook=...)` with hard acceptance: "on any exception after `prepare_workspace_snapshot`, release lease AND `rmtree(run_dir)`". |

### Transient bound during concurrent foreign publishes

    disk_lowerdir(transient)  ≤  |L_active|  +  N × (concurrent foreign publishes during refresh window)

Bounded by the foreign-publish poll interval + refresh latency. The LSP path releases within one refresh cycle (~hundreds of ms). For N=8 sessions and a 5s poll interval, the transient bound is small in practice.

### What "future plugin extension" requires for O(1)

Any future plugin that holds a long-cached overlay session MUST:
- Release its OLD lease promptly after each manifest-change refresh.
- Release the lease on error paths.
- Not hold per-op leases past op completion.

These are *contracts on the plugin*, not infrastructure provided by EphemeralOS. They are enforceable by integration test (see §7).

## 2. Long-cached session pattern

### What "long-cached session" means here

A plugin holds a private mount-namespace where `/testbed` is overlay-mounted against the **latest** manifest's `layer_paths`. The session survives across many tool calls. On manifest change, the session remounts to the new layer set. **PyrightSession is the only current consumer.**

### Two viable designs

#### Design A — Minimal alternative (RECOMMENDED, lands today)

- Keep `PyrightSession`'s existing model: spawn workload under its own `unshare -Urm` (`pyright_session.py:_build_overlay_argv`).
- Keep `plugins/catalog/lsp/runtime/namespace_remount.py` (`nsenter -t <pyright_pid>` cross-namespace remount). This is the load-bearing boundary identified in PLAN.md Step 1's load-bearing-header annotation.
- Add typed `subscribe_workspace_changes` to `EphemeralPipelineLike` (PLAN.md Step 2). This is the only new abstraction needed for "always-latest snapshot."
- Plugin runtime code (e.g., `session_manager.py`) consumes the typed subscribe API; the existing pump-task drains the queue and calls `session.refresh_manifest`.

**Net new code for this design**: 0 lines beyond PLAN.md Steps 1–9. The 9-step plan already covers it.

#### Design B — `PluginSession` abstraction (DEFERRED)

A unified primitive built on `isolated_workspace`'s `_LinuxRuntime.spawn_ns_holder` pattern: dedicated ns_holder subprocess holds `{user, mount}` namespaces open; workload spawns via `setns_persistent_exec` (new helper); refresh swaps lease and remounts via `setns_overlay_mount` (extended with umount-first).

**Net new code for this design**: ~210–240 lines (`plugin_session.py`, `setns_persistent_exec.py`, umount helper or extension, `namespace_holder.py` extracted from `_LinuxRuntime` with `namespaces=` parameter).

### Steelman of Design A (the minimal alternative)

The strongest case for stopping at Design A:

> *PyrightSession already works. It has been in production. Invariants I1, I2, I3 are verified in code today. The "always-latest snapshot" goal is fully satisfied by adding `subscribe_workspace_changes` to `EphemeralPipelineLike` (PLAN.md Step 2) — that's ~10 lines of Protocol delegation. The "unified plugin interface" goal is satisfied by the `PluginRuntime` Protocol collapse in PLAN.md Step 9 — also no new infrastructure. The "reuse existing code" goal is maximally satisfied because we touch nothing. The only thing Design A doesn't deliver is a hypothetical `PluginSession` that no current consumer needs. CLAUDE.md §2 says don't build that.*

### Why Design A wins today

| Criterion | Design A | Design B |
|---|---|---|
| Current consumers | 1 (PyrightSession, working) | 1 (PyrightSession, would migrate) |
| Lines added | 0 (PLAN.md Step 2 already adds the subscribe API) | ~210–240 |
| Lines deleted | 0 | ~155 (`helper/types.py` + `namespace_remount.py`) |
| Migration risk | None — keeps a working code path | LSP daemon stdin/stdout streaming must survive the new `setns + execvpe` helper; process-reaping invariants must survive a shared `kill_holder` |
| CLAUDE.md §2 compliance | Yes (no abstraction built) | No — single-consumer abstraction unless Step 11 (IsolatedPipeline migration) lands |
| Drift risk | `namespace_remount.py` stays as a one-consumer helper | `setns_overlay_mount` becomes shared; one code path for both isolated and plugin uses |
| Reaper-ownership risk | None | `_LinuxRuntime.kill_holder` (`runtime.py:226–229`) does process-global `os.waitpid(-1, ...)`; two instances in one daemon race for SIGCHLD |

CLAUDE.md §2 is the deciding constraint: **don't build an abstraction with one consumer**.

### Trigger for revisiting Design B

Promote Design A → B when **any** of the following is true:
- A second plugin needs the long-cached session pattern (i.e., spawning a persistent workload + manifest-change remount), in any concrete proposal — not "future authors might."
- `namespace_remount.py` accumulates a second use case or grows past ~150 lines.
- The `--user --net --pid --mount` namespace surface in `_LinuxRuntime.spawn_ns_holder` needs to be parametrized for any other reason.

Until one of those triggers, keep PyrightSession as-is. The `namespace_remount.py` header annotation (PLAN.md Step 1) already records its load-bearing role.

## 3. Unified `PluginRuntime` Protocol

The shape future plugin authors consume. This is the **deliverable for "unified interface for plugin extension"** and lands inside PLAN.md Step 9 (S5 — slimming `PluginOpContext`).

```python
class PluginRuntime(Protocol):
    """The single typed surface every plugin op handler consumes.

    Implemented by EphemeralPipeline. Test stubs implement the same Protocol
    with in-memory fakes. Replaces today's three-Protocol triangle
    (ProjectionHandleLike + WorkspaceProjectionLike + EphemeralPipelineLike).
    """

    @property
    def workspace_root(self) -> str: ...

    @property
    def layer_stack_root(self) -> str: ...

    # ---- Freshness ----
    def current_manifest_key(self) -> str: ...

    def subscribe_workspace_changes(
        self, subscriber_id: str
    ) -> AsyncQueue[WorkspaceChangeEvent]: ...

    def unsubscribe_workspace_changes(self, subscriber_id: str) -> None: ...

    # ---- Per-op overlay ----
    async def acquire_operation_overlay(
        self,
        *,
        invocation_id: str,
        workspace_root: str | None = None,
    ) -> OverlayHandle: ...
    # Release via OverlayHandle._release closure.

    # ---- Publish ----
    async def publish_cycle(
        self,
        *,
        request: CommandExecRequest,
        upperdir: str | Path,
        snapshot: SnapshotManifest,
        run_maintenance: bool = True,
    ) -> WorkspaceCapturePublishResult: ...

    async def publish_workspace_paths(
        self,
        *,
        paths: list[str] | tuple[str, ...],
        agent_id: str = "",
        description: str = "plugin workspace edit",
    ) -> ChangesetResult: ...
```

### Survey: does `EphemeralPipeline` already implement this?

| Method | Implemented today? | Source |
|---|---|---|
| `workspace_root` | YES | `pipeline.py:84` property |
| `layer_stack_root` | NO — exposed implicitly as `_workspace_ref` | needs property alias (PLAN.md Step 9) |
| `current_manifest_key` | YES (named `active_manifest_key`) | `pipeline.py:152` — rename or alias (PLAN.md Step 9) |
| `subscribe_workspace_changes` | NO — `event_bus` exposed via `getattr` | PLAN.md Step 2 adds it |
| `unsubscribe_workspace_changes` | NO | PLAN.md Step 2 adds it |
| `acquire_operation_overlay` | YES | `helper/operation.py:43` |
| `publish_cycle` | YES | `helper/publishing.py:73` |
| `publish_workspace_paths` | YES | `helper/publishing.py:205` |

Net new methods on `EphemeralPipeline`: 2 (`subscribe_workspace_changes`, `unsubscribe_workspace_changes`) + 1 rename/alias (`current_manifest_key`) + 1 property alias (`layer_stack_root`). All four land within PLAN.md Steps 2 + 9.

### What collapses into this single Protocol

- `WorkspaceProjectionLike` deleted (PLAN.md Step 9).
- `ProjectionHandleLike` deleted (PLAN.md Step 9 — `OverlayHandle` is the single handle type from Step 6).
- `EphemeralPipelineLike` renamed to `PluginRuntime` (PLAN.md Step 9).
- `OperationOverlayHandle` and `OverlayProjectionHandle` deleted (PLAN.md Step 6).
- `session_manager._acquire_session_view` 3-branch dispatch collapses to a single `_dispatch_lsp_overlay_acquire` helper + None fallback (PLAN.md Step 4).
- `PluginOpContext` slims to `(layer_stack_root, caller, runtime: PluginRuntime, metadata)`.

### PluginService vs PluginTool — distinct concepts

Adding the framework-level intent label and uniform OCC contract requires distinguishing two concepts that plugin authors must reason about separately:

| Concept | What it is | Lifetime | Today's only example |
|---|---|---|---|
| **PluginService** | Long-lived, daemon-side, per-`(plugin, layer_stack_root)` resource. Holds a long-cached overlay-mounted namespace for file-watch / stateful queries. Refreshes on `WorkspaceChangeEvent`. | Per-`(plugin, layer_stack_root)`; survives across many tool calls. | `PyrightSession` (`plugins/catalog/lsp/runtime/pyright_session.py`). |
| **PluginTool** | Per-call `@tool` entry point, intent-labeled. READ_ONLY tools query their plugin service; WRITE_ALLOWED tools execute structurally identically to normal `api.shell` write tools. | Per-call (transient). | All 12 LSP tools (6 read + 6 write). |

Mapping:

- **PluginService = "long-lived overlay session for plugin services for file watch"** (the user's R1 requirement). Today's PyrightSession satisfies this for LSP via its own `unshare -Urm` namespace + `nsenter` remount on manifest change. Future plugin services follow this pattern. A general `PluginSession` abstraction stays deferred per v3 §2 Design B trigger (no second consumer yet).
- **PluginTool = uniform with normal tools** (the user's R2 requirement). After PLAN.md Step 10:
  - Every `@tool` (plugin or not) declares `intent=Intent.READ_ONLY` or `Intent.WRITE_ALLOWED`.
  - READ_ONLY plugin tools run in-daemon and query their plugin service. No overlay allocation. No OCC. Same effective shape as a normal read tool (no state mutation).
  - WRITE_ALLOWED plugin tools take the existing `acquire_operation_overlay + publish_cycle` path. Same OCC primitive (`_occ_client.apply_changeset(CommitOptions(atomic=...))`), same stale-snapshot detection, same atomic-commit semantics as `api.shell` writes. Structurally equivalent, not byte-identical (different entry point, same OCC machinery from `_apply_workspace_capture` onwards).

### What is NOT on this Protocol (and why)

- `acquire_plugin_session` — deferred until a second long-cached consumer exists (§2 trigger).
- `acquire_long_lived_overlay` — same.
- Direct `event_bus` exposure — replaced by typed subscribe API.
- `projection.acquire_overlay` / `projection.acquire` — collapsed into `acquire_operation_overlay`.

## 4. Always-latest-snapshot — verification

1. **Daemon-side foreign-publish watcher** (`pipeline.py:337` — `_watch_foreign_publishes`) polls every `foreign_watch_interval_s`. On manifest change, emits `WorkspaceChangeEvent` on `event_bus`.
2. **First-party publishes** emit synchronously on commit (`helper/publishing.py:190`) — event-emit latency is sub-millisecond.
3. **Plugin session** subscribes via `subscribe_workspace_changes(session_id)`. Consumer task drains the queue and calls `session.refresh_manifest`.
4. **Refresh** acquires new snapshot, `nsenter`-remounts (PyrightSession's existing path), releases old lease.

| Metric | Bound | Note |
|---|---|---|
| Event-emit latency (first-party publish) | sub-ms | synchronous on commit |
| Event-emit latency (foreign publish) | ≤ poll interval | observation, not SLO |
| Refresh-completion latency (mount swap) | ~hundreds of ms | helper subprocess + setns + mount syscalls |

Per-op overlays don't refresh — they lease at acquire time and let OCC handle stale-snapshot conflicts at publish time.

## 5. What this design reuses — and what (little) is new

Everything in this design is covered by **PLAN.md Steps 1–9 alone**, which were previously approved across two consensus rounds. The new contribution of THIS document is:

1. Naming/typing: rename `EphemeralPipelineLike` → `PluginRuntime`, add `layer_stack_root` property alias, add `current_manifest_key` alias.
2. Documenting the O(1) invariant and its conditions.
3. Recording the rejection rationale for Design B (PluginSession abstraction).
4. Defining the trigger for revisiting Design B.

| Reused from | What we reuse | Status |
|---|---|---|
| `sandbox/overlay/handle.py` | `OverlayHandle` (after PLAN.md Step 6 extension) | Unchanged beyond Step 6 |
| `sandbox/overlay/kernel_mount.py` | `mount_overlay`, extended `umount`, `validate_mount_inputs` | Unchanged beyond PLAN.md Step 1 |
| `sandbox/overlay/lifecycle.py` | `create`, `destroy`, new `acquire(release_hook=...)` | Unchanged beyond PLAN.md Step 7 |
| `sandbox/overlay/writable_dirs.py` | `allocate_overlay_writable_dirs` | Unchanged |
| `sandbox/layer_stack/stack.py` | `prepare_workspace_snapshot`, `release_lease`, `read_active_manifest` | Unchanged |
| `sandbox/layer_stack/lease.py` | `LeaseRegistry` refcounting (the engine of the O(1) invariant) | Unchanged |
| `sandbox/ephemeral_workspace/pipeline.py` | `EphemeralPipeline` (becomes `PluginRuntime` implementer) | Unchanged beyond PLAN.md Steps 2 + 9 |
| `sandbox/ephemeral_workspace/events.py` | `WorkspaceChangeEvent`, `EphemeralPipelineEventBus` | Unchanged; consumed via typed Protocol after Step 2 |
| `plugins/catalog/lsp/runtime/pyright_session.py` | PyrightSession's existing namespace + remount model | Unchanged |
| `plugins/catalog/lsp/runtime/namespace_remount.py` | The `nsenter -t <child_pid>` cross-namespace remount (102 lines) | Unchanged — load-bearing header annotation per PLAN.md Step 1 |

**Net new code attributable to this design (beyond PLAN.md Steps 1–9)**: 0 lines.

## 6. Resolved questions

1. **`_LinuxRuntime` split into a shared `namespace_holder.py`?** Deferred until a second consumer (per §2 trigger).
2. **`PluginSession` namespace surface?** Moot (deferred).
3. **`PluginSession` eviction policy?** Moot (deferred).
4. **Does `EphemeralPipeline` already implement enough of `PluginRuntime`?** Yes; survey in §3. Two new methods + two aliases, all within PLAN.md Steps 2 + 9.

## 7. Verification

- **O(1) invariant**: integration test asserting `LeaseRegistry._refcounts[layer]` drops to zero after `session.refresh_manifest()` completes; `_unreferenced_layers()` returns the expected superseded layers; `os.path.isdir(storage_root/layers/<old_layer_id>)` is False post-GC. Test parametrized at N=8 sessions × K=4 publishes. GC trigger = `LayerStack.release_lease(lease_id)` invocation inside the session's refresh path.
- **PluginRuntime Protocol surface**: `mypy --strict` over `EphemeralPipeline` confirms it satisfies the Protocol. Test stubs in `backend/tests/unit_test/test_sandbox/` migrate from `SimpleNamespace` to typed fakes (PLAN.md Step 9 ordering rule).
- **Always-latest**: existing LSP integration test (hover → publish → refresh → hover-sees-new-state) continues to pass.

## 8. Sequencing

This design adds **no new steps** to `docs/plans/unify_sandbox_workspace_phase2_7.md`. The 9 approved steps deliver everything in §1–§5.

The deferred Design B (PluginSession + `namespace_holder.py` + `setns_persistent_exec.py` + umount helper) is captured as a **future option** with a trigger condition; not in scope for any current work.

## 9. Updated ADR (delta from PLAN.md ADR)

**Decision (added)**: Adopt Design A (minimal alternative): the typed `subscribe_workspace_changes` Protocol method from PLAN.md Step 2 + the `PluginRuntime` Protocol naming from PLAN.md Step 9 deliver everything required for "unified plugin interface, always-latest snapshot, O(1) disk usage." Reject Design B (`PluginSession` abstraction) on CLAUDE.md §2 grounds.

**Drivers (added)**:
- O(1) lowerdir invariant is a property of `LeaseRegistry` refcounting; no new infrastructure is required to deliver it.
- Future plugin authors consuming the `PluginRuntime` Protocol see one typed surface (post-Step-9 collapse).
- LSP's existing long-cached pattern works and has been verified across happy/error/evict paths.

**Alternatives considered (added)**:
1. **Design B — `PluginSession` abstraction** — deferred. Verdict: single-consumer; CLAUDE.md §2 rejects abstractions built for one consumer absent a named second consumer. Re-evaluate when the trigger fires.
2. **Promote Step 11 (IsolatedPipeline migration) to make Design B two-consumer** — rejected. `IsolatedPipeline`'s `_LinuxRuntime` is a 320-line module deeply entangled with network/cgroup/veth code; the split benefits no current consumer and adds reaper-ownership complexity (process-global `os.waitpid(-1, ...)`).

**Consequences (added)**: PLAN.md's 9 steps fully cover the design ask. No additional steps. Design B captured as a future-option entry. PyrightSession and `namespace_remount.py` remain in place.

**Follow-ups (added)** — only if §2 trigger fires:
- `_LinuxRuntime.spawn_ns_holder` / `open_ns_fds` need `namespaces=` parameter (today hardcodes `--user --net --pid --mount`).
- LSP daemon needs `setns_persistent_exec.py` (setns + execvpe, no fork/wait) — `_LinuxRuntime.run_in_handle`'s fork+waitpid+`capture_output=True` model is incompatible with `LspJsonRpcClient`'s persistent pipes.
- `setns_overlay_mount.py` needs umount-first (today only mounts); pick "extend existing" vs "new sibling `setns_umount.py`." If pursued, the sibling-file path is preferred — keeps the `mount` script single-responsibility per the existing `R10 single-thread discipline` docstring.
- Reaper ownership: single instance contract OR scope `kill_holder` to known PIDs in `self._holders`/`self._grandchildren` only.
