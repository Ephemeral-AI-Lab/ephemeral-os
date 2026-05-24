# Plugin Runtime Contract — Design

Status: DRAFT (pre-consensus). Companion to `docs/plans/lsp_overlay_integration_PLAN.md` (approved implementation steps 1–9).

This document fixes:
1. The disk-usage invariant for plugin overlays (O(1) lowerdir under N sessions × M ops).
2. The long-cached session pattern abstracted from `PyrightSession` and `IsolatedPipeline`.
3. The unified `PluginRuntime` Protocol that future plugins consume.

It does NOT introduce new infrastructure where existing primitives suffice. The dominant move is **reuse, not invention**.

## 1. Disk-usage invariant

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

The N,M-dependence collapses because:

- `LayerStack` storage is content-addressed: `layer_path = storage_root / "layers" / layer_id`. One directory per `LayerRef`, shared across all leases (`stack.py:114, 319`).
- `LeaseRegistry._refcounts: Counter[LayerRef]` refcounts layer pinning across leases (`lease.py:36, 53, 61`).
- `LayerStack._unreferenced_layers` GCs only when `layer ∉ current_manifest.layers ∪ pinned_layers` (`stack.py:322–329`).
- Multiple leases that pin the same manifest → same layer set → same paths → no copy.
- `prepare_workspace_snapshot` returns those shared paths as strings (`stack.py:114–116`); the overlay mount uses them as `lowerdir+` lines (`kernel_mount.py:60`).

Per-session upperdir/workdir lives under `OVERLAY_WRITABLE_ROOT` (`writable_dirs.py:13`) — small, contains only writes; mostly empty for read-mostly sessions (LSP-style).

Per-op upperdir/workdir is released at op end via `OverlayHandle._release` closure.

### Conditions under which the bound holds

The bound is **conditional on three invariants**:

| # | Invariant | Currently enforced? |
|---|---|---|
| I1 | Every long-cached session releases its OLD lease promptly after a foreign-publish refresh swap completes. | YES — `PyrightSession.refresh_manifest` at `pyright_session.py:115` releases `old_handle` after install. |
| I2 | Refresh error paths still release the lease (no orphan). | YES — `_refresh_owned_session` at `session_manager.py:233` releases on `PyrightOverlayRefreshError`/`OSError`/`TimeoutError`. |
| I3 | Per-op overlays acquired by `acquire_operation_overlay` are released when the op returns or raises. | YES — `EphemeralPipeline.acquire_operation_overlay` catches post-snapshot exceptions and releases (`helper/operation.py:82–85`). |

Transient bound during concurrent foreign publishes:

    disk_lowerdir(transient)  ≤  |L_active| + N × (max concurrent old-version leases)

In practice this is bounded by the foreign-publish watcher poll interval × publish rate; the LSP path already releases within one publish cycle.

### Design choices that preserve O(1)

- A plugin session MUST NOT keep a lease "warm" across manifest changes; it must swap on `WorkspaceChangeEvent`.
- A plugin op MUST NOT hold its overlay handle past op completion.
- The plugin runtime MUST expose a typed subscription so plugins don't build their own polling; this is `subscribe_workspace_changes` from PLAN.md Step 2.

## 2. The long-cached session pattern

### What's needed

A plugin session that:
- Holds a private mount namespace where `/testbed` is overlay-mounted against the latest manifest's `layer_paths`.
- Lives across many tool calls (e.g., Pyright daemon).
- Sees the latest snapshot via remount-on-event, not poll.
- Releases its lease (and namespace) on evict.

### Two existing implementations to reuse from

| Implementation | Namespace ownership | Refresh mechanism | Lease lifetime |
|---|---|---|---|
| `PyrightSession` (`plugins/catalog/lsp/runtime/pyright_session.py`) | Process is itself spawned under `unshare -Urm`; namespace is the pyright child's own | `nsenter -t <pyright_pid>` + remount helper (`namespace_remount.py`) | Lease lives with the session; refresh swaps old→new |
| `IsolatedPipeline` ns_holder (`isolated_workspace/helper/runtime.py:_LinuxRuntime`) | A dedicated `ns_holder` subprocess holds `{user,mnt,pid,net}` open with `unshare --fork --kill-child`; other procs `setns()` in | `setns_overlay_mount` script: setns + umount + mount_overlay | Lease lives with the handle; teardown releases |

**Both patterns exist. `IsolatedPipeline`'s is more general** because the namespace is decoupled from the workload process — any process can `setns` in, the namespace holder is just a long-lived sleep. That decoupling matters for plugins where the workload is a server (LSP) AND for plugins where the workload is a script.

### Unified abstraction: `PluginSession`

Promote a single `sandbox.overlay.PluginSession` (or `sandbox.overlay.long_lived_overlay` — naming TBD) primitive built on the `ns_holder` pattern:

```python
@dataclass
class PluginSession:
    """Long-lived overlay-mounted namespace + a leased snapshot.

    Reuses _LinuxRuntime.spawn_ns_holder + setns_overlay_mount unchanged.
    The plugin's workload (server process, daemon, etc.) is spawned via
    setns_exec into this namespace.
    """
    session_id: str
    holder_pid: int                  # _LinuxRuntime.spawn_ns_holder result
    ns_fds: dict[str, int]           # _LinuxRuntime.open_ns_fds result
    overlay: OverlayHandle           # current manifest's lease + upper/work + layer_paths
    workspace_root: str

    async def refresh(self, layer_stack) -> None:
        """Foreign-publish swap. Acquires new snapshot, setns+umount+mount_overlay,
        releases OLD lease. Preserves I1+I2."""

    async def exec(self, argv, *, stdin=None, timeout_s=None) -> tuple[int, bytes, bytes]:
        """Run a command inside this session's namespace via setns_exec.
        Reuses _LinuxRuntime.run_in_handle."""

    async def release(self) -> None:
        """SIGTERM ns_holder, close FDs, release lease, rmtree upperdir."""
```

**Code reuse and three required additions** (Architect round-3 findings folded in):

| Field/method | Status | Detail |
|---|---|---|
| `spawn_ns_holder` | **MODIFIED** | Add `namespaces: tuple[str, ...] = ("user","mount")` parameter (today hardcodes `--user --net --pid --mount`). Default for `IsolatedPipeline` stays the full four-namespace surface; `PluginSession` opts into the smaller `user+mount` set. |
| `open_ns_fds` | **MODIFIED** | Same `namespaces` parameter so we don't open fds for namespaces we didn't ask for. |
| Initial mount | Unchanged | `isolated_workspace/scripts/setns_overlay_mount.py` already calls `overlay.kernel_mount.mount_overlay` after setns. |
| **Refresh remount** | **NEW CODE** (~30 lines) | `setns_overlay_mount.py` only mounts; refresh needs umount-first. Either extend `setns_overlay_mount.py` to umount-when-mountpoint, or add sibling `setns_umount.py`. The umount-first logic from today's `namespace_remount._detach_mount` moves into the setns-side helper. |
| **`exec` for persistent-stdio workloads (LSP)** | **NEW HELPER** (~50 lines) — `sandbox/overlay/scripts/setns_persistent_exec.py`. setns into target namespaces + `os.execvpe` the workload directly (no fork/waitpid). `PluginSession.spawn_persistent_proc` drives it via `asyncio.create_subprocess_exec` with `stdin/stdout=PIPE` so `LspJsonRpcClient` sees a normal persistent process. | `_LinuxRuntime.run_in_handle` uses `fork+waitpid+subprocess.run(capture_output=True)` and returns `(rc, bytes, bytes)` — **incompatible** with `LspJsonRpcClient`'s persistent `proc.stdin`/`proc.stdout` requirement. A new exec mode is unavoidable. |
| `exec` for one-shot scripts | Unchanged | `_LinuxRuntime.run_in_handle` stays for plugins that don't need persistent stdio. |
| Lease lifecycle | Unchanged | `LayerStack.prepare_workspace_snapshot` + `release_lease`. |
| Teardown | Unchanged | `_LinuxRuntime.kill_holder` + `release_lease` + `rmtree`. |

**Revised net new code** (corrected from earlier overclaim):

- `sandbox/overlay/plugin_session.py` — `PluginSession` dataclass + refresh + release + `spawn_persistent_proc` (~150 lines).
- `sandbox/overlay/scripts/setns_persistent_exec.py` — persistent-stdio setns + execvpe helper (~50 lines).
- Either extension of `setns_overlay_mount.py` (~30 lines) or new `setns_umount.py` (~25 lines) — pick one.
- `namespaces=` parameter diffs on `spawn_ns_holder`/`open_ns_fds` (~10 lines).

**Total new code**: ~210–240 lines. (Earlier draft claimed ~120; that was wrong — see Architect round-3 finding 5.)

**Migration of `PyrightSession`**:
```python
# Today: pyright_session.py:_build_overlay_argv constructs
#   [unshare, "-Urm", sys.executable, "-m", "namespace_entrypoint", payload]
# and asyncio.create_subprocess_exec runs that argv with stdin/stdout PIPE.

# After: PluginSession holds the namespace; pyright spawns into it.
session = await runtime.acquire_plugin_session(
    session_id="lsp-session",
    namespaces=("user", "mount"),
)
proc = await session.spawn_persistent_proc(
    ["pyright-langserver", "--stdio"],
    env=_runtime_subprocess_env(),
    cwd=_runtime_subprocess_cwd(),
)
# proc.stdin / proc.stdout are normal asyncio pipes; LspJsonRpcClient unchanged.

# Refresh (foreign-publish event):
await session.refresh(layer_stack)
# Internally: lease new snapshot, run setns_umount + setns_overlay_mount via
# pass_fds(user_fd, mnt_fd), release old lease.
```

`plugins/catalog/lsp/runtime/namespace_remount.py` deletes — its umount+mount sequence moves into the setns-side helper.

### Asymmetry note (incorporates Architect's prior round 2 finding)

Round 2 Architect rejected deleting `namespace_remount.py` on the grounds that it's the cross-namespace `nsenter` boundary that the LSP child uses. **This design supersedes that** because:

- The LSP child no longer needs its OWN namespace. The session's `ns_holder` owns the namespace; the LSP child runs INSIDE via `setns_exec`.
- Cross-namespace remount goes through `setns_overlay_mount` (which is already cross-namespace via `pass_fds=(user_fd, mnt_fd)` + setns), not via `nsenter -t <child_pid>`.

The Architect was right under the OLD design where each long-lived consumer owned its own namespace. Under the unified design, namespaces are first-class objects (the `ns_holder`) and `namespace_remount.py` becomes redundant with `setns_overlay_mount.py`. This is a **scope expansion** of the previously-approved plan, not a contradiction.

## 3. Unified `PluginRuntime` Protocol

The shape future plugin authors consume. Replaces today's `PluginOpContext` triangle (`projection: WorkspaceProjectionLike + overlay: EphemeralPipelineLike + ProjectionHandleLike` — three Protocols, partial overlap, triple-fallback dispatch in `session_manager._acquire_session_view`).

```python
class PluginRuntime(Protocol):
    """The single typed surface every plugin op handler consumes.

    Implemented by EphemeralPipeline (wrapping itself + injected layer_stack).
    Test stubs implement the same Protocol with in-memory fakes.
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

    # ---- Per-op overlay (the common case) ----
    async def acquire_operation_overlay(
        self,
        *,
        invocation_id: str,
        workspace_root: str | None = None,
    ) -> OverlayHandle: ...
    # Release via OverlayHandle._release closure on its destruction.

    # ---- Long-cached session (LSP-style) ----
    async def acquire_plugin_session(
        self,
        *,
        session_id: str,
        workspace_root: str | None = None,
    ) -> PluginSession: ...
    # Release via PluginSession.release().

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

### What collapses

- `WorkspaceProjectionLike` deleted — `PluginRuntime` is the single surface.
- `ProjectionHandleLike` deleted — `OverlayHandle` is the single handle type (PLAN.md Step 6).
- `OperationOverlayHandle` deleted (PLAN.md Step 6) — `OverlayHandle` is it.
- `OverlayProjectionHandle` deleted (PLAN.md Step 6) — same.
- `session_manager._acquire_session_view` 3-branch dispatch collapses to:
  ```python
  async def _acquire_session_view(ctx, *, active_key):
      session = await ctx.runtime.acquire_plugin_session(session_id="lsp-session")
      return _SessionView(manifest_key=session.overlay.snapshot_manifest_key, ...)
  ```
- `_dispatch_lsp_overlay_acquire` helper (planned in PLAN.md Step 4) deleted — direct call.
- `PluginOpContext` slims to `(layer_stack_root, caller, runtime: PluginRuntime, metadata)`.

### What `PluginRuntime` is implemented by

- **`EphemeralPipeline`** for daemon-mode plugins (today's path). All methods delegate to existing pipeline machinery.
- **In-memory fakes** in tests, satisfying the Protocol with `SimpleNamespace`-typed equivalents.
- Optionally **`IsolatedPipeline`** — but for plugins running under isolated workspaces, the runtime surface is `IsolatedPipeline` exposing the same Protocol. (Verify: today `IsolatedPipeline` does not implement `acquire_operation_overlay` / publish surface; if not, plugins-in-isolated stay scoped to read-only ops by raising `NotImplementedError`. Defer the full IsolatedPipeline Protocol parity.)

## 4. Verification of "always latest snapshot"

The combination is:

1. **Daemon-side foreign-publish watcher** (already exists, `EphemeralPipeline._watch_foreign_publishes` at `pipeline.py:337`) polls `layer_stack.read_active_manifest()` every `foreign_watch_interval_s`. On change, emits `WorkspaceChangeEvent` on `event_bus`.
2. **Plugin session** subscribes via `subscribe_workspace_changes(session_id)`. Consumer task drains the queue and calls `session.refresh(layer_stack)`.
3. **Refresh** acquires new snapshot, setns + umount + remount with new `layer_paths`, releases old lease.

Worst-case staleness = poll interval + remount latency. Mitigation: the daemon's own publish path emits the event synchronously on commit (see `helper/publishing.py:_publish_upperdir` line 190 — `event_bus.emit(WorkspaceChangeEvent(...))`), so first-party publishes are sub-millisecond visibility.

**Per-op overlays don't need refresh** — they lease the latest manifest at acquire time. The op runs against that snapshot, publishes via OCC (which detects stale-snapshot conflicts at apply time), and releases. If the manifest moved between acquire and publish, OCC reports a conflict — that's the OCC invariant, not a freshness gap.

## 5. What this design REUSES (claims by file)

| Reused from | What we reuse | What it costs |
|---|---|---|
| `sandbox/overlay/handle.py` | `OverlayHandle` dataclass extended with `manifest_key`, `manifest_version`, `root_hash`, `run_dir` | +4 fields, deletes 2 sibling types |
| `sandbox/overlay/kernel_mount.py` | `mount_overlay`, `umount` (extended), `validate_mount_inputs` | umount gets `(lazy, raise_on_failure)` two-axis (PLAN.md Step 1) |
| `sandbox/overlay/lifecycle.py` | `create`, `destroy` | Add `acquire(layer_stack, *, invocation_id, workspace_root, release_hook=None)` (PLAN.md Step 7) |
| `sandbox/overlay/writable_dirs.py` | `allocate_overlay_writable_dirs`, `overlay_writable_root` | Unchanged |
| `sandbox/layer_stack/stack.py` | `prepare_workspace_snapshot`, `release_lease`, `read_active_manifest` | Unchanged |
| `sandbox/layer_stack/lease.py` | `LeaseRegistry` (refcounts) | Unchanged |
| `sandbox/isolated_workspace/helper/runtime.py` | `_LinuxRuntime.spawn_ns_holder`, `open_ns_fds`, `run_in_handle`, `kill_holder` | Promote into `sandbox/overlay/namespace_holder.py` AND add `namespaces=` parameter (today hardcodes user/mnt/pid/net at runtime.py:69-73 and opens all four at 95-104). Network/cgroup helpers stay in isolated_workspace. |
| `sandbox/isolated_workspace/scripts/setns_overlay_mount.py` | The setns-+-overlay-mount helper | Extend with umount-when-mountpoint behavior (refresh requires umount-first), OR introduce sibling `setns_umount.py`. Move to `sandbox/overlay/scripts/`. |
| `sandbox/isolated_workspace/scripts/setns_exec.py` | One-shot cross-namespace exec helper (fork+waitpid+capture_output) | Move to `sandbox/overlay/scripts/`. Kept for one-shot scripts. NOT used for LSP — see new helper below. |
| **NEW**: `sandbox/overlay/scripts/setns_persistent_exec.py` | — | setns into target namespaces + `os.execvpe` (no fork/wait). LSP daemon spawn path; preserves `proc.stdin`/`proc.stdout` for `LspJsonRpcClient`. |
| `sandbox/ephemeral_workspace/pipeline.py` | `EphemeralPipeline` becomes the `PluginRuntime` implementation | Implements new Protocol methods; existing methods unchanged. |
| `sandbox/ephemeral_workspace/events.py` | `WorkspaceChangeEvent`, `EphemeralPipelineEventBus` | Subscribe API typed (PLAN.md Step 2). |

**Net new files** (revised):
- `sandbox/overlay/plugin_session.py` — `PluginSession` dataclass + refresh + release + `spawn_persistent_proc` (~150 lines).
- `sandbox/overlay/scripts/setns_persistent_exec.py` — persistent-stdio setns + execvpe helper (~50 lines).
- Either `setns_overlay_mount.py` extension (~30 lines) or new `sandbox/overlay/scripts/setns_umount.py` (~25 lines).
- `sandbox/overlay/namespace_holder.py` — extracted from `_LinuxRuntime` with parametrized `namespaces=` (~100 lines).

**Net deleted files**:
- `backend/src/sandbox/ephemeral_workspace/helper/types.py` (53 lines, deleted via PLAN.md Step 6).
- `backend/src/plugins/catalog/lsp/runtime/namespace_remount.py` (99 lines, umount-first logic moved into setns-side helper).
- Optionally `backend/src/sandbox/ephemeral_workspace/plugin/projection.py` (230 lines), if `WorkspaceProjection` absorbs into `EphemeralPipeline` per Step 9 (S4b).

**Estimated net delta**: ~230 new lines + ~382 deleted = ~152 net deletion. (Earlier draft claimed 380+ net deletion; corrected after Architect round-3 found the LSP migration needs a new persistent-exec helper.)

## 6. Resolved design decisions (Architect round-3 fixes)

1. **`_LinuxRuntime` split: RESOLVED.** Promote `spawn_ns_holder`, `open_ns_fds`, `run_in_handle`, `kill_holder` into `sandbox/overlay/namespace_holder.py`. Add `namespaces: tuple[str, ...]` parameter to both `spawn_ns_holder` and `open_ns_fds` (today the function body hardcodes `--user --net --pid --mount` at `runtime.py:69-73` and `open_ns_fds` opens all four at lines 95-104; both must accept the namespace set as input). Network and cgroup helpers stay in `isolated_workspace`. Estimated module size: ~100 lines.
2. **`PluginSession` namespace surface: RESOLVED.** `PluginSession` uses `namespaces=("user","mount")`. LSP does not need PID or network isolation; it needs the leased overlay view, which only requires user+mount. `IsolatedPipeline` keeps the full `("user","mount","pid","net")` set via its own default. The `namespaces=` parameter on `spawn_ns_holder` (resolved in §6.1) is what enables this.
3. **`PluginSession` eviction policy: RESOLVED.** Explicit release only, no LRU. Plugins call `session.release()` on (a) `api.plugin.ensure` with a different digest (today's `_evict_plugin_sessions` callsite already fires), (b) daemon shutdown, (c) test teardown. Per-`(plugin, layer_stack_root)` cache held in plugin runtime code (LSP's `_sessions: dict[str, PyrightSession]` model extends naturally).
4. **Does `EphemeralPipeline` already implement enough of `PluginRuntime` to make the Protocol cheap?** Survey:
   - `workspace_root` ✅ (property)
   - `current_manifest_key` ≈ (today: `active_manifest_key`; rename or alias)
   - `subscribe_workspace_changes` ❌ (Step 2)
   - `acquire_operation_overlay` ✅
   - `acquire_plugin_session` ❌ (new — Steps 6–9 of PLAN, plus this design)
   - `publish_cycle`, `publish_workspace_paths` ✅

   So the Protocol is 4 existing methods + 2 new ones (subscribe + acquire_plugin_session). Cheap.

## 7. Acceptance for THIS design

- Document reviewed and approved by Architect + Critic.
- New code is bounded to: `plugin_session.py`, `namespace_holder.py`, `setns_persistent_exec.py`, and either an extension to `setns_overlay_mount.py` or sibling `setns_umount.py`. Total ~210–240 lines.
- `_LinuxRuntime`'s ns_holder primitives parametrized with `namespaces=` and extracted to `namespace_holder.py`.
- A persistent-stdio exec helper exists (cannot reuse `_LinuxRuntime.run_in_handle` because it fork+waitpid+`capture_output=True`-buffers, killing `LspJsonRpcClient`'s persistent pipes).
- O(1) disk-usage invariant stated with conditions; lease-release paths in `PyrightSession` verified across success/error/evict.
- `PluginRuntime` Protocol surface frozen — implementation detail (Step 9 of PLAN) refines once approved.

## 8. Sequencing relative to existing PLAN.md

The 9 steps in `docs/plans/lsp_overlay_integration_PLAN.md` remain valid. **This design adds a Step 10** (and possibly a Step 11):

> **Step 10 (S6)** — Promote `_LinuxRuntime.spawn_ns_holder` + `open_ns_fds` + `run_in_handle` + `kill_holder` into `sandbox/overlay/namespace_holder.py` with `namespaces=` parameter. Move `setns_overlay_mount.py` + `setns_exec.py` from `isolated_workspace/scripts/` to `sandbox/overlay/scripts/`. Add `setns_persistent_exec.py` (setns + execvpe, no fork/wait). Extend `setns_overlay_mount.py` to umount-when-mountpoint OR add `setns_umount.py`. Add `sandbox/overlay/plugin_session.py` exposing the `PluginSession` dataclass + refresh + release + `spawn_persistent_proc`. Migrate `PyrightSession` to drive `PluginSession.spawn_persistent_proc(["pyright-langserver","--stdio"])`. Delete `plugins/catalog/lsp/runtime/namespace_remount.py`.

> **Step 11 (S7) — optional** — Migrate `IsolatedPipeline` to consume the same `namespace_holder.py` (with its default `namespaces=("user","mount","pid","net")`). Today's `_LinuxRuntime` is then trimmed to just the network/cgroup pieces.

Step 10 lands **after** Steps 1–9 because (a) it depends on the unified `OverlayHandle` from Step 6, (b) it depends on `subscribe_workspace_changes` from Step 2, (c) `PluginRuntime` Protocol from Step 9 is the consumer surface.

Acceptance for Step 10:
- `namespace_holder.py` exists; `namespaces=` parameter on `spawn_ns_holder` and `open_ns_fds`; `isolated_workspace` consumes the same module with its default four-namespace surface.
- `setns_persistent_exec.py` exists; LSP daemon's `proc.stdin`/`proc.stdout` pipes work end-to-end after spawn.
- `setns_overlay_mount.py` (or `setns_umount.py`) handles umount-first; remount-on-refresh integration test passes.
- `PyrightSession` no longer spawns its own `unshare -Urm`; it spawns `pyright-langserver` via `PluginSession.spawn_persistent_proc`.
- `plugins/catalog/lsp/runtime/namespace_remount.py` deleted.
- LSP integration tests green; foreign-publish refresh measured under <1s under unit-test loads.
- Disk-usage invariant verified by an integration test: spawn N=8 sessions, publish K=4 layers, assert layers GC'd within publish_cycle + foreign_watch interval (invariant I1 verified empirically).
