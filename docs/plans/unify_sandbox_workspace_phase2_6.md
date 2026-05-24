# Phase 2.6 — Isolated Workspace Cleanup + Symmetry

**Type:** Refactor + cleanup. Removes per-call serialization in `isolated_workspace`, removes dead defense (`freeze` + `freezer_degraded`), removes duplicated lease-guard logic, unifies the three parallel layer-stack Protocols, and aligns `__init__.py` export surfaces between `ephemeral_workspace/` and `isolated_workspace/`.

**Scope:** Per-call parallelism + structural symmetry + redundancy removal. No new features; behavior change limited to (a) iws tool calls now run concurrently within one session, (b) freeze syscalls removed, (c) wire-protocol shape unchanged via `release_workspace_snapshot` API alias during rollout window.

**Depends on:** Phase 2.5 (background lifecycle through daemon requests; `InFlightRegistry`, `BackgroundTaskManager.cancel_by_agent`, `api.v1.{cancel,heartbeat,inflight_count}`). All Phase 2.5 surfaces preserved.

**Blocks:** Phase 2.7 (TBD) — split `EphemeralPipeline`'s dual-mode coexistence (session-mounted vs per-tool-call) into two classes. Flagged here as §10 known limitation.

**Atomic commit plan:** 9 logical commits (one separable PR at C4). Commit order matters: C0 → C1 → C2 → C2.5 → C3 → C3.5a → C3.5b → C3.8 → C3.9 → (C4 separate PR).

See [`unify_sandbox_workspace.md`](unify_sandbox_workspace.md) for the trichotomy overview, [`unify_sandbox_workspace_phase2.md`](unify_sandbox_workspace_phase2.md) for foundation pipelines, and [`unify_sandbox_workspace_phase2_5.md`](unify_sandbox_workspace_phase2_5.md) for background lifecycle.

---

## 0. Why this is its own phase

Phase 2.5 shipped the canonical background-lifecycle design but did not touch the per-call mechanics inside the pipelines themselves. Three pain points surfaced after Phase 2.5 stabilized:

1. **`isolated_workspace` serializes calls within one session** via a per-handle `asyncio.Lock` wrapping every exec. Cgroup quotas (`_quota.py`) already enforce the resource limits the lock was implicitly defending; the lock blocks legitimate parallelism for nothing.

2. **`freeze` + `freezer_degraded` are dead defense.** `_runtime.freeze` writes `1` to the cgroup freezer between calls to "pause the namespace when idle" — but the only process in an idle iws is the `ns_holder` (`unshare --fork sleep`), which consumes ~0 CPU whether frozen or not. The `freezer_degraded` flag has **zero production consumers** (grep-verified — only test files reference it). Both exist as defense-in-depth for a threat model already covered by cgroup quotas + network policy.

3. **Massive structural redundancy between the two workspace folders:**
   - `_lock_for` + `_destroy_with_lease_guard` + `_handle_locks` + `_released_lease_ids` are byte-identical copies (`ephemeral_workspace/_operation.py:47-67` ≡ `isolated_workspace/pipeline.py:148-168`).
   - Three parallel `Protocol` classes for the same layer-stack concept (`OverlayLayerStackClient`, `LayerStackPort`, `WorkspaceLeaseClient`).
   - `__init__.py` export asymmetry — eph exports 1 symbol; iws exports 14 (including 4 leading-underscore privates).
   - Three "lifecycle" places in iws (`handlers.py`, `lifecycle/`, `_lifecycle.py` mixin) — partially addressed in Phase 2.5 C4 but the mixin remains.

The user directive that triggered this phase: *"make sure it cleans up the code, removes confusion, no redundant items in both workspace modules."* Phase 2.6 makes the redundancy and dead-defense removable as one coherent unit; the parallelism unlock is the natural by-product.

The user has consistently rejected cargo-cult preservation across multiple rounds: rejected `_freeze_gate` (preserves freeze for nothing), rejected `freezer_degraded` preservation (no consumers), rejected `iws_concurrent_calls_max` (mode-specific telemetry where workspace-wide would do), rejected `handle.lock` retention in exit (vestigial once exec-path drops it). The pattern crystallized into a principle: *when you remove a thing, check what was attached to it; if the attachment's only purpose was to interact with the removed thing, remove the attachment too.*

---

## 1. Principles (delta from Phase 2 + 2.5)

**P1 (revised — Phase 2.5 §1 Principle 1).** Per-call pipeline body matches its mode's actual mechanics. **The ephemeral 5-step shape (acquire → run → capture → commit → destroy) MUST NOT be force-fitted onto iws.** Iws's handle is persistent; iws's `run_tool_call` is naturally ~15 lines because there is no per-call create/destroy. Forcing length parity is manufactured symmetry; honest divergence is annotated, not hidden.

**P2 (NEW).** Don't remove a thing without examining its observable contract — **AND** confirming the contract has consumers. A wire field with no client branching on it is bytes, not a contract.

**P3 (NEW).** Symmetry only where it's honest. Apply this to telemetry, settings, and configuration, not just file layout. iws-specific machinery is OK for genuinely intrinsic concerns (TTL, quotas, network); not OK for things observed *about* iws (call counts, latency, freeze state).

**P4 (NEW).** When you remove a thing, check what was attached to it. If the attachment's only purpose was to interact with the removed thing, remove the attachment too. Don't leave hooks for absent partners.

**P5 (NEW).** Surgical changes; bundle only what shares a rollback boundary. Handler-shim collapse (C4) is separable from parallelism (C2) and goes as its own PR.

**P6 (preserved — Phase 2.5 §1 Principle 5).** Terminal-status precedence on race: `completed > failed > cancelled > running`. Unchanged.

---

## 2. Architecture — diagrams

**Reading order.** The 2×2 of {Ephemeral, Isolated} × {Foreground, Background} is NOT 4 different workflows. It's 2 substrates (eph vs iws — these have different daemon-side pipeline bodies) × 2 engine wrappers (FG vs BG — orthogonal to workspace mode). The diagrams below reflect this:

- §2.1 shows the **engine wrappers** (FG vs BG side-by-side). Both wrappers terminate in the same wire RPC envelope.
- §2.2 shows the **ephemeral substrate** (daemon-side pipeline body). Identical for FG and BG.
- §2.3 shows the **isolated substrate** (daemon-side pipeline body). Identical for FG and BG.
- §2.4 shows the **post-Phase-2.6 folder layout**.

Implementation-grounded claim: `req.background` is metadata only, never branched on inside `EphemeralPipeline.run_tool_call` or `IsolatedPipeline.run_tool_call` (grep-verified). The substrate is unbranched; the BG wrapper is genuinely thin.

### 2.1 Engine wrappers (FG vs BG; orthogonal to workspace mode)

```
                FOREGROUND                          │             BACKGROUND
═══════════════════════════════════════════════════ │ ════════════════════════════════════════════════════
agent: <tool>(args)                                 │ agent: <tool>(args, background=true)
   │                                                │    │
   ▼ engine.execute_tool_call (await inline)        │    ▼ engine.background.dispatch.launch_background_tool
   │                                                │    │   BackgroundTaskManager.launch(
   │                                                │    │     asyncio.create_task(coro),
   │                                                │    │     tag={agent_id, sandbox_id,
   │                                                │    │          sandbox_invocation_id, uses_sandbox=True})
   │                                                │    │   first launch starts 60s heartbeat loop
   │                                                │    │
   │                                                │    ◀── returns "bg_N" IMMEDIATELY; agent continues
   │                                                │
   │                                                │ ┄┄┄ later, inside the asyncio.Task ┄┄┄
   │                                                │    │
   ↓ one wire RPC                                   │    ↓ one wire RPC (SAME envelope shape)
     api.v1.<verb> {invocation_id,                  │      api.v1.<verb> {invocation_id,
                    background:false, ...}          │                     background:true, ...}
   │                                                │    │
═════════════════════ ENTERS SUBSTRATE (see §2.2 eph or §2.3 iws) ═══════════════════════
   │                                                │    │
   ← daemon response                                │    ← daemon response
   ▼ ToolResult returned inline to model            │    ▼ asyncio.Task done_callback
                                                    │      → _set_terminal_status(COMPLETED, result)
                                                    │      → progress_lines populated
                                                    │
                                                    │ ┄┄┄ later ┄┄┄
                                                    │    ▼ agent: check_background_task_result(bg_N)
                                                    │    ▼ → BackgroundTaskManager._tasks[bg_N].result

BG-ONLY machinery (no FG counterpart):

  CANCEL path:
    BackgroundTaskManager.cancel
      → sandbox_api.cancel(sandbox_id, invocation_id)   # wire-cancel FIRST
      → tracked.asyncio_task.cancel()                   # local cancel ALWAYS
    Daemon: InFlightRegistry.cancel_task(invocation_id) → task.cancel()
            → CancelledError propagates into substrate's try/finally
            → substrate's lease cleanup runs; NO OCC commit

  HEARTBEAT:
    Engine sends api.v1.heartbeat([invocation_ids]) every 60s
    Daemon updates InFlightRequest.last_seen for each

  ORPHAN/CRASH safety:
    Daemon InFlightRegistry TTL reaper (EOS_INFLIGHT_TTL_S=300s default)
      cancels BG tasks with stale last_seen → substrate's try/finally cleans up
    Engine restart with new process_id triggers immediate orphan reap
      (split-brain detection per Phase 2.5 §13)
```

### 2.2 Ephemeral substrate (daemon-side; identical for FG and BG)

```
[continues from §2.1 wire RPC]
   │
   ▼ daemon dispatcher: dispatch_envelope_async
   ▼ InFlightRegistry.register(invocation_id, task, background=<from envelope>)
   │   FG: background=False → not TTL-eligible
   │   BG: background=True  → TTL-eligible by reaper
   │
   ▼ daemon dispatch: run_tool_handler → resolve_pipeline
   ▼ resolve_pipeline:
   │   iws.get_handle(agent_id) is None  →  use EphemeralPipeline
   │
   ▼ EphemeralPipeline.run_tool_call(req):   ◀── IDENTICAL code path for FG and BG
   │                                              (zero branches on req.background)
   │
   │   handle = await overlay_lifecycle.create(layer_stack, agent_id=...)
   │   #         ├─ leases workspace snapshot via LayerStackPort
   │   #         ├─ allocates fresh upperdir/workdir under runtime/overlay/
   │   #         └─ returns OverlayHandle bound to layer_paths
   │   try:
   │     result = await run_in_namespace(handle, req)
   │     #        ├─ spawns `unshare -Urm python -m sandbox.overlay.namespace_entrypoint`
   │     #        ├─ child mounts overlay {lowerdir=layer_paths, upperdir, workdir}
   │     #        ├─ chroot to workspace_root, exec command
   │     #        └─ writes stdout/stderr to *_ref files
   │     if req.intent == WRITE_ALLOWED:
   │       path_changes = await overlay_lifecycle.capture_changes(handle)
   │       result = await self._commit_and_attach(result, path_changes, ...)
   │       #          └─ OCC apply_changeset → may stale-base-reject, may publish
   │     return result
   │   finally:
   │     await self._lease_guard.destroy(handle, overlay_lifecycle.destroy)
   │     #     ├─ releases layer-stack lease (idempotent)
   │     #     ├─ rmtrees upperdir/workdir
   │     #     └─ shared with iws via _shared/lease_guard.py
   │
   ▼ InFlightRegistry.deregister(invocation_id)
[returns to §2.1 wire response]
```

### 2.3 Isolated substrate (daemon-side; identical for FG and BG)

```
PREREQUISITE: enter_isolated_workspace called previously
  → lease + scratch_dir + persistent namespace + veth + cgroup all exist
  → IsolatedWorkspaceHandle stored in pipeline._handles + _by_agent

[continues from §2.1 wire RPC]
   │
   ▼ daemon dispatcher: dispatch_envelope_async
   ▼ InFlightRegistry.register (same as eph; background flag is metadata)
   ▼ resolve_pipeline:
   │   iws.get_handle(agent_id) is not None  →  use IsolatedPipeline
   │
   ▼ IsolatedPipeline.run_tool_call(req):   ◀── ~15 lines; IDENTICAL for FG and BG
   │                                              (zero branches on req.background)
   │
   │   handle = self.get_handle(req.agent_id)         # already-leased; no acquire
   │   if handle is None: raise IsolatedWorkspaceError("no_isolated_workspace")
   │   overlay = self._overlay_handle(handle)         # adapt to OverlayHandle
   │
   │   result = await run_in_namespace(overlay, req, isolated_runner=...)
   │   #        ├─ Phase 2.5 cancellation-aware variant
   │   #        ├─ setns into persistent ns (no fresh unshare)
   │   #        ├─ no mount (overlay was mounted at enter)
   │   #        └─ run_in_handle (post-Phase-2.6):
   │   #             - NO handle.lock          → TRUE PARALLELISM across concurrent
   │   #             - NO freeze/unfreeze         FG/BG calls in same iws
   │   #             - subprocess.run via setns_exec into persistent ns
   │   #             - run_in_executor: doesn't block other agents' loops
   │
   │   if req.intent == WRITE_ALLOWED:
   │     changes = await overlay_lifecycle.capture_changes(overlay)
   │     result["changed_paths"] = [c.path for c in changes]
   │     # observability ONLY — NO OCC commit; writes drop at exit_isolated_workspace
   │
   │   result["workspace"] = "isolated"
   │   handle.last_activity = self._clock()    # benign monotonic-clock race
   │   return result
   │
   ▼ InFlightRegistry.deregister(invocation_id)
[returns to §2.1 wire response]

LEASE / UPPERDIR LIFECYCLE: enter_isolated_workspace → exit_isolated_workspace
   exit:
     1. cancel_by_agent(agent_id, grace_s) ── drains in-flight BG tasks for this agent
        (per Phase 2.5; reuses the cancel path from §2.1's BG-ONLY machinery)
     2. _teardown: kill ns_holder, teardown veth, release_lease,
                   rmtree(scratch_dir)    ── upperdir DELETED here (writes drop)
```

### 2.4 Post-Phase-2.6 folder layout (architecture diagram)


```
sandbox/
  _shared/                                  # NEW + EXPANDED
    workspace_pipeline.py    # WorkspacePipeline Protocol (Phase 2)
    lease_guard.py           # NEW (C2.5) — LeaseGuard class, sole owner of
                             #              _handle_locks + _released_lease_ids
    layer_stack_port.py      # NEW (C3.5b) — single LayerStackPort Protocol,
                             #               replaces 3 parallel Protocols
    shell_contract.py        # MOVED from ephemeral_workspace/ (C3)
    tool_primitives/
      cancellation.py        # Phase 2.5
      shell.py
      ...
    ports.py
  ephemeral_workspace/         # TOP LEVEL = PUBLIC SURFACE only
    __init__.py              # exports {EphemeralPipeline} ONLY (C3.8)
    pipeline.py              # PUBLIC — EphemeralPipeline facade
    events.py                # PUBLIC-ish — WorkspaceChangeEvent bus (runtime
                             # control flow for _watch_foreign_publishes — NOT audit)
    plugin/                  # PUBLIC subsystem (eph-only intrinsic)
    helper/                  # NEW (C3.9) — private internals
      __init__.py
      manager.py             # was _manager.py — singleton + bootstrap
      operation.py           # was _operation.py — EphemeralOperationMixin
                             # NOTE: _lock_for + _destroy_with_lease_guard
                             # REMOVED in C2.5 (moved to _shared/lease_guard.py)
      publishing.py          # was _publishing.py — EphemeralPublishMixin (OCC)
      types.py               # was _types.py — OperationOverlayHandle, _OverlaySnapshot
                             # NOTE: OverlayLayerStackClient REMOVED in C3.5b
      utils.py               # was _utils.py
  isolated_workspace/          # TOP LEVEL = PUBLIC SURFACE only
    __init__.py              # exports {IsolatedPipeline, IsolatedWorkspaceError,
                             #          IsolatedWorkspaceHandle, AuditSink} (C3.8)
    pipeline.py              # PUBLIC — IsolatedPipeline facade
    network.py               # PUBLIC subsystem — bridge/nftables/veth (iws intrinsic)
    scripts/                 # PUBLIC subsystem — setns subprocess helpers
    helper/                  # NEW (C3.9) — private internals
      __init__.py
      manager.py             # was _manager.py — singleton + bootstrap (mirrors eph)
      lifecycle.py           # was _lifecycle.py — enter/exit/_teardown mixin
      gc.py                  # was _gc.py — orphan reaper mixin (KEPT; C3.7 dropped)
      ttl.py                 # was _ttl.py — eviction loop mixin (KEPT)
      quota.py               # was _quota.py — host-memory gate mixin (KEPT)
      runtime.py             # was _runtime.py — _LinuxRuntime (cgroup/setns/exec)
                             # NOTE: freeze() + SIGSTOP fallback REMOVED in C1
      types.py               # was _types.py — IsolatedWorkspaceHandle, _ManagerConfig,
                             # _PhaseTimer
                             # NOTE: freezer_degraded + lock REMOVED in C1+C2
                             #       LayerStackPort REMOVED in C3.5b
                             #       AuditSink stays here (or moves to _shared/)
    # DELETED in C4 (separate PR): handlers.py, lifecycle/
  daemon/
    rpc/
      dispatcher.py          # single dict[str, Callable] post-C4
      in_flight.py
      server.py
    dispatch.py              # run_tool_handler unchanged
    workspace_server.py      # API alias for release_workspace_snapshot during
                             # rollout window (per C3.5a)
    # DELETED in C4 (separate PR): handler/{shell,edit,glob,grep,read,write,
    #                                       workspace,cancel,health,metrics}.py
```

---

## 3. RALPLAN-DR Summary

### Decision Drivers (top 3)

1. **User's lived asymmetry pain.** iws calls serialize unnecessarily; cgroup quotas already enforce the limits freeze defends. Two folders with the same product purpose feel like different products.
2. **Operational complexity reduction.** Each iws tool call today pays: 1× `handle.lock` acquire + 2× freeze syscalls + 1× lease-dict lookup, all for defense already provided by quotas + map-level lock. Removing pays back per call forever.
3. **Cargo-cult elimination.** Multiple rounds of pushback in the design conversation surfaced a repeating anti-pattern: machinery preserved because it touches something we're keeping, not because the system needs it. Phase 2.6 codifies the rule via P4 and applies it systematically.

### Viable Options (with bounded pros/cons)

#### Option α — Refcount `FreezeGate` preserving freeze-when-idle (REJECTED)
Replace `handle.lock` with `FreezeGate` refcount; freeze on 0→0 transition only.
- **Pro:** preserves "freeze-when-idle" property; parallelism allowed.
- **Con:** freeze adds no functional value once cgroup quotas + network policy are in place; introduces a new sync primitive for a property that has no consumers. **User explicitly pushed back: "why do we need _freeze_gate.py?"**

#### Option β — Symmetry-only, no parallelism (REJECTED)
Pure refactor: extract `_manager.py`, relocate `shell_contract.py`, collapse handlers. No behavior change.
- **Pro:** minimal blast radius.
- **Con:** punts the headline ask (parallel calls); leaves operational complaint unaddressed.

#### Option γ — Drop freeze + lock + collapse handlers + adapter.py + wiring.py in one plan (REJECTED — V1)
Original draft. Bundle parallelism with handler-rewrite.
- **Pro:** all done in one go.
- **Con:** blast radius amplified for a goal needing ~30 LOC; "adapter + wiring" was "handlers wearing a hat" — same pattern under a different name.

#### Option δ — Drop freeze + lock + extract shared lease_guard + unify Protocols + audit-unify + mixin-collapse (REJECTED — V4)
V3 plus C3.6 (audit unification) plus C3.7 (mixin collapse).
- **Pro:** more cleanup on paper.
- **Con:** C3.6 misreads `event_bus` (it's runtime control flow for `_watch_foreign_publishes`, not audit); iws JSONL is consumed by 20+ tests; C3.7 collapses focused phase-mixins into a 600L god class.

#### Option ε (CHOSEN — V4-pruned) — 8 commits, surgical, with §10 limitation flag
Detailed in §4–§8 below.
- **Pro:** removes real redundancy (lease-guard, layer-stack Protocols, export surface) without breaking working separation (audit JSONL, lifecycle-phase mixins); preserves all wire contracts via API alias; explicitly flags the dual-mode `EphemeralPipeline` confusion for Phase 2.7.
- **Con:** 8 commits is more than V1's "one plan"; protocol unification touches wire-protocol surface (mitigated by API alias in C3.5a).

### Invalidation rationale for α / β / γ / δ

- **α** preserves machinery for an absent consumer (freeze-when-idle has no observer); contradicts P2 + P4.
- **β** punts the headline ask; user explicitly wanted parallelism + symmetry.
- **γ** bundles concerns without shared rollback boundary; contradicts P5.
- **δ** misreads two non-redundant patterns as redundant (`event_bus` vs JSONL serve different purposes; 4 iws mixins decompose by lifecycle phase, not arbitrarily); contradicts P3's "honest symmetry only."

ε is the only option that satisfies "remove cargo-cult + remove real redundancy + preserve working separation + surface dual-mode confusion" simultaneously.

---

## 4. Module changes — DELETES

### 4.A. Code paths to remove

- **`_runtime.freeze()` + SIGSTOP fallback path** at `isolated_workspace/_runtime.py:199-231`. Replaces removed: the entire freeze/unfreeze call sites in `run_in_handle` (`pipeline.py:213`, `:238`).
- **`freezer_degraded` field** at `isolated_workspace/_types.py:148` and its persistence at `:170`.
- **`freezer_degraded`** in `status` RPC response at `isolated_workspace/handlers.py:151`.
- **`handle.lock` field** at `isolated_workspace/_types.py:157` (`lock: asyncio.Lock = field(default_factory=asyncio.Lock)`); both its writers at `pipeline.py:213` (exec wrap) and `_lifecycle.py:188` (exit wrap).
- **Duplicate `_lock_for` + `_destroy_with_lease_guard`** at `isolated_workspace/pipeline.py:148-168` (kept in `_shared/lease_guard.py` via composition; see C2.5).
- **Duplicate `_lock_for` + `_destroy_with_lease_guard`** at `ephemeral_workspace/_operation.py:47-67` (same, kept in shared module).
- **`OverlayLayerStackClient` Protocol** at `ephemeral_workspace/_types.py:15-33`. Replaced by `_shared/layer_stack_port.py::LayerStackPort` (see C3.5b).
- **`LayerStackPort` Protocol** at `isolated_workspace/_types.py:111-119`. Same replacement.
- **`WorkspaceLeaseClient` Protocol** at `ephemeral_workspace/shell_contract.py:104-115`. Same replacement.
- **`_LayerStackAdapter` wrapper** at `isolated_workspace/handlers.py:43-58`. Becomes unnecessary post-C3.5b (iws binds `LayerStack` once at construction).
- **`failure_modes/test_freezer_stall_falls_back_to_sigstop.py`** — entire file.
- **`freezer_degraded is False` assertion** at `happy_path/test_status_reports_open_handle.py:54-56`.
- **`iws_concurrent_calls_max` gauge** — never added; documented here as a non-goal (user-rejected mode-specific telemetry).

### 4.B. Doc references to update (not delete; rewrite)

- `task_center_runner/tests/mock/sandbox/isolated_workspace/PLAN.md:308` — strike R11 row.
- `task_center_runner/tests/mock/sandbox/isolated_workspace/NEXT-AGENT-GUIDE.md:399` — strike freezer fallback paragraph.
- `task_center_runner/tests/mock/sandbox/isolated_workspace/IMPLEMENTATION-REPORT.md:178` — strike R11 implementation note.

### 4.C. Files to delete in C4 (separate PR)

- `sandbox/daemon/handler/{shell,edit,glob,grep,read,write,workspace,cancel,health,metrics}.py` — **11 files** (the directory contains 11 `.py` files including `__init__.py`; the entire `daemon/handler/` directory is removed).
- `sandbox/isolated_workspace/handlers.py` — entire file.
- `sandbox/isolated_workspace/lifecycle/` — entire directory (3 files: `__init__.py`, `enter_isolated_workspace.py`, `exit_isolated_workspace.py`).

---

## 5. Module changes — ADDS

### 5.0 (C0) Baseline metrics

`tests/baselines/iws_serial_call_timing.json` — captures current serial-call timing for 5 shells × 200ms in one iws (expected ≈1.05s pre-Phase-2.6; ≈300ms post-C2).
`tests/baselines/iws_freeze_syscalls.strace` — captures current freeze syscall count per call (expected 2/call pre-C1; 0/call post-C1).

Pure additions; no production code change. Regression guards for C1 + C2.

### 5.1 (C1) Remove freeze + freezer_degraded entirely

- Delete `_runtime.freeze()` and SIGSTOP fallback (`_runtime.py:199-231`).
- Delete `freezer_degraded` field (`_types.py:148, 170`).
- Remove from `status` RPC response (`handlers.py:151`).
- Delete `failure_modes/test_freezer_stall_falls_back_to_sigstop.py`.
- Remove assertion at `happy_path/test_status_reports_open_handle.py:54-56`.
- Update doc references at `PLAN.md:308`, `NEXT-AGENT-GUIDE.md:399`, `IMPLEMENTATION-REPORT.md:178`.
- CHANGELOG entry: *"freeze removed; cgroup quotas (`_quota.py`) are the sole resource boundary for idle isolated workspaces."*

**Migration**: any client persisting old handle JSON with `freezer_degraded` will see the field ignored on rehydrate (extra fields tolerated). Zero production consumers grep-confirmed — no client breaks.

### 5.2 (C2) Drop handle.lock entirely + enable parallelism

Three lock surfaces, three decisions:

| Lock surface | File:line | Action | Rationale |
|---|---|---|---|
| `handle.lock` exec wrap | `pipeline.py:213` | **DROP** | Per-call tool serialization; freeze pair is gone post-C1 so dropping is safe |
| `handle.lock` exit wrap | `_lifecycle.py:188` | **DROP** | Vestigial once exec-path drops it — protects nothing |
| `self._handle_locks` lease-keyed | `pipeline.py:148` | **MOVE** to `_shared/lease_guard.py` (C2.5) | Lease destroy race protection |

- Grep `handle.lock` post-drop; if zero references remain, remove `lock: asyncio.Lock` field from `IsolatedWorkspaceHandle` at `_types.py:157`.
- Add comment at `pipeline.py:239`: `# Benign single-writer monotonic clock; CPython scalar atomicity. Do NOT add multi-field state here without re-introducing serialization.`
- Audit `_ttl.py` / `_gc.py` for serialization assumptions: `grep -n "handle.lock\|last_activity" backend/src/sandbox/isolated_workspace/_{ttl,gc}.py`; document any.
- Document the accepted exit-vs-in-flight race at exit's teardown site: *"Teardown may run concurrently with in-flight calls that already obtained the handle reference. `_teardown` suppresses all exceptions; in-flight subprocesses surface I/O errors as tool failures. Agent has called exit, so ongoing iws-bound work is by definition not expected to succeed."*

**Tests**:
- New: `tests/concurrency/test_iws_parallel_tool_calls.py` — 5 shells × 200ms, assert `total < 1.5 * max(t_i) = 300ms`; baseline serial was `>4.5 * max(t_i) = 900ms`.
- New: `tests/concurrency/test_iws_exit_during_inflight_call.py` — start 1s shell, fire `exit_isolated_workspace` at 100ms, assert exit completes within `grace_s + 100ms` and in-flight tool call returns tool-level error (not daemon crash).

### 5.3 (C2.5) Extract lease guard to `_shared/lease_guard.py`

```python
# sandbox/_shared/lease_guard.py (NEW, ~50 lines)
import asyncio
from collections.abc import Callable, Awaitable

class LeaseGuard:
    """Sole owner of per-pipeline lease-destroy race protection.

    Owns the lease-keyed lock dict and the released-set. Both EphemeralPipeline
    and IsolatedPipeline compose one instance. Single source of truth for the
    lease-release semantics — removes the duplicated _lock_for +
    _destroy_with_lease_guard from both pipelines.
    """

    def __init__(self) -> None:
        self._handle_locks: dict[str, asyncio.Lock] = {}
        self._released_lease_ids: set[str] = set()

    def lock_for(self, lease_id: str) -> asyncio.Lock:
        lock = self._handle_locks.get(lease_id)
        if lock is None:
            lock = self._handle_locks[lease_id] = asyncio.Lock()
        return lock

    async def destroy(self, handle, destroy_fn: Callable[[object], Awaitable[None]]) -> None:
        async with self.lock_for(handle.lease_id):
            if handle._destroyed:
                self._handle_locks.pop(handle.lease_id, None)
                return
            if handle.lease_id and handle.lease_id in self._released_lease_ids:
                handle._destroyed = True
                self._handle_locks.pop(handle.lease_id, None)
                return
            if handle.lease_id:
                self._released_lease_ids.add(handle.lease_id)
            try:
                await destroy_fn(handle)
            finally:
                self._handle_locks.pop(handle.lease_id, None)

    def mark_released(self, lease_id: str) -> None:
        """Called by EphemeralPipeline._release_lease (the SECOND writer
        to _released_lease_ids that the architect identified)."""
        if lease_id:
            self._released_lease_ids.add(lease_id)
```

- **Critical**: `EphemeralPipeline._release_lease` at `pipeline.py:294-299` writes to `_released_lease_ids` directly today; this is the second writer the architect flagged. It MUST be routed through `LeaseGuard.mark_released(lease_id)` so the shared module owns the set fully.
- Delete `EphemeralOperationMixin._lock_for` and `_destroy_with_lease_guard` from `_operation.py:47-67`.
- Delete iws's identical copies at `pipeline.py:148-168`.
- Delete `_released_lease_ids: set[str]` and `_handle_locks: dict[str, asyncio.Lock]` fields from both pipeline `__init__` (`ephemeral_workspace/pipeline.py:73-74` and `isolated_workspace/pipeline.py:73-74`).
- Both pipelines now compose `self._lease_guard = LeaseGuard()` in `__init__`; all call sites delegate.
- Net: −40 lines duplicated code; one place to fix bugs in lease-destroy race protection.

### 5.4 (C3) Honest symmetry — `_manager.py` + `shell_contract.py` relocation + divergence comments

**Extract `isolated_workspace/_manager.py`** (mirroring eph). Concrete file inventory:

From `isolated_workspace/pipeline.py:325-349`:
- `_pipeline_singleton: IsolatedPipeline | None`
- `set_pipeline(pipeline)`
- `get_active_pipeline()`
- `require_pipeline()`
- `require_arg(args, key)`

From `isolated_workspace/handlers.py:38-107`:
- `_bootstrap_lock: asyncio.Lock`
- `_ensure_manager(args) -> IsolatedPipeline`
- `_LayerStackAdapter` (deleted entirely in C3.5b)
- `_JsonlAuditSink`
- `_resolve_audit_path()`
- `DEFAULT_AUDIT_JSONL_PATH`

**Relocate `shell_contract.py` from `ephemeral_workspace/` to `_shared/`**:

Update **8 importers**:
- `_shared/ports.py:3`
- `overlay/namespace_runner.py:27`
- `overlay/lifecycle.py:9`
- `ephemeral_workspace/_types.py:9`
- `ephemeral_workspace/_publishing.py:20`
- `ephemeral_workspace/pipeline.py:34`
- `ephemeral_workspace/plugin/overlay_dispatch.py:14`
- plus any transitive uses

**Critical**: update string literal at `host/runtime_bundle.py:217` (worker bundle file inventory — silent boot break otherwise).

**CI gate**: worker image cold-boot test must pass post-relocate.

**Add divergence comments** to each pipeline's class docstring (rescues the intent of dropped C3.6 in 6 lines, zero risk):

```python
# In EphemeralPipeline class docstring:
"""...
Audit/event divergence vs IsolatedPipeline:
  EphemeralPipeline uses `events.WorkspaceChangeEvent` via in-process
  `event_bus.emit()` — this is RUNTIME CONTROL FLOW consumed by
  `_watch_foreign_publishes`, not audit. For lifecycle audit, see
  IsolatedPipeline's `_JsonlAuditSink` pattern.
"""

# In IsolatedPipeline class docstring:
"""...
Audit/event divergence vs EphemeralPipeline:
  IsolatedPipeline uses `_JsonlAuditSink` writing
  `sandbox_isolated_workspace_*` events to a JSONL file. This is
  AUDIT (consumed by 20+ tier-3 tests parsing exact event-type strings),
  not runtime control flow. For runtime events, see EphemeralPipeline's
  `event_bus` pattern.
"""
```

**Add divergence comment in iws `run_tool_call`**:
```python
# iws handle is persistent — no per-call create/destroy.
# WRITE_ALLOWED captures changed_paths for audit ONLY; no OCC commit;
# writes drop at exit_isolated_workspace via shutil.rmtree(scratch_dir).
```

**OCC contract test** `tests/contracts/test_iws_does_not_import_occ.py`:
```python
from pathlib import Path

def test_iws_does_not_import_occ_mutation_client():
    iws_files = Path("backend/src/sandbox/isolated_workspace").rglob("*.py")
    offenders = [
        f for f in iws_files
        if "OCCMutationClient" in f.read_text()
        or "from sandbox.occ" in f.read_text()
    ]
    assert not offenders, f"isolated_workspace must not import OCC: {offenders}"
```

Pin glob scope: rescope to also include `_shared/` post-C4 if needed.

**Do NOT force iws body to ≤25 lines.** Honest target: ~15 lines (iws has less to do per-call than eph).

Preserve all `__init__.py` re-exports through C3 → C4 transition; pre-C4 grep `grep -rn 'from sandbox.isolated_workspace.{handlers,lifecycle}' backend/` must be 0 before deletion.

### 5.5 (C3.5a) Rename `release_workspace_snapshot` → `release_lease` with API alias

**Why split** from C3.5b: this commit touches the wire-protocol surface (`api.release_workspace_snapshot` at `daemon/rpc/dispatcher.py:273`). Splitting from the Protocol-collapse keeps the rollback boundary tight.

- Rename internal Python method: `release_workspace_snapshot` → `release_lease` across:
  - `daemon/workspace_server.py:144` (definition)
  - `daemon/handler/workspace.py:92-94` (handler)
  - iws callers: `_gc.py:163`, `_lifecycle.py:79, :239`
- **Add wire-protocol alias** at `daemon/rpc/dispatcher.py:273`: register BOTH `api.release_workspace_snapshot` (legacy) AND `api.release_lease` (new) → same handler. Legacy alias logs WARN with `deprecated_alias=api.release_workspace_snapshot` on use during rollout window (one release cycle).
- CHANGELOG entry: *"`api.release_workspace_snapshot` deprecated in favor of `api.release_lease`; alias remains during rollout window."*

### 5.6 (C3.5b) Protocol unification — `_shared/layer_stack_port.py`

```python
# sandbox/_shared/layer_stack_port.py (NEW)
from pathlib import Path
from typing import Protocol
from sandbox._shared.shell_contract import WorkspaceSnapshotLease, SnapshotManifest

class LayerStackPort(Protocol):
    """Single canonical Protocol for layer-stack access from workspace pipelines.

    Replaces (deleted): OverlayLayerStackClient, LayerStackPort (iws),
    WorkspaceLeaseClient. All three had ~80% overlap; their divergence was
    bootstrap-shape (per-call vs bound), addressed by iws's bootstrap rebind.
    """
    storage_root: Path

    def prepare_workspace_snapshot(self, *, request_id: str) -> WorkspaceSnapshotLease: ...
    def release_lease(self, *, lease_id: str) -> bool: ...
    def read_active_manifest(self) -> SnapshotManifest: ...
```

**Critical bootstrap rebind** (per architect): iws's `_ensure_manager` (now in `_manager.py` post-C3) calls `workspace_server.get_layer_stack_manager(root)` ONCE at pipeline construction; stores bound `LayerStack` on the pipeline; drops per-call `layer_stack_root` arg from all snapshot/release calls.

- Delete the three Protocol classes per §4.A.
- Delete `_LayerStackAdapter` wrapper at `handlers.py:43-58` (no longer needed; iws binds directly).
- Update callers: `_gc.py:163`, `_lifecycle.py:79`, `_lifecycle.py:239` (no longer pass `layer_stack_root`; use the bound port instance).

### 5.7 (C3.8) `__init__.py` export-surface alignment

**eph `__init__.py`** — exports unchanged (already minimal): `{EphemeralPipeline}` plus any typed Request/Result types it accepts/returns.

**iws `__init__.py`** — STOP exporting (today exports 14 symbols including 4 leading-underscore privates):
- `_LinuxRuntime` (impl detail)
- `_PhaseTimer` (impl detail)
- `_PHASE_TIMER_OVERHEAD_BUDGET_MS` (constant)
- `_ManagerConfig` (impl detail)
- `set_pipeline`, `require_pipeline`, `require_arg`, `get_active_pipeline` (now live in `_manager.py`; not part of public iws surface)
- `LayerSnapshotLike` (subsumed by `LayerStackPort` in `_shared/`)
- `LayerStackPort` (relocated to `_shared/`)

**iws final export set**: `{IsolatedPipeline, IsolatedWorkspaceError, IsolatedWorkspaceHandle, AuditSink}`.

**Why `AuditSink` stays**: dropped C3.6 left iws's JSONL audit pattern intact; `AuditSink` is the Protocol for the sink. External callers (test harness, daemon bootstrap) instantiate `_JsonlAuditSink` via this Protocol. Alternative: move `AuditSink` to `_shared/` if a future audit consumer outside iws materializes.

**Static lint** `tests/static/test_workspace_export_surface.py` (NEW) — pins the alphabetical export list for both modules. Catches regressions where a future implementer re-leaks privates.

### 5.8 (C3.9) Move private files into `helper/` subfolders

**Goal**: physically separate public from private at the directory boundary. The leading-underscore convention is a soft signal; a subfolder is a hard one. Reduces top-level clutter from ~10 files to ~3-4 per workspace folder.

**Rationale**: post-C3.8 the public surface is minimal (1 export for eph, 4 for iws), but the file system still lists ~10 files at the top level mixing the public pipeline with private internals. A `helper/` folder makes the public/private boundary visible to anyone browsing the directory.

**Convention chosen**: folder name `helper/` (no leading underscore); file names inside drop the leading underscore (the folder boundary IS the privacy signal — underscored filenames inside `helper/` would be redundant). Alternative `_internal/` (numpy/scipy convention) considered but rejected for readability.

**Files moved** (rename, not delete):

ephemeral_workspace/:
| Before | After |
|---|---|
| `_manager.py` | `helper/manager.py` |
| `_operation.py` | `helper/operation.py` |
| `_publishing.py` | `helper/publishing.py` |
| `_types.py` | `helper/types.py` |
| `_utils.py` | `helper/utils.py` |

isolated_workspace/:
| Before | After |
|---|---|
| `_manager.py` | `helper/manager.py` |
| `_lifecycle.py` | `helper/lifecycle.py` |
| `_gc.py` | `helper/gc.py` |
| `_ttl.py` | `helper/ttl.py` |
| `_quota.py` | `helper/quota.py` |
| `_runtime.py` | `helper/runtime.py` |
| `_types.py` | `helper/types.py` |

**Files staying top-level** (PUBLIC):
- eph: `__init__.py`, `pipeline.py`, `events.py`, `plugin/`
- iws: `__init__.py`, `pipeline.py`, `network.py`, `scripts/`

`events.py` (eph) and `network.py` (iws) stay top-level because they expose types/subsystems consumed by external code today. If a future audit confirms zero external consumers, move them in a follow-up.

**Mechanical migration**:
1. Create `helper/__init__.py` in each workspace folder (empty file or with a one-line docstring `"""Private internals; do not import from outside this package."""`).
2. `git mv` each file with the rename (preserves history).
3. Update internal imports within the workspace folder: `from sandbox.ephemeral_workspace._manager import ...` → `from sandbox.ephemeral_workspace.helper.manager import ...`.
4. Update `__init__.py` re-exports: `from ._types import X` → `from .helper.types import X`.
5. Update external importers: anywhere outside the workspace folder importing privates (post-C3.8 this should be only the daemon bootstrap; grep to confirm).
6. Re-run `tests/static/test_workspace_export_surface.py` to confirm public surface unchanged.

**Import-path changes (full list to verify with grep before commit)**:
- `from sandbox.ephemeral_workspace._manager import` → `from sandbox.ephemeral_workspace.helper.manager import`
- `from sandbox.ephemeral_workspace._operation import` → `from sandbox.ephemeral_workspace.helper.operation import`
- `from sandbox.ephemeral_workspace._publishing import` → `from sandbox.ephemeral_workspace.helper.publishing import`
- `from sandbox.ephemeral_workspace._types import` → `from sandbox.ephemeral_workspace.helper.types import`
- `from sandbox.ephemeral_workspace._utils import` → `from sandbox.ephemeral_workspace.helper.utils import`
- `from sandbox.isolated_workspace._manager import` → `from sandbox.isolated_workspace.helper.manager import`
- `from sandbox.isolated_workspace._lifecycle import` → `from sandbox.isolated_workspace.helper.lifecycle import`
- `from sandbox.isolated_workspace._gc import` → `from sandbox.isolated_workspace.helper.gc import`
- `from sandbox.isolated_workspace._ttl import` → `from sandbox.isolated_workspace.helper.ttl import`
- `from sandbox.isolated_workspace._quota import` → `from sandbox.isolated_workspace.helper.quota import`
- `from sandbox.isolated_workspace._runtime import` → `from sandbox.isolated_workspace.helper.runtime import`
- `from sandbox.isolated_workspace._types import` → `from sandbox.isolated_workspace.helper.types import`

**Verification**:
- `git log --follow helper/<file>.py` returns full history of the pre-rename file (rename detection).
- `grep -rn 'from sandbox\.\(ephemeral\|isolated\)_workspace\._' backend/` returns 0 (all old paths gone).
- All tests pass; `pytest` exit 0.
- Static export-surface lint still green.

**Why bundled in one commit** (both folders together): the symmetry is exactly the point — both folders end up with the same shape (`__init__.py`, `pipeline.py`, public-subsystems, `helper/`). Splitting into eph-then-iws would temporarily leave the codebase asymmetric for one commit's worth of history; doing both together makes the symmetric structure land atomically.

### 5.9 (C4 — separate PR) Handler collapse

- **Interpretation A** confirmed: single `dict[str, Callable]` in `dispatcher.py` replaces all of `daemon/handler/*.py`. Co-locates ~200 lines of verb routing in one file. No new `adapter.py` or `wiring.py` files.
- iws lifecycle methods become typed methods on `IsolatedPipeline.{enter, exit, status, list_open, test_reset}`; dispatcher inline lambda marshals `Request.from_payload(args)`.
- **Delete**: `daemon/handler/*.py` (11 files total — verify via `find backend/src/sandbox/daemon/handler -name "*.py" | wc -l` returns 0 post-deletion), `isolated_workspace/handlers.py`, `isolated_workspace/lifecycle/` (3 files).
- Acceptance greps:
  - `grep -rn 'from sandbox.daemon.handler' backend/src/` returns 0
  - `grep -rn 'from sandbox.isolated_workspace.handlers' backend/src/` returns 0
  - `grep -rn 'from sandbox.isolated_workspace.lifecycle' backend/src/` returns 0
- Wire-protocol round-trip test: `tests/contracts/test_iws_rpc_envelopes.py`.
- Update OCC contract test glob (from C3) to drop `lifecycle/` path reference once deleted.

---

## 6. Redundancy fix summary (RxN table)

| ID | Redundancy | Pre-Phase-2.6 | Post-Phase-2.6 | Commit | Lines removed |
|---|---|---|---|---|---|
| R1 | `_lock_for` + `_destroy_with_lease_guard` duplicated | 2 copies (eph + iws) | 1 in `_shared/lease_guard.py` | C2.5 | ~40 |
| R2 | Three layer-stack Protocols | `OverlayLayerStackClient` + `LayerStackPort` + `WorkspaceLeaseClient` | 1 `LayerStackPort` in `_shared/` | C3.5b | ~30 |
| R3 | Two audit patterns | `event_bus` (eph runtime) + `_emit` (iws JSONL) | UNCHANGED — different purposes per architect; divergence-comment docstrings added | C3 (docstring only) | 0 (intentional) |
| R4 | Mixin asymmetry | 2 eph + 4 iws | UNCHANGED — iws's 4-mixin split is by lifecycle phase | — | 0 (intentional) |
| R5 | `__init__.py` export asymmetry | 1 eph + 14 iws (4 privates) | 1 eph + 4 iws | C3.8 | ~10 |
| R6 | Three "lifecycle" places in iws | `handlers.py` + `lifecycle/` + `_lifecycle.py` mixin | only `_lifecycle.py` mixin | C4 | ~140 |
| R7 | `freeze` + `freezer_degraded` dead defense | present, zero consumers | deleted | C1 | ~50 |
| R8 | `handle.lock` per-call serialization | present, blocks parallelism | deleted | C2 | ~5 |
| R9 | `shell_contract.py` mis-located (used by both modes) | in `ephemeral_workspace/` | in `_shared/` | C3 | 0 (moved) |
| **Total** | | | | | **~275 lines** |

---

## 7. Pre-mortem (10 scenarios)

1. **Concurrent `setns_exec` FD/ns scrambling.** Verified safe by architect: helper is single-threaded by R10 import discipline; each invocation is its own subprocess; `cgroup.procs` writes append-safely. Mitigation: 100-shell parallel test runs in CI; assert no FD leaks, no zombies, no overlay corruption.

2. **`last_activity` race after lock removal.** Benign on CPython (GIL-protected scalar, monotonic single-writer). Annotated at mutation site; concurrent unit test pins; comment forbids multi-field state additions without re-introducing serialization. On non-CPython runtimes (PyPy), use `time.monotonic_ns()` writes (integer-atomic on all platforms).

3. **Freeze removal breaks compliance audit.** Grep-confirmed zero production consumers of `freezer_degraded`. Migration: extra field tolerated on rehydrate. CHANGELOG note for any compliance reviewer asking after the fact. If a real compliance requirement surfaces, freeze can be re-added as a workspace-wide concern (`EOS_WORKSPACE_FREEZER` flag), not an iws hidden knob.

4. **JSONL audit interleave under concurrent calls.** `append_jsonl_event` uses `O_APPEND` (verified); safe for writes under PIPE_BUF (4096 bytes typical). Mitigation: soak test fuzzes payload sizes **including >4KB** to exercise partial-write boundary; verify no torn JSON.

5. **OCC asymmetry silent double-commit.** Future contributor adds OCC to iws assuming symmetry → double commit. Mitigation: `tests/contracts/test_iws_does_not_import_occ.py` enforces invariant; divergence comment in iws `run_tool_call` documents semantics.

6. **`set_pipeline(None)` test fixture preservation during `_manager.py` extraction.** Mitigation: preserve exact signature; verify with `grep -rn 'set_pipeline(None)' backend/` returns same hits pre/post extraction.

7. **Exit + tool_call race after lock removal.** Primary protection via `_map_lock` removing handle from `_by_agent` before any lock acquired (`_lifecycle.py:178-185`). In-flight calls that already obtained the handle reference surface I/O errors as tool failures during teardown; agent already called exit so failure is expected. Regression test `test_iws_exit_during_inflight_call.py` pins the contract.

8. **`runtime_bundle.py:217` string-literal silent boot break** during `shell_contract.py` relocation. Mitigation: C3 acceptance criterion includes worker cold-boot CI test post-relocate.

9. **LeaseGuard extraction breaks eph's second writer at `pipeline.py:297-299`.** Mitigation: C2.5 explicitly routes `_release_lease` through `LeaseGuard.mark_released()`; acceptance criterion greps for `_released_lease_ids` outside `lease_guard.py` importers (must be 0).

10. **Protocol unification breaks iws's per-call `layer_stack_root` arg.** Mitigation: C3.5b changes iws bootstrap — `_ensure_manager` (in `_manager.py` post-C3) calls `workspace_server.get_layer_stack_manager(root)` ONCE at pipeline construction; stores bound `LayerStack` on pipeline; drops per-call arg from all snapshot/release call sites.

---

## 8. Acceptance criteria (mechanically verifiable)

| Commit | Criterion | Verification command |
|---|---|---|
| C0 | Baselines committed at cited paths | `test -f tests/baselines/iws_serial_call_timing.json && test -f tests/baselines/iws_freeze_syscalls.strace` |
| C1 | `freezer_degraded` removed from production code | `grep -rn freezer_degraded backend/src/ \| grep -v __pycache__ \| wc -l` returns 0; status RPC shape no longer has the field |
| C2 | Parallel test green; serialization regression test green | pytest `tests/concurrency/test_iws_parallel_tool_calls.py tests/concurrency/test_iws_exit_during_inflight_call.py` |
| C2.5 | Lease guard fully extracted; both writers route through shared module | `grep -rn '_lock_for\|_destroy_with_lease_guard' backend/src/sandbox/{ephemeral,isolated}_workspace/` returns 0; `grep -rn '_released_lease_ids' backend/src/sandbox/{ephemeral,isolated}_workspace/` returns 0 (only `_shared/lease_guard.py` should write) |
| C3 | Both `_manager.py` exist; `_shared/shell_contract.py` exists; `runtime_bundle.py:217` updated; worker cold-boot CI green; OCC contract test green | `test -f backend/src/sandbox/{ephemeral,isolated}_workspace/_manager.py && test -f backend/src/sandbox/_shared/shell_contract.py && grep -q 'shell_contract' backend/src/sandbox/host/runtime_bundle.py` |
| C3.5a | `api.release_lease` registered + `api.release_workspace_snapshot` alias active with deprecation log | grep dispatcher for both registrations; pytest the alias deprecation warning test |
| C3.5b | Single `LayerStackPort` in `_shared/`; three old Protocols deleted; iws bootstrap binds at construction | `grep -rn 'class OverlayLayerStackClient\|class WorkspaceLeaseClient' backend/src/` returns 0; `grep -rn 'class LayerStackPort' backend/src/` returns 1 (in `_shared/layer_stack_port.py`) |
| C3.8 | Static export-surface test green; iws exports ≤4 symbols (incl. `AuditSink`); eph exports ≤2 | pytest `tests/static/test_workspace_export_surface.py` |
| C3.9 | `helper/` folders exist with renamed files; top-level no longer contains `_*.py`; all old import paths gone; all tests pass | `test -d backend/src/sandbox/{ephemeral,isolated}_workspace/helper && find backend/src/sandbox/{ephemeral,isolated}_workspace -maxdepth 1 -name '_*.py' \| wc -l` returns 0; `grep -rn 'from sandbox\.\(ephemeral\|isolated\)_workspace\._' backend/` returns 0; full pytest exit 0 |
| C4 | All deletions complete; both import greps return 0; RPC envelope test green | `find backend/src/sandbox/daemon/handler -name '*.py' \| wc -l` returns 0; `test ! -e backend/src/sandbox/isolated_workspace/handlers.py`; `test ! -d backend/src/sandbox/isolated_workspace/lifecycle`; pytest `tests/contracts/test_iws_rpc_envelopes.py` |

---

## 9. ADR

**Decision**: Remove dead defense (`freeze`, `freezer_degraded`, `handle.lock`); enable parallel tool calls in isolated workspaces; extract truly duplicated machinery (`LeaseGuard`, `LayerStackPort`) to `_shared/`; align `__init__.py` export surfaces; defer handler-shim collapse to a separable PR; explicitly flag the dual-mode `EphemeralPipeline` confusion for Phase 2.7. Keep the two audit patterns (`event_bus` vs JSONL) because they serve different purposes (runtime control flow vs lifecycle audit). Keep iws's 4-mixin lifecycle decomposition because the split is by phase, not arbitrary.

**Drivers**:
1. iws calls serialize unnecessarily; cgroup quotas already provide the resource boundary.
2. `freezer_degraded` field had zero production consumers (grep-verified).
3. Real duplications (`_lock_for` byte-identical across two files; three parallel Protocols; export-surface asymmetry) compound cognitive load.
4. iws-specific machinery for non-iws-specific concerns (telemetry, settings) is anti-symmetry and propagates the cargo-cult anti-pattern.
5. Bundling rewrites without shared rollback boundary amplifies blast radius.

**Alternatives considered**: α (FreezeGate — preserves dead defense), β (symmetry-only — punts headline), γ (V1 single-plan — too big), δ (V4 with audit-unify + mixin-collapse — misreads non-redundancy as redundancy). ε (V4-pruned) chosen.

**Consequences**:
- iws agents issue parallel tool calls in one session — matches eph concurrency model.
- ~275 lines of duplicated/dead code removed across 8 commits.
- `freezer_degraded` removed; observable wire shape changes (extra-field tolerance handles rehydrate).
- `api.release_workspace_snapshot` deprecated in favor of `api.release_lease`; alias remains during one release cycle.
- Single source of truth for lease-destroy race (`_shared/lease_guard.py`).
- Single source of truth for layer-stack Protocol (`_shared/layer_stack_port.py`).
- Audit/event pattern divergence between modes is now DOCUMENTED, not hidden — divergence comments on both pipeline class docstrings.
- iws `__init__.py` export surface shrinks from 14 to 4 symbols; static lint pins the contract.
- 14 files deleted in C4 (11 daemon handler shims + iws `handlers.py` + 3 `lifecycle/` files); 2 added (`_shared/lease_guard.py`, `_shared/layer_stack_port.py`); 1 moved (`shell_contract.py`).
- Each workspace folder gets a `helper/` subfolder (C3.9): top-level holds only public surface (`__init__.py`, `pipeline.py`, public subsystems); 5 eph privates + 7 iws privates relocated. Public/private boundary visible at directory level, not just by leading-underscore convention.
- **`EphemeralPipeline` dual-mode coexistence (session-mounted vs per-tool-call) UNTOUCHED.** Explicitly flagged for Phase 2.7. See §10.

**Reversibility**: Each commit is independently revert-safe via `git revert <sha>`. The riskiest is C3.5b (Protocol collapse + iws bootstrap rebind). C3.5a (API alias) provides the rollout-window safety net for the wire-protocol rename; if any downstream caller breaks, alias-only revert is sufficient.

---

## 10. What this phase does NOT touch (out of scope)

### 10.A — Deferred to Phase 2.7

**`EphemeralPipeline` dual-mode coexistence.** Architect-identified: `EphemeralPipeline` conflates two modes on one class with no shared state across a single instance lifecycle:

- **Mode A (session-mounted)**: `start()` / `stop()` / `ensure_current()` / `_foreign_watch_task` / `_active_lease_id` / `_mounted` / `_upperdir` / `_workdir` at `ephemeral_workspace/pipeline.py:172-256`. Used by `daemon/plugin` ops via `get_sandbox_overlay(start=True)` for the persistent-mount workspace path.
- **Mode B (per-tool-call)**: `run_tool_call()` / `acquire_operation_overlay()` / `release_operation_overlay()` at `ephemeral_workspace/pipeline.py:125-156` + `_operation.py:69-125`. Used by `daemon/dispatch.run_tool_handler`.

Verification commands:
```bash
grep -n "def run_tool_call\|def start\|def stop\|def ensure_current" backend/src/sandbox/ephemeral_workspace/pipeline.py
grep -n "def acquire_operation_overlay\|def release_operation_overlay" backend/src/sandbox/ephemeral_workspace/_operation.py
```

**Why deferred**: splitting this is a refactor of comparable size to Phase 2.6 itself; bundling would double scope. Phase 2.7 (TBD) will either split into separate classes (`EphemeralPipeline` for Mode B + `PersistentMountPipeline` for Mode A) or factor Mode A into a thin façade that delegates Mode B operations. This is the bigger source of confusion than R1-R6 combined.

### 10.B — Genuinely out of scope (preserved)

- **Overlay primitives** (`overlay/{handle,lifecycle,namespace_runner,namespace_entrypoint,kernel_mount,...}.py`) — Phase 2.5 made `namespace_runner.py` cancellation-aware; nothing changes here.
- **OCC layer-stack maintenance** (`occ/maintenance.py` squash policy) — unchanged.
- **`tool_primitives/{read,write,edit,grep,glob,file_ops,capture}.py`** — unchanged.
- **iws-side network policy** (`network.py`) — unchanged; see follow-up #1 for parallel-safety audit.
- **Plugin runtime** (`ephemeral_workspace/plugin/`) — unchanged; iws still blocks plugins entirely.
- **Phase 2.5 background-lifecycle wire surface** (`api.v1.{shell,cancel,heartbeat,inflight_count}`, `InFlightRegistry`, `BackgroundTaskManager.{cancel_by_agent,count_by_agent}`) — unchanged.

### 10.C — Explicit non-goals (won't-do)

- **Adding `EOS_ISOLATED_WORKSPACE_FREEZER` flag back** — the right rollout was "delete," not "feature-flag." If a deployment needs freeze for a concrete threat model, surface the threat and add freeze back as a workspace-wide concern, not an iws hidden knob.
- **Forcing iws `run_tool_call` body to match eph length** — explicitly forbidden by P1. Future contributor who "fixes" iws to mirror eph length is regressing the principle.
- **`iws_concurrent_calls_max` gauge** — user-rejected mode-specific telemetry. If observability surfaces a real need, the right shape is `workspace_concurrent_calls_max{mode="ephemeral|isolated"}` written by both pipelines, NOT iws-specific.
- **Audit unification** (`event_bus` ↔ `_JsonlAuditSink`) — two patterns serve two purposes (runtime control flow vs lifecycle audit); 20+ tier-3 tests parse exact JSONL event-type strings. Divergence is documented via class-docstring comments, not hidden.
- **Mixin collapse** of iws's `_gc.py` / `_ttl.py` / `_quota.py` into `_lifecycle.py` — the 4-way split decomposes by lifecycle phase, not arbitrarily. Collapsing produces a ~600-line god class. The user's "remove confusion" directive cuts the other way for focused phase-mixins.

---

## 11. Follow-ups

Ordered roughly by likelihood of becoming load-bearing.

### Near-term (likely worth doing within the milestone Phase 2.6 lands in)

1. **`network.py` parallel-safety audit** — Phase 2.6 C2 unlocks within-iws parallelism. Verify concurrent DNS lookups, veth state reads, and IP-pool dispenser are thread-safe inside one iws. Specifically check `IsolatedNetwork.reachable_rfc1918_subnets()` and any caching in `network.py`. Expected work: 30-min code-read + targeted concurrent test.

2. **Codify "observable contract needs observers" rule** in `CLAUDE.md` or project guidelines. Pattern from this session: `_freeze_gate` → `freezer_degraded` → `iws_concurrent_calls_max` → `handle.lock` retention — all rejected for the same reason. One-paragraph rule: *"Before claiming a field/flag/gauge is an observable contract, grep for consumers that branch on it. No branches → no contract → safe to remove."*

3. **Codify "remove attached machinery" rule (P4)** in `CLAUDE.md`. Companion to #2: *"When you remove a thing, check what was attached to it. If the attachment's only purpose was to interact with the removed thing, remove the attachment too."*

4. **Symmetry-or-shared lint for new env vars / metrics** — CI check flagging new `EOS_ISOLATED_WORKSPACE_*` env vars and `iws_*` metric names. Either accept with inline justification (intrinsic to iws), or convert to workspace-wide. Prevents drift back into mode-specific machinery.

### Medium-term (revisit after Phase 2.6 stabilizes)

5. **Phase 2.7 — split `EphemeralPipeline` dual-mode coexistence.** See §10.A. The bigger confusion than R1-R6 combined; deferred only because bundling doubles scope.

6. **Audit-path symmetry between eph and iws** — `EOS_ISOLATED_WORKSPACE_AUDIT_PATH` is iws-specific today because eph emits via `event_bus` not JSONL. If eph ever needs its own audit-path env var, rename both to `EOS_WORKSPACE_AUDIT_PATH_{ISOLATED,EPHEMERAL}` or unify under one path. Don't preempt; trigger when eph asks.

7. **`WorkspacePipeline` protocol enforcement** — promote `_shared/workspace_pipeline.py::WorkspacePipeline` to be the formal contract both pipelines implement. Add runtime conformance check at pipeline instantiation. Would catch silent API drift between modes.

8. **Reconsider in-flight tool-call drain semantics** — Phase 2.6 accepts the exit-vs-in-flight race because the agent already chose to exit. If a stricter contract emerges (e.g., "exit must not return until all in-flight calls have observed cancellation"), the right fix is a refcount of in-flight requests per iws + an `await drain` step in `exit`, NOT re-introducing per-call `handle.lock`. Defer until a real consumer asks.

### Long-term / speculative (don't do unless a real consumer asks)

9. **`workspace_concurrent_calls_max{mode="ephemeral|isolated"}` gauge** — only add when a real consumer (oncall, capacity planner, agent UX) asks. Until then, existing per-call timing metrics suffice. Must be workspace-wide, never iws-specific.

10. **Multi-engine-per-sandbox support** — today's `enter_isolated_workspace` Q4 gate already defends via `api.v1.inflight_count` (Phase 2.5 §13). When deployment genuinely needs multi-engine, the wire surface is ready; only the merge logic needs revisiting.

11. **Plugin support in iws** — currently blocked entirely. If a future use case wants plugins-in-iws, the plugin runtime would need an iws-aware execution path (no fresh `unshare`, use the persistent ns). Not urgent.

12. **Remove `api.release_workspace_snapshot` alias** added in C3.5a — schedule for one release cycle after Phase 2.6 ships. Flip the dispatcher to register only `api.release_lease`; remove the WARN log code path. Tracking issue: TBD.

13. **Move `AuditSink` Protocol to `_shared/`** if any non-iws audit consumer materializes. Today iws is the sole producer; keeping it in `isolated_workspace/_types.py` is honest.

### Won't-do (explicit non-goals)

- **Adding `EOS_ISOLATED_WORKSPACE_FREEZER` flag** — see §10.C.
- **Forcing iws `run_tool_call` body length parity with eph** — see §10.C.
- **Audit-pattern unification** (`event_bus` ↔ `_JsonlAuditSink`) — see §10.C.
- **iws mixin collapse** (`_gc.py` + `_ttl.py` + `_quota.py` → `_lifecycle.py`) — see §10.C.

---

## 12. Consensus arc (this phase's RALPLAN-DR provenance)

Phase 2.6 was authored through 4 ralplan iterations in one session:

- **V1**: drop freeze + lock + collapse handlers + adapter.py + wiring.py in one plan. *Rejected*: blast radius + "handlers wearing a hat."
- **V2**: split commits; preserve `freezer_degraded` as observable contract; refcount `FreezeGate`. *Rejected*: cargo-cult preservation (user pushback on both).
- **V3.x**: drop `_freeze_gate`; drop `freezer_degraded` (grep-verified zero consumers); enumerate lock topology; drop `handle.lock` entirely (both writers); add `_manager.py` + `shell_contract.py` relocation + divergence comments + OCC contract test. *Approved by Critic*.
- **V4**: V3.x plus C3.6 (audit unification) + C3.7 (mixin collapse) + C3.5 (Protocol unify). *Pruned by architect*: C3.6 misreads `event_bus` as audit; C3.7 creates god class.
- **V4-pruned** (this document): V3.x + C2.5 + C3.5a + C3.5b + C3.8 + §10 dual-mode flag + divergence-comment rescue from C3.6. *Approved by Critic with 3 MAJOR fixes addressed.*

Authoring agents: ralplan consensus loop with `oh-my-claudecode:architect` and `oh-my-claudecode:critic` subagents. All architect/critic feedback grep-verified against actual code before incorporation.
