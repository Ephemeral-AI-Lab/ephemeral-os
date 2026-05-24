# Unify Sandbox Workspace API — Overview

**Status:** Canonical 3-phase plan. Each phase has its own document.
**Date:** 2026-05-24

This document is the **overview**. The detailed plan lives in four phase documents:

1. [`unify_sandbox_workspace_phase1.md`](unify_sandbox_workspace_phase1.md) — **Foundation** (folder reorg + overlay extraction + shared primitives; mechanical, no behavior change)
2. [`unify_sandbox_workspace_phase2.md`](unify_sandbox_workspace_phase2.md) — **Unification (foreground-only)** — per-call ephemeral pipeline, persistent isolated pipeline, lifecycle host API, agent tools. Background lifecycle is out of scope here; Phase 2.5 owns it.
3. [`unify_sandbox_workspace_phase2_5.md`](unify_sandbox_workspace_phase2_5.md) — **Background Tool Lifecycle** — canonical background design. Background is a generic `ToolCallRequest.background` flag; engine's `BackgroundTaskManager` is the lifecycle wrapper; overlay lease lifetime is coroutine-bound; generic `api.v1.cancel(invocation_id)` wire RPC; verb-supplied cancellation cleanup in `overlay.run_in_namespace`. Removes existing `shell_job.py` (609 lines), `shell_job_handler.py` (174 lines), four `api.v1.shell.*` wire RPCs, and the `is_background` shell-tool branch from the repo.
4. [`unify_sandbox_workspace_phase3.md`](unify_sandbox_workspace_phase3.md) — **Test migration & documentation**

Verb renames (`search_content` → `grep`, `glob_files` → `glob`) shipped previously and are not part of this plan. Daytona provider support is out of scope (Docker-only deployment).

---

## 1. The three workspaces

| Concept | Role | Writeback | Overlay lifecycle | Lifecycle methods agents see |
|---|---|---|---|---|
| **`main_workspace`** | Persistent identity (base repo + LayerStack snapshots). Single source of truth. | OCC-only; no direct writes. | n/a — read via layer-stack primitives. | n/a |
| **`ephemeral_workspace`** | Per-tool-call execution context. Foreground calls awaited by the caller; background calls wrapped as `asyncio.Task` by the engine's `BackgroundTaskManager` (Phase 2.5). | OCC merge into `main_workspace` at end of every WRITE_ALLOWED call. | **Coroutine-bound.** Overlay handle is acquired inside `pipeline.run_tool_call` and released in its `finally`. Foreground = wall-time of the call; background = wall-time of the asyncio.Task wrapping the same coroutine. No separate shell-job abstraction, no per-job pipeline registry. | `run_tool_call` only (1 method, branch-free body — does NOT inspect `req.background`). |
| **`isolated_workspace`** | Per `enter → exit` execution context inside a `{user,mnt,pid,net}` namespace stack. | **None** — upperdir discarded at `exit`. **Rationale:** iws is designed for tool-execution isolation, not persistent agent state. Persistent state belongs to `main_workspace` via OCC. Sessions needing to retain work-product write to `main_workspace` BEFORE calling exit. (Critic must-fix #14 / Planner F.6 — adds the missing rationale.) | **Created once at `enter`; reused across all tool calls in session; destroyed at `exit`.** | `enter` + `exit` + `run_tool_call` (3 methods, 2 are mode-specific) |

### Unified execution

Every tool call in both modes flows through the same kernel-overlay path. There is no in-workspace / out-of-workspace branching in handlers: the overlay's natural pass-through (workspace subtree replaced by layerstack; rest of FS untouched) handles paths like `/etc/foo` and `/tmp/scratch` uniformly. The only difference between ephemeral and isolated is **when the overlay handle is created and destroyed**.

### Pass-through semantics for non-workspace paths

(Critic must-fix #9 / Architect F.5 / Planner C.10/F.12 — SECURITY-relevant.)

| Path | Read (ephemeral) | Write (ephemeral) | Read (isolated) | Write (isolated) |
|---|---|---|---|---|
| `/testbed/*` (workspace) | overlay merge (upperdir+lowerdir) | overlay upperdir + OCC commit | overlay merge | overlay upperdir, no commit, discarded at exit |
| `/tmp/*` (non-workspace tmp) | host pass-through | overlay upperdir, filtered out at OCC capture (Phase 2 §7.5 conv filter) | host pass-through inside ns | overlay upperdir, discarded at exit |
| `/etc/`, `/var/`, `/proc/`, `/sys/`, `/boot/` (system paths) | host pass-through | **REFUSED** by namespace-child denylist (Phase 2 §7.5) — `forbidden_host_path` error before kernel call | host pass-through inside ns | **REFUSED** by denylist |

**Why the denylist is required:** today's `_write_out_of_workspace` runs as the unprivileged daemon user → kernel returns EACCES for `/etc/hosts` writes. After unification, the namespace child runs as root inside the user namespace → root-in-namespace CAN write `/etc/hosts` unless explicitly denied. The denylist closes this gap.

### Background tool policy (REVISED in Phase 2.5)

**REDACTED — superseded by [`unify_sandbox_workspace_phase2_5.md`](unify_sandbox_workspace_phase2_5.md).** The earlier `ShellJob` + four-verb (`shell_launch`/`shell_reap`/`shell_poll`/`shell_cancel`) model is replaced. Summary of the current design (details in Phase 2.5):

- **Background is a tool-call flag, not a verb.** Any tool declared `background="optional"` can be called with `background=true`; the engine's `BackgroundTaskManager` wraps `pipeline.run_tool_call(req)` as an `asyncio.Task`. The pipeline's body is unchanged between foreground and background; the only difference is who awaits the coroutine.
- **Overlay lease lifetime is coroutine-bound.** Acquired in `pipeline.run_tool_call`, released in its `finally`. There is no shell-job abstraction and no pipeline-owned background registry.
- **Cancellation** flows via local `asyncio.Task.cancel()` + generic `api.v1.cancel(invocation_id)` wire RPC. The wire envelope carries `invocation_id` for correlation. Daemon-side `InFlightInvocationRegistry` maps `invocation_id → asyncio.Task`; `api.v1.cancel` cancels the task; the pipeline's `try/finally` releases the lease and destroys the overlay.
- **Ephemeral OCC at completion** follows the same source-tag rule as foreground writes (`"api_write"` for single-path typed writes; `"overlay_capture"` for multi-path shell). Cancelled coroutines do NOT commit (the commit branch is on the post-`run_in_namespace` happy path).
- **Isolated background** runs against the session overlay; no per-job overlay, no commit. Coroutine completion does not destroy the session overlay (only `exit` does).
- **Cross-mode rejection (Q4) and iws-exit drain** move to the engine layer — `BackgroundTaskManager.count_by_agent` and `cancel_by_agent`. The pipeline has no agent-scoped registry to consult; the engine is the single source of truth.
- **Terminal-status precedence (`completed > failed > cancelled > running`)** preserved at the engine layer (`engine/background/manager.py:32-38`); a shell that exits between cancel-signal and cancel-landing returns COMPLETED with the real result, not CANCELLED.
- **Orphan cleanup on engine death** preserved via `InFlightInvocationRegistry`'s TTL reaper plus batched `api.v1.heartbeat(invocation_ids=[...])` liveness refresh. New engines also consult daemon `api.v1.inflight_count(agent_id)` during Q4 checks, so stale daemon-visible work blocks isolated-workspace entry until it finishes or the TTL reaper cancels it.

See Phase 2.5 §1 for the 5 NEW principles; §3 for the RALPLAN-DR option matrix; §5 for the module-level changes; §13 for the pre-mortem (envelope migration / cancel-ordering / multi-engine split-brain).

### Agent-callable surface

- **6 tool ops** with static `Intent` metadata:
  - `Intent.READ_ONLY`: `read_file`, `grep`, `glob`
  - `Intent.WRITE_ALLOWED` (uniform shape): `write_file`, `edit_file`
  - `Intent.WRITE_ALLOWED` (shell-specific shape): `shell` — keeps its own signature for `cancel_event` + `pgrp` (cancellation cleanup is verb-supplied in Phase 2.5 §5.5). Background support is generic (Phase 2.5); shell does NOT have its own `job_id` / TTL-reap surface.
- **2 lifecycle ops**: `enter_isolated_workspace`, `exit_isolated_workspace` — switch the agent's active execution context; use `LifecycleResultBase`, different audit class (`lifecycle_operation`), publish `workspace_lifecycle_*` events.

---

## 2. Principles

1. **Workspace lifecycle is coroutine-bound** (Phase 2.5 §1 P1-revised — supersedes Phase 2's "owner-defined" framing that allowed a shell job to outlive `run_tool_call`). The `OverlayHandle` is acquired inside `pipeline.run_tool_call` and released in its `finally`. For foreground calls the caller awaits the coroutine, so lifetime equals call wall-time. For background calls the engine's `BackgroundTaskManager` wraps the coroutine as an `asyncio.Task`, so lifetime equals task wall-time. For isolated mode the iws session owns its session overlay (created at `enter`, destroyed at `exit`); background coroutines run against the session overlay and their completion does NOT destroy it. The pipeline has no agent-scoped background registry. Pipelines are named `EphemeralPipeline` / `IsolatedPipeline` — "Pipeline" connotes "orchestrates workspace-scoped execution context." We considered `Executor` / `Context` (Planner C.5); kept `Pipeline` for symmetry with the two-class story and to avoid the `concurrent.futures.Executor` overload (rename is a separate plan if revisited).

2. **Shared overlay substrate, optional capture, mode-defined cadence.** `sandbox/overlay/{create, destroy, run_in_namespace}` are always called by both pipelines. `capture_changes` is OPTIONAL — `EphemeralPipeline` always invokes it for `WRITE_ALLOWED` verbs (for OCC commit); `IsolatedPipeline` invokes it ONLY for `changed_paths` observability (no OCC commit follows, upperdir discarded at exit). `overlay.create` accepts an iws-only `network: NetworkConfig | None` parameter (ephemeral always passes `None`). The substrate is shared; the call pattern is mode-defined. (Architect §D Principle 2 reworded — earlier "same interface" wording was leaky.)

3. **`OverlayHandle` is a state-bearing handle with idempotent destroy.** It is not an immutable value object (Critic must-fix #10 / Planner F.9 / Architect §D Principle 3 — the earlier immutable-object wording contradicted the mutable `_destroyed` field). Mutability of `_destroyed: bool` is intentional and documented; thread/asyncio safety relies on the owning pipeline's `_handle_locks: dict[str, asyncio.Lock]` AND single-bit-write semantics for `_destroyed` (no torn-reads). Concurrent `destroy(handle)` calls (e.g., from shell-job reaper or interleaved asyncio tasks) must be safe — see Phase 2 §3.1's `_destroy_with_lease_guard` per-handle-lock TOCTOU fix.

4. **`WorkspacePipeline` protocol has ONE method.** `async def run_tool_call(req: ToolCallRequest) -> ToolCallResult`. Each pipeline implements its own internals. Lifecycle methods (`enter`/`exit`) live only on `IsolatedPipeline` — called by `sandbox/isolated_workspace/lifecycle/` host-side coroutines, not through the protocol. The protocol provides type-safe dispatch in `daemon/dispatch.py::resolve_pipeline(agent_id) -> WorkspacePipeline`; the lifecycle plumbing is intentionally NOT on the protocol because ephemeral has nothing analogous.

5. **Unified execution per tool call. No in/out-of-workspace branching.** All 6 verbs route through the overlay. No `classify_path`, no `_xxx_in_workspace`/`_xxx_out_of_workspace` helpers. The overlay's pass-through layer handles non-workspace paths (see the §1 pass-through table). Read-only verbs skip the capture+commit phase but still mount. **System-path writes (`/etc/`, `/var/`, `/proc/`, `/sys/`, `/boot/`) are REJECTED by a namespace-child denylist** (Phase 2 §7.5) BEFORE the kernel call — closes the root-in-namespace security gap that today's `_write_out_of_workspace` accidentally relied on (unprivileged daemon couldn't write `/etc/*` → EACCES; root-in-namespace can unless denied).

6. **Two-tier verb dispatch inside the namespace child.** Tier 1 (uniform): `read/write/edit/grep/glob` use `tool_primitives.<verb>.compute(args) -> ToolCallResult`. Tier 2 (shell-specific): `shell` uses `tool_primitives.shell.run(args, cancel_event, stdout_ref, stderr_ref) -> ShellResult`. Shell's surface is honestly different — pretending otherwise was the failure mode. **Background shells are supported in BOTH modes** with different overlay-lifetime ownership (see Principle 1 and §1 background-shell policy table).

7. **OCC commit preserves single-path coalescing via source-tag.** All 4 OCC helper sites accept a `source: str = "overlay_capture"` kwarg: `overlay_path_changes_to_occ_changes`, `build_overlay_write_change`, `build_overlay_delete_change`, inline `SymlinkChange`/`OpaqueDirChange` constructors (verified Phase 2 §6.1–§6.4; Critic must-fix #7 / Architect F.8 DISAGREE with Planner — this is a 4-site change, not 1). `EphemeralPipeline` passes `source="api_write"` for single-path typed writes (`write_file`, `edit_file` where `len({c.path for c in changes}) == 1`), preserving `CommitQueue._disjoint_batches` coalescing for concurrent disjoint writers. Multi-path writes (shell, or pathological typed write through a symlink) keep `source="overlay_capture"` for cross-path atomicity.

8. **Mandatory `O_NOFOLLOW` via `tool_primitives.file_ops.open_no_follow` chokepoint.** Namespace runs as root; symlink-following inside the overlay would resolve through the host pass-through. **The chokepoint preserves the per-component walk** (root with `O_DIRECTORY` → each intermediate segment with `O_DIRECTORY|O_NOFOLLOW|dir_fd` → final open with `flags|O_NOFOLLOW`), or uses `openat2(RESOLVE_NO_SYMLINKS)` when available. Naive last-component-only `os.open(path, flags|O_NOFOLLOW)` is FORBIDDEN by static lint (Phase 3 §4.4). This defends against intermediate-component symlink attacks (`/testbed/dir → /etc`, `read("/testbed/dir/passwd")`), not just trailing-symlink attacks. (Critic must-fix #15 / Architect F.6.)

9. **Single namespace strategy.** `copy_backed` strategy removed entirely. New mount API (fsopen/fsconfig/fsmount + private mount namespaces) is a hard precondition at sandbox startup; `scripts/verify_overlay_preconditions.py` is a deployment guard and the tombstone flag `EOS_REQUIRE_NEW_MOUNT_API=1` permits rollback during the deploy window (deleted in Phase 3). `MountMode` enum, `MaterializeLayout`, `_workspace_rewrite.py`, `ExecutionStrategy` ABC, `EOS_OVERLAY_FORCE_MATERIALIZE` env-var all deleted.

10. **`isolated_workspace` blocks plugin access.** Dispatcher-entry gate: any `api.plugin.*` or `plugin.<name>.<op>` invoked by an agent with an open iws handle returns `forbidden_in_isolated_workspace`. Fail-OPEN when pipeline not bootstrapped — accepted because the alternative (fail-CLOSED) would break every test fixture that doesn't init iws. **Fail-OPEN emits a loud audit event** (`workspace_lifecycle.plugin_check_unbootstrapped`) so the bypass is visible (Planner F.20). Threat-model: an attacker who DoSes iws bootstrap bypasses the policy; risk accepted, mitigated by audit visibility + follow-up plan for fail-CLOSED-with-explicit-bypass.

11. **O(1) lowerdir disk; upperdir is owner-defined.** Overlayfs natural sharing — lowerdir is the layer stack, shared across calls/sessions, no copy. Upperdir is transient and its lifetime tracks the owning entity: **O(parallel calls + in-flight background tasks) in ephemeral mode** (foreground calls create and destroy an upperdir at call boundaries; background calls hold the same per-call upperdir until the engine-owned asyncio task completes or is cancelled through `api.v1.cancel(invocation_id)`), **O(mutations-per-session) in isolated mode** (upperdir grows until exit, then discarded; background tasks share the session upperdir and are drained by `sandbox.isolated_workspace.lifecycle.exit_isolated_workspace`). Planner B.5 caught the earlier wording that conflated lowerdir/upperdir.

---

## 3. Three-layer architecture

```
Layer 3 — Pipelines (mode-specific orchestration)
─────────────────────────────────────────────────
  EphemeralPipeline.run_tool_call(req):
    handle = await overlay.create(layer_stack, agent_id=req.agent_id)
    try:
      result = await overlay.run_in_namespace(handle, req)
      if req.intent == Intent.WRITE_ALLOWED:
        changes = await overlay.capture_changes(handle)
        source = "api_write" if req.verb in {"write_file","edit_file"} else "overlay_capture"
        await self._occ.commit(changes, base_version=handle.snapshot_version, source=source)
      return result
    finally:
      await overlay.destroy(handle)

  IsolatedPipeline.enter(agent_id, config):
    handle = await overlay.create(layer_stack, agent_id, network=config.network)
    self._sessions[agent_id] = handle

  IsolatedPipeline.run_tool_call(req):
    handle = self._sessions[req.agent_id]
    return await overlay.run_in_namespace(handle, req)

  IsolatedPipeline.exit(agent_id):
    handle = self._sessions.pop(agent_id)
    await overlay.destroy(handle)   # discards upperdir, no commit

Layer 2 — overlay.run_in_namespace(handle, req)
─────────────────────────────────────────────────
  Fork into handle's namespace. Child process:
    - chdir handle.workspace_root
    - lazy-import tool_primitives.<verb>
    - two-tier dispatch:
        if verb == "shell":
          return tool_primitives.shell.run(args, cancel_event, ...)
        else:
          return tool_primitives.<verb>.compute(args)
  Returns ToolCallResult to host via pipe.

Layer 1 — Primitives
─────────────────────────────────────────────────
  sandbox/overlay/:
    handle.py         OverlayHandle dataclass (+ _destroyed guard)
    lifecycle.py      create / destroy / capture_changes
    namespace.py      host-side fork+unshare+wait coordinator
    namespace_child.py child entry: mount + chdir + verb dispatch
    kernel_mount.py, new_mount_api.py, capability.py, layout.py, capture.py,
    change_synthesis.py (with source parameter)

  sandbox/_shared/tool_primitives/:
    read.py, write.py, edit.py, grep.py, glob.py, shell.py, file_ops.py
    (O_NOFOLLOW unconditional in read/write/edit)
```

---

## 4. Phase summary

### Phase 1 — Foundation (mechanical refactor + class-rename shims)

Materializes the three workspace packages (`main_workspace/` as a **thin re-export facade**, not just a doc anchor), relocates the overlay subsystem (FLAT, no `strategies/` subfolder, namespace-only), extracts shared primitives, defines `OverlayHandle` + lifecycle primitives, **mechanically decomposes `manager.py` (1624 lines, verified) into 7 focused modules**, ships deployment-precondition guard + tombstone flag. **No daemon behavior change; class internals rewritten in Phase 2.** Parity corpus captured **scoped to ephemeral mode only**.

**Ships:**
- `sandbox/main_workspace/__init__.py` (5-line thin re-export facade — `LayerStack`, `prepare_workspace_snapshot`, `CommitQueue`, `WriteChange`, `DeleteChange`)
- `sandbox/ephemeral_workspace/pipeline.py` (renamed from `daemon/service/sandbox_overlay.py::SandboxOverlay → EphemeralPipeline` via `git mv`)
- `sandbox/ephemeral_workspace/plugin/` (moved from `sandbox/plugin/`)
- `sandbox/isolated_workspace/{pipeline,_types,_lifecycle,_gc,_ttl,_quota,_runtime}.py` (`manager.py` mechanical decomposition; no file >400 lines)
- `sandbox/overlay/` (FLAT — absorbs `execution/overlay/` + `execution/strategies/namespace*`; deletes `copy_backed.py`, `base.py`, `_workspace_rewrite.py`)
- `sandbox/overlay/handle.py` (NEW — `OverlayHandle` dataclass with `_destroyed` field; documented as a mutable state-bearing handle; `namespace_pid` lifecycle documented)
- `sandbox/overlay/lifecycle.py` (NEW — `create`, `destroy`, `capture_changes`)
- `sandbox/_shared/tool_primitives/` (NEW — verb compute impls; `file_ops.open_no_follow` chokepoint with per-component walk OR `openat2(RESOLVE_NO_SYMLINKS)`)
- Relocate non-overlay `execution/*` files into `ephemeral_workspace/` and `_shared/`
- `scripts/verify_overlay_preconditions.py` (deployment guard)
- `docs/sandbox/deployment_targets.md` (pre-rollout audit)
- `EOS_REQUIRE_NEW_MOUNT_API=1` tombstone flag (deleted in Phase 3 §6C.4)
- Parity corpus at `tests/mock/sandbox/_fixtures/tool_primitives_parity_corpus.json` — **EPHEMERAL MODE ONLY** (iws is functional-upgrade per Phase 3 `behavior_upgrade/` tier)

**Cost:** ~300 atomic import updates across `backend/`. No production behavior change. ≤10 logical atomic commits with `git revert <sha>` rollback per commit.

→ Details: [`unify_sandbox_workspace_phase1.md`](unify_sandbox_workspace_phase1.md)

### Phase 2 — Unification (substantive)

Implements the per-call ephemeral pipeline and persistent isolated pipeline. Adds the unified tool-op dispatch and the lifecycle host API + agent-callable tools. Deletes the iws tool-op surface and the in/out-of-workspace branching. **Includes TOCTOU fix, host-path denylist, background tool policy, OCC 4-helper source-tag threading.**

**Ships:**
- `ToolCallRequest` / `ToolCallResult` / `Intent` enum / `LifecycleResultBase` in `sandbox/_shared/models.py`
- `WorkspacePipeline` protocol (one method)
- `EphemeralPipeline.run_tool_call` with full per-call lifecycle inline; **per-handle `asyncio.Lock` in `_destroy_with_lease_guard` (TOCTOU fix — Critic must-fix #5)**
- ~~`EphemeralPipeline.{launch,reap,poll,cancel}_background_job`~~ **REMOVED in Phase 2.5.** Background lifecycle moved to engine's `BackgroundTaskManager`; pipeline body has no background-specific methods or registry.
- ~~`EphemeralPipeline.startup_gc()`~~ **REMOVED in Phase 2.5.** Replaced by daemon-side `InFlightInvocationRegistry`'s TTL reaper + `api.v1.heartbeat` engine-process-id liveness signal.
- `IsolatedPipeline.run_tool_call` + `enter` + `exit`. In Phase 2.5: the `ephemeral_jobs_in_flight` Q4 check and `exit` drain move to engine layer (`BackgroundTaskManager.count_by_agent` / `cancel_by_agent`); pipeline.enter / pipeline.exit are pure teardown.
- `overlay.run_in_namespace` (host) + `namespace_child.py` two-tier dispatcher; **host-path denylist** (`/etc/`, `/var/`, `/proc/`, `/sys/`, `/boot/` rejected for WRITE-allowed verbs)
- **OCC source-tag threaded through 4 helper sites** (`overlay_path_changes_to_occ_changes`, `build_overlay_write_change`, `build_overlay_delete_change`, inline `SymlinkChange`/`OpaqueDirChange` constructors); single-path determination uses `len({c.path for c in changes}) == 1`
- `OverlayHandle._destroyed` guard + per-pipeline `_handle_locks: dict[str, asyncio.Lock]`
- `tool_primitives.file_ops.open_no_follow` chokepoint enforced by static lint (no naive `os.open(path, flags|O_NOFOLLOW)` bypass)
- Thin daemon handlers (~15 lines each)
- `sandbox/daemon/dispatch.py::resolve_pipeline(agent_id)`
- DELETE `daemon/request_context.py::classify_path` + `_xxx_in_workspace`/`_xxx_out_of_workspace` helpers
- **`sandbox/isolated_workspace/lifecycle/` host-side coroutines** (isolated-workspace-owned package — replaces the rejected `sandbox/api/lifecycle/` naming per Critic must-fix #6; `sandbox/api/` stays client-side only)
- `sandbox/audit/lifecycle.py` + `WorkspaceLifecycle` event class
- Plugin-block gate emits `workspace_lifecycle.plugin_check_unbootstrapped` audit event on fail-OPEN
- `backend/src/tools/isolated_workspace/{enter,exit}_isolated_workspace/` (imports from `sandbox.isolated_workspace.lifecycle.*`)
- **`WorkspaceSession` DEFERRED to `tests/mock/sandbox/_fixtures/workspace_session.py`** test utility — NOT shipped as public API until a production caller materializes (Critic must-fix #11)
- DELETE `isolated_workspace/ops_handlers.py` (98 lines of shell-out wrappers — verified) + 5 iws tool-op RPCs. **`isolated_workspace/handlers.py` (lifecycle helpers) PRESERVED.** No separate isolated-workspace RPC module exists or has ever existed (phantom reference removed from docs per Critic must-fix #1).

**Cost:** substantive — bounded by Phase 1's parity corpus (ephemeral-only) and Phase 3's `behavior_upgrade/` tier (iws). ≤8 logical atomic commits.

**iws verb migration is a FUNCTIONAL UPGRADE, not a refactor.** Today's `ops_handlers.py` shells out to `/bin/cat`/`/usr/bin/grep -r -n`/`in_ns_write.py` returning `subprocess.run` shape; after Phase 2, iws verbs return typed `ReadResult`/`WriteResult`/`EditResult` (real search/replace, not full-body overwrite)/`GrepResult` (honors `mode`, `case_insensitive`, `include_pattern`, `multiline`)/`GlobResult` shapes.

→ Details: [`unify_sandbox_workspace_phase2.md`](unify_sandbox_workspace_phase2.md)

### Phase 3 — Test migration & documentation

Reshapes the iws test suite around the new tool surface. Adds new test tiers for unified per-call lifecycle, OCC coalescing preservation, O_NOFOLLOW security (per-component walk + intermediate-symlink), plugin policy + fail-OPEN audit event, host-path denylist, iws behavior upgrade, unit-level coverage, deployment pre-flight CI, observability assertions. Validates Tier 8 soak with perf escalation threshold.

**Ships:**
- Updated happy_path tests using agent-level lifecycle tools (imports `sandbox.isolated_workspace.lifecycle.*`, NOT `sandbox.api.*`)
- `tool_wrappers/` tier (tool dispatch + lifecycle round-trip)
- `policy/` tier (destructive pre-hook, plugin-block, plugin fail-OPEN audit event, host-path denylist for `/etc/`/`/var/`/`/proc/`/`/sys/`/`/boot/`)
- `security/` tier (O_NOFOLLOW symlink-escape — trailing-component AND intermediate-component; static AST lint enforces chokepoint)
- `pipeline_lifecycle/` tier (ephemeral upperdir GC after each call; isolated upperdir persists across calls; **iws upperdir scales with mutations**; lowerdir O(1))
- `concurrency/` tier (OCC source-tag coalescing on all 4 helper sites; `_wire_handle` ordering invariant; **destroy-under-asyncio-interleaving**; **background tool lifetime — engine launches return `bg_*` task ids, the same `run_tool_call` coroutine survives as an asyncio task, completion commits, cancellation discards, iws drains at exit, and enter rejects when sandbox-bound background tasks are in flight**; **10-step interleaved E2E**)
- **`behavior_upgrade/` tier** (NEW — iws verb migration validation: typed-shape `ReadResult`/`WriteResult`/`EditResult` (real search/replace)/`GrepResult` (modes+options honored)/`GlobResult`/shell `changed_paths`)
- **`unit/` tier** (NEW — per-module coverage for `OverlayHandle`, `overlay/lifecycle`, `overlay/namespace`, `namespace_child`, `tool_primitives/file_ops`, `overlay_change_conversion`, pipeline lease accounting, `LifecycleError` enumeration, `resolve_pipeline`)
- **`observability/` tier** (NEW — `timings["mount_ms"]` populated, iws upperdir mid-session gauge, audit-event payload shape stability)
- **Deployment pre-flight CI** running `scripts/verify_overlay_preconditions.py` (`EOS_REQUIRE_NEW_MOUNT_API` tested + deleted at the end of Phase 3)
- Tier 8 soak baseline reshape (per-call mount cost in baseline) **+ perf escalation threshold** (read p50 > 200ms or p99 > 500ms auto-files follow-up issue)
- `docs/sandbox/api_surface.md` — 11 sections including pass-through table, background tool policy, `WorkspaceSession` deferral note, namespace-child-boundary diagram, `open_no_follow` per-component-walk explanation, deployment-precondition reference
- Updated blast-radius doc (reflects `sandbox/isolated_workspace/lifecycle/` + extracted `manager.py` modules), PLAN.md (9 tiers), CHANGELOG

**Atomic commit plan:** ≤5 logical atomic commits.

→ Details: [`unify_sandbox_workspace_phase3.md`](unify_sandbox_workspace_phase3.md)

---

## 5. Out of scope

- Daytona provider support — `sandbox/provider/daytona/` is preserved as-is but unmaintained; follow-up plan deletes (Planner F.15 — not glossed as "out of scope" anymore).
- Daemon wire-protocol versioning. `api.v1.<verb>` survives; no `api.v2.*`.
- OCC writeback for iws (intentional design feature; rationale documented in §1 isolated_workspace row).
- Network-policy API for iws (separate plan).
- iws lifecycle RPC handlers survive UNCHANGED in `sandbox/isolated_workspace/handlers.py` (no separate isolated-workspace RPC module exists — phantom reference removed per Critic must-fix #1). Lifecycle RPC namespace `api.isolated_workspace.{enter,exit,status,list_open,test_reset}` preserved.
- Mypy-level Union narrowing on `ToolCallResult` types.
- Moving `layer_stack/` or `occ/` into `main_workspace/` (500+ external imports; sidestepped via thin re-export facade in `main_workspace/__init__.py`).
- `WorkspaceSession` as public API — deferred to `tests/mock/sandbox/_fixtures/workspace_session.py` test utility until a production caller materializes (Critic must-fix #11).
- Backward-compatible iws result-shape preservation. iws verbs adopt the typed-verb spec; old `subprocess.run`-style result fields (`stdout`, `stderr`, `exit_code`, `duration_s`) are REMOVED.
- Renaming `Pipeline` → `Executor` / `Context` (Planner C.5 alternatives considered; deferred to a follow-up rename plan if revisited).
- Renaming `_shared/` → `common/` (Planner C.4; cosmetic; deferred).

---

## 6. ADR (architecture decision record)

**Decision:** Refactor sandbox around three named workspace concepts (`main_workspace`, `ephemeral_workspace`, `isolated_workspace`) as sibling packages. Unify execution: all 6 tool ops route through a per-call overlay in ephemeral mode and a persistent overlay in isolated mode. Drop the `copy_backed` strategy entirely; namespace strategy is the only execution path. Add agent-callable lifecycle tools. Relocate plugin subtree under `ephemeral_workspace` (ephemeral-only). Extract overlay as OCC-unaware filesystem substrate at `sandbox/overlay/` (flat, no `strategies/` subfolder). Pipeline class renames for symmetry: `SandboxOverlay → EphemeralPipeline`, `IsolatedWorkspaceManager → IsolatedPipeline`. Keep `workspace_root` field naming.

**Drivers:**
1. Match user-defined semantics literally: ephemeral = per-call workspace; isolated = persistent through lifecycle.
2. Cleanest readable code — each pipeline body fits on one screen; lifecycle is inline; no scattered `_branch.py` indirection.
3. Architectural clarity via the three-workspace mental model + explicit separation of tool ops / lifecycle ops / filesystem substrate.
4. O(1) lowerdir disk via overlayfs natural sharing.

**Alternatives considered:**
- **Option X — Refactor surface only.** Keep today's asymmetric execution semantics (typed verbs OCC-direct against snapshot; shell uses overlay). Rejected: leaves the in/out-of-workspace branching and per-verb asymmetry the user explicitly wanted gone.
- **Option Y — Two pipelines + verb-level asymmetry inside pipelines.** Pipeline is mode-uniform; inside `EphemeralPipeline.run_tool_call`, branch on `Intent` (read-only → manifest-direct via `layer_stack.read_text`; write-allowed → overlay). **Rejected for these specific reasons** (Critic must-fix #13 / Architect Section A steelman — earlier "user said so" rationale was a partial abdication):
  - **Principle violation (P5):** Option Y reintroduces verb-level branching INSIDE the pipeline (the `Intent.READ_ONLY → manifest-direct; Intent.WRITE_ALLOWED → overlay` switch). Principle 5 explicitly forbids that branching as the user-quality goal — "no in/out-of-workspace branching" extends to "no read/write branching in the execution path."
  - **Honest cost acceptance:** Option Z accepts ~50–200ms per-call mount overhead on read/grep/glob (Phase 3 §7.3 measured: read ~5ms → ~50–150ms; write/edit ~10ms → ~60–180ms). At LLM agent cadence (dozens of reads per response), this moves median latency materially. We accept this as the price of code surface simplicity (each pipeline body = one screen).
  - **Falsifiable safeguard:** Phase 3 §7.4 escalation threshold (read p50 > 200ms or p99 > 500ms auto-files a follow-up issue to revisit Option Y). Without this threshold, "accepted per user judgment" would be unfalsifiable. With it, if production tracing shows reads dominating the cost profile, we revisit in a follow-up plan.
  - **User judgment cited as tiebreaker, not sole rationale.** The principle violation is the primary reason; the cost acceptance is the second; user judgment breaks ties on the perf budget when the principles point one way and the perf number points the other.
- **Option Z — Full unification (CHOSEN).** All 6 verbs go through overlay in both modes. Accepts ~50–200 ms per-call ephemeral mount overhead (with the §7.4 escalation threshold as a fail-safe). Preserves OCC coalescing via source-tag round-trip (4 helper sites). Shell verb keeps its own signature (two-tier dispatch).
- **Persistent ephemeral overlay** (mount-per-agent, not per-call). Rejected: contradicts user's literal "ephemeral = per tool call" definition.
- **`acquire_handle/release_handle` symmetric protocol.** Rejected: forces `IsolatedPipeline` to fake an acquire step it doesn't have; misleading abstraction.
- **Dual RPC namespace for tool ops.** Rejected: dispatcher duplication.
- **`workspace` parameter in request payload.** Rejected: per-call boilerplate, mismatch-prone.
- **Field-zeroing iws results.** Rejected: incoherent; iws files genuinely change.
- **Renaming `workspace_root` to `mount_point`.** Rejected: preserves existing user-friendly naming.
- **Moving `layer_stack/` or `occ/` into `main_workspace/`.** Rejected: 500+ import churn for zero behavior benefit. Resolved instead via thin re-export facade in `main_workspace/__init__.py`.
- **Inserting host-side coroutines into `sandbox/api/`.** Rejected: `sandbox/api/` already houses client-side wire artifacts; same-package opposite-sides-of-wire is confusing. Resolved instead via `sandbox/isolated_workspace/lifecycle/` (Critic must-fix #6).
- **Naive `os.open(path, flags|O_NOFOLLOW)` as the file_ops chokepoint.** Rejected: silently weakens to last-component-only; intermediate-symlink attacks bypass. Resolved instead via per-component walk OR `openat2(RESOLVE_NO_SYMLINKS)` (Architect F.6 / Critic must-fix #15).
- **Fail-CLOSED plugin block by default.** Rejected: breaks every test fixture that doesn't init iws. Resolved instead via audit-loud fail-OPEN (Planner A.3.5 Option γ); follow-up plan spawns fail-CLOSED-with-explicit-bypass variant.
- **`WorkspaceSession` shipped as public API.** Rejected: no production caller documented; would create a maintenance burden + a public surface with no user (Critic must-fix #11). Resolved instead by demoting to test fixture; promote to public API only when a caller materializes.
- **Background shells modeled as a daemon-side `ShellJob` with four `api.v1.shell.{launch,reap,poll,cancel}` verbs.** ~~Originally accepted in Phase 2~~ **REVERSED in Phase 2.5.** Two parallel background-task registries (engine `BackgroundTaskManager` + daemon `ShellJobRegistry`) for the same lifecycle was the root smell; the user directive ("background is a tool-call concept, not a shell concept") forced the issue. Phase 2.5 deletes the daemon-side ShellJob abstraction, makes background a generic `ToolCallRequest.background` flag wrapped by the engine, and binds overlay lease lifetime to the asyncio coroutine. Q4 + iws-exit drain move to engine layer. See [`unify_sandbox_workspace_phase2_5.md`](unify_sandbox_workspace_phase2_5.md) §3 for the full α/β/γ option matrix.

**Consequences:**
- Phase 1: large mechanical PR; ~300 atomic import updates; `daemon/service/` empties out; `sandbox/overlay/` exists flat; `sandbox/_shared/tool_primitives/` exists; `sandbox/execution/` deleted; `manager.py` mechanically decomposed into 7 modules; thin `main_workspace/` facade lands; deployment guard + tombstone flag ship; parity corpus (ephemeral-only) committed. ≤10 atomic commits.
- Phase 2: substantive PR; new audit class; new agent-level tools; iws tool-op RPCs deleted atomically; thin daemon handlers; OCC source-tag plumbed through 4 helpers; OverlayHandle idempotency wired with per-handle `asyncio.Lock` TOCTOU fix; `tool_primitives.file_ops.open_no_follow` chokepoint enforced; host-path denylist landed; `sandbox/isolated_workspace/lifecycle/` package replaces rejected `sandbox/api/lifecycle/`. ≤8 atomic commits. **NOTE — earlier background-shell ownership entries from this consequence list are SUPERSEDED by Phase 2.5 and were never shipped.**
- Phase 2.5: background lifecycle delta (≤5 atomic commits) — deletes `shell_job.py` + `shell_job_handler.py` + 4 shell wire verbs; adds `invocation_id` envelope + `api.v1.cancel(invocation_id)` + `InFlightInvocationRegistry` + `api.v1.heartbeat`; engine-layer Q4 + iws-exit drain; verb-supplied cancellation cleanup. See [`unify_sandbox_workspace_phase2_5.md`](unify_sandbox_workspace_phase2_5.md).
- Phase 3: test reshape + doc updates; CHANGELOG entry; Tier 8 soak baseline reshape with perf escalation threshold; new `behavior_upgrade/`, `unit/`, `observability/` tiers ship; deployment pre-flight CI lands; tombstone flag deleted. ≤5 atomic commits.
- **iws behavior upgrade:** today's `ops_handlers.py` (98 lines of shell-out wrappers) is replaced with the typed-verb spec. `edit_file` gains real search/replace (was full-body overwrite); `grep` honors `mode`/`case_insensitive`/`include_pattern`/`multiline` (was hardcoded `/usr/bin/grep -r -n`); `read_file` enforces 16MB cap; `write_file` gains OCC conflict tracking. Validated by Phase 3 `behavior_upgrade/` tier.
- **Daytona disposition:** `sandbox/provider/daytona/` preserved-but-unmaintained; follow-up plan deletes (Planner F.15 / Critic Section D #9 — not "out of scope" silence).
- **Perf:** read/grep/glob gain ~50–200 ms per-call mount overhead (acceptable per user judgment in LLM workflows). **Escalation threshold:** read p50 > 200ms or p99 > 500ms in `baseline_post_unify.json` auto-files a follow-up issue to revisit Option Y verb-level asymmetry.
- **Concurrency:** OCC disjoint-batch coalescing preserved via `source="api_write"` for single-path typed writes (`len({c.path for c in changes}) == 1`); per-handle `asyncio.Lock` prevents `_destroy_with_lease_guard` TOCTOU race.
- **Security:** `tool_primitives.file_ops.open_no_follow` chokepoint with per-component walk (or `openat2(RESOLVE_NO_SYMLINKS)`) defends against trailing AND intermediate symlink escape inside the root-namespace child. Host-path denylist (`/etc/`, `/var/`, `/proc/`, `/sys/`, `/boot/`) rejects writes BEFORE the kernel call — closes the root-in-namespace privilege gap that pre-unification accidentally relied on unprivileged-daemon EACCES.
- **Observability:** plugin-block fail-OPEN emits `workspace_lifecycle.plugin_check_unbootstrapped` audit event; per-call `timings["mount_ms"]` populated; iws upperdir mid-session gauge available.
- **Portability:** new mount API + private user namespaces required; Docker-only. `scripts/verify_overlay_preconditions.py` is a deployment guard run by CI. `EOS_REQUIRE_NEW_MOUNT_API=0` permits fallback during the rollout window; flag deleted in Phase 3.
- **Reversibility:** each phase delivered as ≤10/≤8/≤5 atomic commits with `git revert <sha>` rollback per commit. Tests run on parent SHA before each commit lands.

**Follow-ups (out of this plan):**
- Mypy-Union narrowing on `ToolCallResult` types.
- Collapse iws lifecycle RPCs into `api.v1.workspace.*` (separate plan).
- `IsolatedWorkspaceSession` subclass for iws-only host methods (only if a production caller materializes).
- Cache iws `manifest_version` per-process.
- Delete `sandbox/provider/daytona/` (separate plan).
- Fail-CLOSED plugin block with explicit bypass (after auditing test fixtures).
- Rename `Pipeline` → `Executor` / `Context` (cosmetic; Planner C.5).
- Rename `_shared/` → `common/` (cosmetic; Planner C.4).
- Per-call read-latency revisit if Phase 3 §7.4 escalation threshold fires.

**Pre-mortem (deliberate mode — 3 scenarios with leading indicators):**

Each scenario lists the failure, what users see, **the specific signal we'd observe FIRST** (Critic must-fix #14), and the mitigation already enacted in the plan.

1. **Background tool lifecycle failure modes** — see Phase 2.5 §13 pre-mortem for the three scenarios that supersede the original Phase 2 background-shell scenario: envelope `invocation_id` migration regressions; cancel-ordering invariant (namespace-child exits BEFORE overlay destroy); multi-engine / engine-restart Q4 split-brain. Phase 2.5's pre-mortem is more specific because the new design has fewer race surfaces — coroutine-bound lease lifetime removes the old registration window, shell-specific reap/cancel races, and double-destroy from a job reaper coroutine racing a foreground finally. The remaining racy surface is the engine ↔ daemon wire-cancel handshake (Phase 2.5 §5.7 try/finally + §11 sub-test M).

2. **Concurrent destroy TOCTOU race in `_destroy_with_lease_guard`** (Phase 2 §3.1 / Planner D.2):
   - **Fails when:** shell-job reaper coroutine and the main `finally` block both reach `_destroy_with_lease_guard` for the same handle. Without the lock, both pass `_destroyed=False` before either awaits `overlay.destroy` → double umount → EBUSY/EINVAL from kernel; lease released twice.
   - **User-visible:** sporadic production failures; tests pass because they don't run shell jobs in parallel.
   - **Leading indicator (specific signal):** Tier 8 soak JSONL audit shows `release_lease` event for the same `lease_id` twice within ~1 second AND `gc_orphan_count` goes non-zero with the orphan being a lease that was released to an agent's session AND attempted-released by the reaper. That double-release event is the canary; without the lock it WILL fire.
   - **Mitigation enacted:** per-pipeline `_handle_locks: dict[str, asyncio.Lock]` keyed by `lease_id`; lock entry popped after destroy. Phase 3 §6.5 `test_destroy_under_asyncio_interleaving.py` would FAIL without this fix.

3. **Migration ordering trap — sandbox refuses to boot on degraded kernel** (Phase 1 §4.5 / Planner D.3):
   - **Fails when:** Phase 1 lands and `new_mount_api_supported()` becomes a hard precondition. Operator deploys to an environment whose kernel doesn't support the new mount API. Sandbox refuses to boot.
   - **User-visible:** service refuses to start in some environments; rollback requires reverting Phase 1's PR (large blast radius).
   - **Leading indicator (specific signal):** `scripts/verify_overlay_preconditions.py` exits non-zero on the target kernel. **The signal fires BEFORE deployment if the script is wired into the CI deploy pipeline** (Phase 3 §6C.2) — operator sees the build fail at the verify step, not at boot time.
   - **Mitigation enacted:** Phase 1 §4.5.1 ships the script; Phase 1 §4.5.2 demands a pre-rollout audit of every deployment target; Phase 1 §4.5.3 ships the `EOS_REQUIRE_NEW_MOUNT_API=0` tombstone flag as a 30-day rollback escape; Phase 3 §6C wires the verify script into CI; flag deleted at the end of Phase 3.
