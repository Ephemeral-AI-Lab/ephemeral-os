# Phase 2.5 — Background Tool Lifecycle (delta to Phase 2)

**Type:** Substantive correction to Phase 2's background-shell design. Eliminates the daemon-side ShellJob registry and the four `shell_launch/reap/poll/cancel` verbs; folds background semantics into the engine-owned asyncio.Task lifecycle that already wraps every other background-capable tool.
**Scope:** Generalize background execution from "shell-specific verbs + daemon-side ShellJob" to "any tool call can carry `background=true`; the engine's existing `BackgroundTaskManager` is the lifecycle wrapper; the pipeline's `run_tool_call` coroutine body is unchanged between foreground and background." Overlay lease lifetime tracks the coroutine — and therefore the asyncio.Task — naturally via the existing try/finally.
**Depends on:** Phase 2 §1–§14 (foundation pipelines + overlay primitives + OCC source-tag + tool_primitives + lifecycle host API). Phase 2.5 REPLACES Phase 2 §3.1's background-shell sub-section, §3.3's `ShellJobRegistry` restructure, §4.1's `_session_jobs` / `_dispatch_background_verb_iws`, §7.2's `_SHELL_RPC_TO_VERB` table.
**Blocks:** Phase 3 background-shell test tier (§6.6 sub-tests A–H must be rewritten against the new generic model).
**Atomic commit plan:** ≤5 logical commits. Suggested split: (1) wire-level `request_id` + `api.v1.cancel(request_id)` RPC + daemon in-flight request registry; (2) cancellation-aware `overlay.run_in_namespace` + `tool_primitives.shell.run` (extract `cancel_event` from `shell_job.py`); (3) `ToolCallRequest.background` flag + pipeline body remains unchanged; engine wraps `pipeline.run_tool_call` in `BackgroundTaskManager.launch`; (4) engine-layer Q4 + iws-exit drain (`BackgroundTaskManager.count_by_agent` + `cancel_by_agent`); (5) delete `sandbox/daemon/service/shell_job.py` + `shell_job_handler.py` + the four `api.v1.shell.{launch,reap,poll,cancel}` RPCs + `is_background` branch in `tools/sandbox/shell/shell.py` + Phase 2 docs revised.

See [`unify_sandbox_workspace.md`](unify_sandbox_workspace.md) for the trichotomy overview. This document is a delta on [`unify_sandbox_workspace_phase2.md`](unify_sandbox_workspace_phase2.md) — sections it does NOT touch (overlay primitives, OCC source-tag plumbing, tool_primitives, lifecycle host API, host-path denylist, plugin-block gate, O_NOFOLLOW chokepoint, manager.py decomposition) stand unchanged.

---

## 0. Why this is its own phase

Phase 2 modeled background shell as a **shell-specific** lifecycle: `shell.launch` creates an overlay owned by a `ShellJob`; `shell.reap` waits + commits + destroys; `shell.cancel` kills + discards; `shell.poll` peeks. Two parallel registries existed (one in `EphemeralPipeline._background_jobs`, one in `IsolatedPipeline._session_jobs`). Q4 cross-mode rejection consulted the pipeline registries; iws-exit drained them.

The user directive: **background is a tool-call concept, not a shell concept.** The agent-facing surface already says so — `tools/background/{cancel,check,wait}_background_task` are tool-agnostic and work via the engine's `BackgroundTaskManager` (`backend/src/engine/background/manager.py`). Phase 2 left a parallel sandbox-side background abstraction that duplicates the engine's abstraction, restricts it to shell, and forces the pipeline to carry a verb-table-keyed branch (`_dispatch_background_verb` / `_dispatch_background_verb_iws`) that is exactly the kind of branching Principle 4 ("ONE method") wanted gone.

Phase 2.5 deletes the duplicate. The pipeline's protocol stays at one method whose body is identical for foreground and background; the engine's BackgroundTaskManager is the lifecycle wrapper for every background-capable tool (shell, read_file, write_file, edit_file, grep, glob, run_subagent, and any future tool that opts in via `background="optional"`).

---

## 1. Principles (delta from Phase 2 overview §2)

**P1 (revised — Phase 2 §2 Principle 1).** Overlay lifetime is **coroutine-bound**, not job-bound. The `OverlayHandle` is acquired inside `pipeline.run_tool_call` and released in that coroutine's `finally`. In foreground mode the caller awaits the coroutine, so its lifetime equals the call's wall-time. In background mode the engine wraps the coroutine as an `asyncio.Task`, so its lifetime equals the task's wall-time. Same `try/finally`. Same `_destroy_with_lease_guard`. No `ShellJob`, no per-job lease accounting on the pipeline.

**P2 (NEW).** Background is a **request flag**, not a verb. `ToolCallRequest.background: bool` is set by the engine when (and only when) the agent passed `background=true` on a `background="optional"`-declared tool. Pipeline code never branches on `background` — it's metadata for audit and for the engine's wrapper, not a control-flow input to the substrate.

**P3 (NEW).** Cancellation propagates via `asyncio.CancelledError`. Local `asyncio.Task.cancel()` (engine) + `api.v1.cancel(request_id)` (wire) + cancellation-aware `overlay.run_in_namespace` and `tool_primitives.shell.run` (kill namespace-child PG / shell-child PG on cancel) compose to a single contract: **a cancelled task runs its `finally` exactly once, destroys the overlay, and does NOT commit to OCC** (because the commit branch is on the post-`run_in_namespace` happy path).

**P4 (NEW).** Cross-mode rejection (Q4) and iws-exit drain are **engine-layer** concerns. The pipeline has no background registry to consult. `sandbox.lifecycle.enter_isolated_workspace` asks `BackgroundTaskManager.count_by_agent(agent_id)` before calling `pipeline.enter`; `sandbox.lifecycle.exit_isolated_workspace` asks `cancel_by_agent(agent_id, grace_s=...)` before calling `pipeline.exit`. The pipeline knows nothing about agent-scoped background populations.

**P5 (preserved — Phase 2 manager.py:32-38).** Terminal-status precedence on race: `completed > failed > cancelled > running`. A 1s shell that exits between cancel-signal and cancel-landing returns COMPLETED with its real result, not "Cancelled". This is already enforced by `BackgroundTaskManager._set_terminal_status` and the `_TERMINAL_PRECEDENCE` table. Phase 2.5 must not regress it.

---

## 2. Architecture (delta diagram)

### Before (Phase 2)

```
agent: shell(cmd, background=true)
   |
   v
engine/tool_call/dispatch.py → launch_background_tool
   |
   v
BackgroundTaskManager.launch(task_id, "shell", input, coro)   # coro = sandbox_api.shell(...)
   |
   v (await chain inside coro)
sandbox/api/tool/shell.py → daemon RPC api.v1.shell.launch
   |
   v
daemon/service/shell_job_handler.py → ShellJobRegistry.launch
   |
   v
EphemeralPipeline._launch_bg_job:                         # PHASE 2 DESIGN
  handle = overlay.create()
  child = spawn_background_in_namespace(handle, req)
  self._background_jobs[job_id] = ShellJob(handle, child, ...)
  return {"job_id": job_id}

(later)
agent: check_background_task_result(bg_X)
   |
   v
sandbox_api.shell continues: api.v1.shell.poll → api.v1.shell.reap
   |
   v
EphemeralPipeline._reap_bg_job: capture + commit + destroy
```

Two registries (engine + pipeline) racing for the same lifecycle; four wire verbs for one logical op; pipeline carries `_background_jobs` + `_session_jobs` + agent-index + `startup_gc` for orphan sweep.

### After (Phase 2.5)

```
agent: shell(cmd, background=true)
   |
   v
engine/tool_call/dispatch.py → launch_background_tool
   |
   v
BackgroundTaskManager.launch(task_id, "shell", input, coro)
                                                ^
                                       coro IS the wrapper.
                                       asyncio.Task IS the lifecycle.
   |
   v (await inside coro = one wire RPC)
sandbox/api/tool/shell.py → daemon RPC api.v1.shell  (foreground signature; long-running)
   |
   v
EphemeralPipeline.run_tool_call(req):       # SAME body as foreground
  handle = await overlay.create(...)
  try:
    result = await overlay.run_in_namespace(handle, req)   # cancellation-aware
    if req.intent == WRITE_ALLOWED:
      changes = await overlay.capture_changes(handle)
      result = await self._commit(...)                     # source="api_write" or "overlay_capture"
    return result
  except asyncio.CancelledError:
    # daemon-side: namespace child PG already SIGTERM'd by run_in_namespace
    raise                                                  # finally still runs
  finally:
    await self._destroy_with_lease_guard(handle)

(later)
agent: cancel_background_task(bg_X)
   |
   v
BackgroundTaskManager.cancel(task_id, reason):
  tracked.asyncio_task.cancel()
   |
   v (local cancel propagates; wire layer also sends api.v1.cancel(request_id))
daemon RPC api.v1.cancel(request_id):
  task = self._in_flight[request_id]
  task.cancel()       # raises CancelledError inside run_tool_call
   |
   v
EphemeralPipeline.run_tool_call's finally fires → overlay destroyed → lease released
```

One registry. One wire RPC per tool call. Pipeline body identical between foreground and background. Cancellation is a single contract that flows through asyncio + a generic cancel RPC.

---

## 3. RALPLAN-DR Summary

### Decision Drivers (top 3)

1. **Single source of truth for background lifecycle.** Two registries (engine `BackgroundTaskManager` + daemon `ShellJobRegistry`) doing the same job is the root cause of the smell — pick one. The engine's wins because it's already tool-agnostic and already drives `tools/background/{cancel,check,wait}_background_task`.
2. **Principle 4 fidelity.** Pipeline's `run_tool_call` should have ONE branch-free body. Phase 2's `_BACKGROUND_SHELL_VERBS` frozenset + `_dispatch_background_verb` was a four-line tax that exists ONLY because of the duplicate registry; remove the duplicate and the tax goes too.
3. **Overlay lease lifetime = coroutine lifetime.** This is the simplest binding possible — Python's own `try/finally` is the wrapper. Any abstraction on top (ShellJob, per-pipeline `_background_jobs`) is one indirection too many.

### Viable Options

#### Option α — Keep ShellJob, generalize to ToolJob (REJECTED)

Rename `ShellJob` → `ToolJob`; `_background_jobs` keys any background verb, not just shell; same four launch/reap/poll/cancel verbs but verb-typed.

- **Pro:** Smaller diff. Daemon-side orphan reaper (TTL + lease-release on engine death) survives unchanged.
- **Con:** Still duplicates the engine's `BackgroundTaskManager`. Pipeline still carries a verb branch (now `_BACKGROUND_TOOL_VERBS` frozenset). Two-RPC pattern still required. **Violates the user directive** ("Do not have items like ShellJob ... because it is background task specific rather than shell specific"). The user's complaint is about the abstraction LAYER, not the abstraction's GENERICITY.

#### Option β — Engine cancels local Task only; daemon discovers via connection-close (REJECTED)

No `api.v1.cancel(request_id)` RPC. Engine cancels its local asyncio.Task; the wire-level transport notices the dropped response future and signals the daemon via connection-state; daemon-side dispatcher detects "request abandoned" and cancels the in-flight asyncio.Task it spawned.

- **Pro:** Cleanest wire (no new verb).
- **Con:** Today's transport doesn't carry per-request lifecycle signals (`grep request_id sandbox/api/protocol.py sandbox/daemon/rpc/{server,dispatcher}.py` → zero hits). Implementing connection-close-as-cancel requires either HTTP/2 streams or a custom keepalive-with-cancel-frame protocol — neither of which is in scope here. **Premature transport investment.**

#### Option γ (CHOSEN) — Engine asyncio.Task as wrapper; generic `api.v1.cancel(request_id)` RPC; pipeline body unchanged

Detailed in §2 above and §5–§8 below.

- **Pro:** Matches user directive verbatim. Pipeline body shrinks (no `_dispatch_background_verb`, no `_background_jobs`). Single lifecycle wrapper (engine-side). Wire-cancel is a generic RPC reusable by every verb. Cancellation contract is uniform (CancelledError + finally).
- **Con:** Wire layer gains `request_id` correlation it doesn't have today — has to be added to every RPC envelope. Daemon gains an in-flight task registry (request-keyed, not job-keyed; smaller and tool-generic). Existing ShellJobRegistry's TTL reaper (release lease when engine dies mid-call) needs an analog at the new request-keyed registry — see §6.

### Invalidation rationale for α / β

α leaves the abstraction layer the user complained about; β leaves the transport story unsolved AND wire-cancel still has to exist somewhere (today as `api.v1.shell.cancel`). γ is the only option that satisfies "no ShellJob, no shell_launch/reap/cancel/poll, lifecycle on top of any tool" simultaneously.

---

## 4. Module changes — DELETES

These are absolute deletes (no shim, no compat alias):

- **`sandbox/daemon/service/shell_job.py`** (609 lines verified) — `ShellJob` dataclass + `ShellJobRegistry` class + module-level singleton (`_REGISTRY` / `get_shell_job_registry()` / `reset_shell_job_registry()`) + helper functions (`_signal_pgrp`, `_escalate_kill`, `_pgrp_alive`, `_read_tail`, `_read_full`, `_change_path`). The TTL reaper logic moves to the new request-keyed registry in §6.
- **`sandbox/daemon/service/shell_job_handler.py`** (174 lines verified) — RPC dispatchers for `api.v1.shell.launch / reap / poll / cancel`. The cancel-by-job-id case becomes cancel-by-request-id in §6.
- **`sandbox/api/transport.py:19::DAEMON_OP_SHELL_CANCEL`** constant — replaced by a generic `DAEMON_OP_CANCEL = "api.v1.cancel"`.
- **`tools/sandbox/shell/shell.py:149–166`** — the `is_background = bool(getattr(context, "background_task_id", None))` branch and the `background=is_background` argument passed to `sandbox_api.shell(...)`. The shell tool stops being background-aware; the engine wrapper does the wrapping.
- **`tools/sandbox/shell/_sandbox_api_background_pathway`** (if such a branch exists in `sandbox/api/tool/shell.py`) — collapsed to the single-RPC foreground pathway. Verify with `grep -n "background" sandbox/api/tool/shell.py` during Step 5.
- **Phase 2 §3.1 deletions** in `EphemeralPipeline`:
  - `_BACKGROUND_SHELL_VERBS` frozenset
  - `_background_jobs: dict[str, ShellJob]`
  - `_jobs_by_agent: dict[str, set[str]]`
  - `_dispatch_background_verb`
  - `_launch_bg_job`, `_reap_bg_job`, `_poll_bg_job`, `_cancel_bg_job`
  - `get_agent_background_jobs(agent_id)`
  - `startup_gc()` (replaced by request-keyed registry's TTL reaper)
- **Phase 2 §4.1 deletions** in `IsolatedPipeline`:
  - `_session_jobs: dict[str, dict[str, ShellJob]]`
  - `_dispatch_background_verb_iws`
  - `_drain_background_jobs` (drain logic moves to engine — `BackgroundTaskManager.cancel_by_agent(agent_id, grace_s)`; pipeline.exit no longer drains)
  - The `ephemeral_jobs_in_flight` check inside `enter` (moves to engine — `sandbox.lifecycle.enter_isolated_workspace` checks `BackgroundTaskManager.count_by_agent`)
- **Phase 2 §7.2** `_SHELL_RPC_TO_VERB` table — gone entirely. `daemon/handler/shell.py` becomes the same shape as `daemon/handler/read.py` (one verb, one intent, one `pipeline.run_tool_call`).
- **Phase 3 §6.6 sub-test H** ("static check that no public `^(launch|reap|poll|cancel)_background_job$` method exists on EphemeralPipeline") is preserved AS-IS — the assertion's value strengthens after this phase since the methods don't exist at all.

---

## 5. Module changes — ADDS

### 5.1. `ToolCallRequest.background: bool = False`

Add to `sandbox/_shared/models.py::ToolCallRequest` (defined in Phase 2 §1.1). Default `False`. Set by `daemon/handler/*` when the inbound RPC carries a `background=true` arg (today's shell already carries it; other verbs gain the schema field — opt-in per tool via the framework's existing `background="optional"` decorator).

**Pipeline contract:** the body of `run_tool_call` MUST NOT branch on `req.background`. The flag is metadata for audit (`audit.tool_op_started.background=true`) and for the engine's wrapper. If a future implementer adds a `if req.background:` somewhere in pipeline code, the static lint (§9) fails the build.

### 5.2. Wire-level `request_id` correlation

`sandbox/api/protocol.py` envelope gains a `request_id: str` field (today: zero hits per `grep -c request_id`). Every outgoing RPC from `sandbox/api/_raw_exec.py` populates a fresh `uuid4().hex` (or pulls from `ExecutionMetadata.background_task_id` when present, so audit can cross-link). Daemon-side `daemon/rpc/server.py` reads it and stamps every spawned asyncio.Task with it before inserting into the in-flight registry (§6).

### 5.3. Daemon-side in-flight request registry (`sandbox/daemon/rpc/in_flight.py` — NEW)

```python
class InFlightRequestRegistry:
    """Tracks daemon-side asyncio.Tasks by request_id for generic cancellation.

    Replaces the shell-specific ShellJobRegistry (deleted in §4). Does NOT
    hold overlay handles, ShellJobs, or lease accounting — the pipeline's
    own try/finally owns those. This registry only maps request_id → Task
    so api.v1.cancel(request_id) can cancel the Task and let the
    pipeline's existing destroy chokepoint do the cleanup.
    """
    _by_request: dict[str, asyncio.Task]
    _ttl_seconds: float                  # default 300s; env override EOS_INFLIGHT_TTL_S
    _last_seen: dict[str, float]         # monotonic time of last "alive" signal

    def register(self, request_id: str, task: asyncio.Task) -> None: ...
    def cancel(self, request_id: str) -> bool: ...           # api.v1.cancel impl
    def deregister(self, request_id: str) -> None: ...       # called by run_tool_call's finally
    async def ttl_reaper_loop(self) -> None: ...             # cancels Tasks whose engine has gone silent
```

**TTL reaper preserves the existing safety net** (orphan cleanup when engine dies mid-shell). It's smaller than `ShellJobRegistry` because it has no overlay/lease accounting — just task tracking. When a TTL fires, the registry cancels the asyncio.Task; the pipeline's `try/finally` runs and cleans up the overlay.

### 5.4. `api.v1.cancel(request_id)` daemon RPC

`sandbox/daemon/handler/cancel.py` (NEW — ~15 lines):

```python
async def cancel(args: dict[str, object]) -> dict[str, object]:
    request_id = require_arg(args, "request_id")
    cancelled = in_flight.cancel(request_id)
    return {"success": True, "cancelled": cancelled}
```

`sandbox/api/_raw_exec.py` exposes a client-side `cancel(request_id)`. Engine's `BackgroundTaskManager.cancel(task_id, reason)` gains a hook: for sandbox-bound tasks (detected by tool name in `_sandbox_tool_names` or by tracked metadata), it sends `api.v1.cancel(request_id)` over the wire BEFORE calling `tracked.asyncio_task.cancel()`. The ordering matters: sending the wire cancel first means the daemon's `run_tool_call` raises CancelledError and runs its `finally` while the engine's local Task is still alive to await the cleanup response.

### 5.5. Cancellation-aware `overlay.run_in_namespace` (verb-supplied cleanup, P5 preserved)

**Architect P5 finding:** plumbing `cancel_event: threading.Event` and `pgrp_holder` through `run_in_namespace`'s signature leaks shell-specific shape into a primitive used by ALL six verbs. Phase 2.5 closes this by inverting the dependency — verbs that need cancellation cleanup supply their own handler, the primitive only forwards `CancelledError`.

Add `sandbox/_shared/tool_primitives/cancellation.py` (NEW):

```python
class VerbCancellation(Protocol):
    """Verb-supplied cleanup callable invoked when run_in_namespace receives
    asyncio.CancelledError. Read/write/edit/grep/glob return NO_OP_CANCELLATION
    (their compute unwinds via Python's native CancelledError propagation).
    Shell returns ShellPgrpCancellation(cancel_event, pgrp_holder)."""

    def on_cancel(self) -> None: ...

NO_OP_CANCELLATION: VerbCancellation = _Noop()  # singleton no-op for pure-compute verbs

class ShellPgrpCancellation:
    """Shell-specific: SIGTERM the child PG; 2s SIGKILL escalation."""
    def __init__(self, cancel_event: threading.Event, pgrp_holder: list[int]) -> None: ...
    def on_cancel(self) -> None: ...
```

`overlay/namespace.py::run_in_namespace` stays verb-shape-clean:

```python
async def run_in_namespace(handle: OverlayHandle, req: ToolCallRequest) -> ToolCallResult:
    """Cancellation forwarding only. Verb-specific cleanup is owned by the verb."""
    cancellation = _build_verb_cancellation(req)  # dispatched by req.verb
    try:
        return await _run_in_namespace_inner(handle, req, cancellation=cancellation)
    except asyncio.CancelledError:
        cancellation.on_cancel()
        raise

def _build_verb_cancellation(req: ToolCallRequest) -> VerbCancellation:
    if req.verb == "shell":
        return ShellPgrpCancellation(threading.Event(), [])
    return NO_OP_CANCELLATION
```

The `cancel_event` + `pgrp_holder` arguments that `tool_primitives.shell.run` already accepts (Phase 1 §6.7 + `shell_job.py:360-362`) are now sourced from the `ShellPgrpCancellation` instance passed to `_run_in_namespace_inner` for shell calls; for non-shell verbs no such instance flows through at all. The `if req.verb == "shell"` branch in `_build_verb_cancellation` is a Tier-2 dispatch decision (same shape as Phase 2 §5.2's two-tier verb table) — it's NOT a control-flow branch inside the shared substrate.

→ **Verify:** static lint `tests/static/test_run_in_namespace_no_shell_state.py` (NEW) — AST scan asserts `run_in_namespace`'s function body references neither `threading.Event` nor `pgrp_holder`. Those names appear ONLY in `cancellation.py` and `shell_job.py`'s extracted body.

### 5.6. Engine-layer Q4 + iws-exit drain

`BackgroundTaskManager` gains two agent-scoped methods (`engine/background/manager.py`):

```python
def count_by_agent(self, agent_id: str) -> int:
    """Number of RUNNING sandbox-bound background tasks owned by agent_id."""
    return sum(
        1 for t in self._tasks.values()
        if t.status == TaskStatus.RUNNING
        and getattr(t, "agent_id", None) == agent_id
        and getattr(t, "uses_sandbox", False)
    )

async def cancel_by_agent(self, agent_id: str, *, grace_s: float) -> int:
    """Cancel sandbox-bound bg tasks for agent_id, await up to grace_s; return survivor count."""
    targets = [t for t in self._tasks.values()
               if t.status == TaskStatus.RUNNING
               and getattr(t, "agent_id", None) == agent_id
               and getattr(t, "uses_sandbox", False)]
    for t in targets:
        await self.cancel(t.task_id, reason=f"iws_exit_drain(agent_id={agent_id})")
    deadline = monotonic_now() + grace_s
    survivors = [t for t in targets if t.status == TaskStatus.RUNNING]
    # await asyncio.wait with timeout, force-kill survivors via wire cancel + asyncio.cancel
    ...
    return len(survivors)
```

`TrackedBackgroundTask` gains two fields: `agent_id: str | None = None` and `uses_sandbox: bool = False`. The engine's `launch_background_tool` (`backend/src/engine/background/dispatch.py`) populates them from `ExecutionMetadata` / tool registry (`tool_def.uses_sandbox` becomes a `@tool` declaration on every sandbox-touching tool).

`sandbox.lifecycle.enter_isolated_workspace` (Phase 2 §10.2) gains a pre-check:

```python
async def enter_isolated_workspace(req, *, background_manager):
    in_flight = background_manager.count_by_agent(req.agent_id)
    if in_flight > 0:
        return EnterIsolatedWorkspaceResult(
            success=False,
            error=LifecycleError(
                kind="ephemeral_jobs_in_flight",
                details={"count": str(in_flight)},
            ),
        )
    async with lifecycle_operation(...):
        pipeline = isolated_workspace.require_pipeline()
        handle = await pipeline.enter(req.agent_id, _config_from_req(req))
        return EnterIsolatedWorkspaceResult(success=True, ...)
```

`sandbox.lifecycle.exit_isolated_workspace` (Phase 2 §10.3) drains before exit:

```python
async def exit_isolated_workspace(req, *, background_manager):
    async with lifecycle_operation(...):
        survivors = await background_manager.cancel_by_agent(req.agent_id, grace_s=req.grace_s)
        result = await isolated_workspace.require_pipeline().exit(req.agent_id, grace_s=0.0)
        # grace_s already consumed by the drain; pipeline.exit becomes synchronous teardown.
        return result.with_phases(evicted_background_jobs=survivors)
```

The host-side coroutine is the integration point. The pipeline neither sees nor cares about agent-scoped task populations.

### 5.7. Engine-side wire-cancel hook in `BackgroundTaskManager.cancel`

Currently `BackgroundTaskManager.cancel(task_id, reason)` only calls `tracked.asyncio_task.cancel()`. After Phase 2.5 it also propagates over the wire for sandbox-bound tasks:

```python
async def cancel(self, task_id: str, reason: str = "") -> bool:
    tracked = self._tasks.get(task_id)
    if tracked is None:
        return False
    tracked.cancel_reason = reason or None
    if not should_cancel_asyncio_task(tracked):
        await request_subagent_early_stop(tracked, reason=reason)
        return True
    # NEW: wire-cancel for sandbox-bound tasks; daemon-side run_tool_call
    # sees CancelledError, runs its finally, releases lease, returns.
    # Architect B3: wire-cancel is BEST-EFFORT; local-cancel ALWAYS fires.
    # If the wire RPC raises (network blip, daemon process restart), we
    # MUST NOT let the exception block the local cancel — otherwise the
    # agent's task hangs forever waiting on a future that nobody will
    # complete.
    tracked.stop_mode = "cancel"
    applied = self._set_terminal_status(...)
    try:
        if getattr(tracked, "uses_sandbox", False):
            request_id = getattr(tracked, "sandbox_request_id", None)
            if request_id:
                try:
                    await sandbox_api.cancel(tracked.sandbox_id, request_id)
                except Exception as exc:
                    logger.warning(
                        "wire-cancel failed for task_id=%s request_id=%s: %s "
                        "(local cancel still firing; daemon TTL reaper will clean up)",
                        task_id, request_id, exc,
                    )
    finally:
        tracked.asyncio_task.cancel()
    return True
```

`TrackedBackgroundTask.sandbox_request_id` and `.sandbox_id` are stamped by the engine wrapper at launch time (from the same `request_id` the wire envelope carries).

**Contract:** wire-cancel is best-effort (a failed wire-cancel logs WARN and falls through to the daemon-side TTL reaper for eventual cleanup); local-cancel ALWAYS fires (engine's `asyncio.Task` is unblocked regardless). This ordering prevents the latent deadlock the Architect flagged in §13 pre-mortem.

---

## 6. Orphan cleanup story (preserves today's safety net)

**The risk Phase 2.5 must NOT silently regress:** today's `ShellJobRegistry.ttl_reaper` (lines 415–470 of `shell_job.py`) releases leases when the engine dies mid-shell. Without an analog, an engine crash mid-background-shell would leak overlay leases until daemon restart.

**Analog in Phase 2.5:** `InFlightRequestRegistry.ttl_reaper_loop`. Heuristic for "engine has gone silent":

| Signal | Today (ShellJobRegistry) | After Phase 2.5 (InFlightRequestRegistry) |
|---|---|---|
| Liveness ping | `last_poll_at` updated by every `shell.poll` RPC | `last_seen` updated by either (a) `api.v1.heartbeat(request_id)` (NEW lightweight ping the engine sends every N seconds for live background tasks) OR (b) wire-level keepalive frame the transport already supports (verify in §10) |
| TTL | 300s (`DEFAULT_TTL_SECONDS`, env `EOS_SHELL_JOB_TTL_S`) | 300s, env `EOS_INFLIGHT_TTL_S` |
| On expiry | SIGKILL pgrp, release lease, no commit | `asyncio.Task.cancel()` on the in-flight task; pipeline's existing finally releases lease + destroys overlay; no commit (CancelledError) |
| Counter | `_ttl_reaped_total`, exposed via `api.v1.shell.metrics` | `_ttl_reaped_total`, exposed via `api.v1.cancel.metrics` (or absorbed into daemon healthcheck) |

**Heartbeat choice — RESOLVED to 6.A (explicit `api.v1.heartbeat`).**

The transport layer (`sandbox/api/transport.py`) does not today carry per-request keepalive signals; adopting 6.B would couple Phase 2.5 to a transport refactor that is out of scope. 6.A is therefore the path:

- Engine emits one `api.v1.heartbeat(request_ids: list[str])` RPC every 60s carrying the IDs of all in-flight sandbox-bound background tasks owned by that engine. Daemon updates `_last_seen[request_id] = monotonic_now()` for each.
- TTL default `EOS_INFLIGHT_TTL_S = 300s` (5×heartbeat interval — survives 4 missed pings before reaping).
- **False-positive guard (Architect B2):** the interval is sized for normal jitter:
  - p99 LLM streaming pause: typically <30s (observed empirically in Tier 8 soak).
  - GC pause / async stall on engine: <5s.
  - 4× p99 streaming pause = 120s ≤ 300s TTL → safe headroom.
  - If Tier 8 measurements show p99 streaming pause >60s on any agent, the TTL is bumped to `max(300, 5*p99_streaming_pause_s)` at next baseline reshape.
- **Batched ping (not per-task ping)** keeps cost flat at one RPC per engine per 60s regardless of N in-flight tasks. At 1000 concurrent agents this is 1000 pings/min daemon-side — bounded and visible in metrics. (The N=64 worst case the Architect cited assumed per-task pings; the batched form trades a slightly larger payload for O(1) RPC count.)
- **Engine-restart detection (Architect B4 split-brain):** the daemon records the engine's `process_id` + `started_at` in the heartbeat payload. When a new engine connects with a fresh `process_id`, the daemon assumes the old engine's in-flight tasks are orphaned and triggers TTL reap immediately for them (no need to wait for 5×60s). This shortcuts orphan cleanup on the common case (engine crash + restart).

Heartbeat is implemented in commit (1) alongside the envelope `request_id` change so the wire surface lands as a single atomic edit.

---

## 7. Cancellation contract (preserves terminal-status precedence)

Phase 2.5 preserves the existing `_TERMINAL_PRECEDENCE` table (`engine/background/manager.py:32–38`). The race scenario:

1. Background shell `bg_5` is 990ms into a `sleep 1` (will exit naturally at 1000ms).
2. Agent calls `cancel_background_task(bg_5)` at 995ms.
3. Engine's `BackgroundTaskManager.cancel` sends `api.v1.cancel(request_id)` over the wire.
4. At 1000ms the shell exits naturally; pipeline's `run_tool_call` reaches the post-`run_in_namespace` happy path, commits, runs `finally`, returns COMPLETED result.
5. The wire `api.v1.cancel` RPC arrives at the daemon at 1005ms — the in-flight registry's `deregister(request_id)` has already fired in the `finally`; the cancel is a no-op (returns `cancelled=False, already_done=True`).
6. The engine's local `asyncio.Task.cancel()` fires after the await returns; since the Task already produced its result, `cancel()` is a no-op on a completed task.
7. `_set_terminal_status` sees COMPLETED already latched (rank 3); the CANCELLED attempt (rank 1) is dropped. Agent sees COMPLETED with the real shell output.

**Acceptance check:** a new Phase 3 sub-test `test_cancel_landing_after_completion_preserves_result.py` exercises this race deterministically (mock the wire-cancel delivery to land AFTER the wire-response).

---

## 8. Timeout enforcement (explicit decision)

**Timeouts live in the daemon, not the engine.** Today's shell passes `timeout` in the request body; daemon-side `tool_primitives.shell.run` enforces it via `subprocess.communicate(timeout=...)` and falls back to SIGTERM/SIGKILL. Phase 2.5 PRESERVES this — the engine does NOT wrap in `asyncio.wait_for`. Reasons:

1. Timeout-on-cancel is a different code path than network-loss-on-cancel; conflating them at the engine yields worse diagnostics.
2. Today's daemon-side timeout produces an explicit `timed_out_after=Ns` field in the result; engine-side `wait_for` would surface as a generic `asyncio.TimeoutError`.
3. Subagent tools (non-sandbox) handle their own timeouts; uniform behavior across sandbox + non-sandbox bg tasks means engine shouldn't impose.

**Wiring change:** when the daemon-side shell times out and SIGKILL's the child, the pipeline's `run_tool_call` returns a non-cancelled result with `timed_out=True`. The engine's `BackgroundTaskManager` sees natural completion (not cancellation); status latches to COMPLETED with an error result. This matches today's behavior; tests in Phase 3 §6.6 sub-test I (NEW — added by this phase, see §11) pin it.

---

## 9. Static lint additions

`tests/static/test_pipeline_run_tool_call_no_background_branch.py` (NEW) — AST scan of `EphemeralPipeline.run_tool_call` and `IsolatedPipeline.run_tool_call` bodies. Asserts:

1. No `if req.background:` (or `req.args["background"]` lookup).
2. No reference to `_background_jobs`, `_session_jobs`, `ShellJob`, `_dispatch_background_verb`.
3. No reference to the four `shell_launch/reap/poll/cancel` verb strings.
4. Body length ≤30 lines per pipeline (Principle: "fits on one screen" — Phase 2.5 should make it tighter than Phase 2's original which was already short).

Lints in (1)–(3) catch regressions where a future implementer re-introduces verb branching. (4) is a soft cap on body length.

---

## 10. Pre-implementation audit checklist

Before commit (1) lands, the implementer MUST:

- **A1.** Run `grep -rn "request_id" sandbox/api/ sandbox/daemon/rpc/` and confirm the count is still ~0 (matches my pre-plan check). If a recent commit added `request_id` to the protocol, fold into the audit and skip §5.2's "NEW" framing.
- **A2.** Run `grep -rn "api.v1.shell.cancel\|api.v1.shell.launch\|api.v1.shell.reap\|api.v1.shell.poll" backend/` and inventory every caller. Each one must be migrated in commits (3) or (5).
- **A3.** Read `sandbox/api/transport.py` end-to-end and decide between heartbeat options 6.A and 6.B. Document the decision in the ADR.
- **A4.** Read `tools/sandbox/shell/_lib/` (if it exists) and confirm there's no client-side "launch then reap" orchestration that needs to collapse to a single RPC.
- **A5.** Confirm `engine/background/manager.py::TrackedBackgroundTask` is the only place `agent_id` / `uses_sandbox` would need to land. (Verify via `grep -rn "TrackedBackgroundTask" backend/`.)
- **A6.** Read `sandbox/daemon/rpc/dispatcher.py:227–233` (the existing 4 `api.v1.shell.{launch,reap,poll,cancel}` route entries) and verify they are the only call sites that need deletion in commit (5). Confirm `api.v1.cancel` can be added to the dispatch table without colliding with `api.v1.shell` (which becomes the foreground-and-background unified shell RPC).
- **A7.** Inventory every `background="optional"` declaration: `grep -rn 'background="optional"' backend/src/tools/`. Today this is ONLY `tools/sandbox/shell/shell.py:133`. If any other tool has been added since this plan was drafted, the §5.1 contract ("pipeline body never branches on `req.background`") must hold for that tool too — verify it doesn't introduce a verb-specific background-aware code path.
- **A8.** Read `engine/background/subagent_policy.py::should_cancel_asyncio_task` and verify the `BackgroundTaskManager.cancel` flow for a subagent that is also sandbox-bound. Today (`manager.py:268-270`) subagents go through `request_subagent_early_stop` and skip the asyncio-cancel path. Phase 2.5's §5.7 wire-cancel hook runs BEFORE the subagent-policy check; the ordering means a sandbox-bound subagent gets BOTH wire-cancel + cooperative early-stop. Decide: is this the desired semantics (likely yes — wire-cancel cleans up daemon-side overlay, early-stop gives the subagent a chance to salvage)? Document explicitly in the §5.7 code block as a comment if so; otherwise reorder.

If any audit reveals a discrepancy from the assumptions baked into this plan, halt and surface the delta before proceeding.

---

## 11. Phase 3 test impact (sketch — full update lives with Phase 3 commits)

Phase 3's `tests/mock/sandbox/concurrency/test_background_shell_lifetime.py` (currently 8 sub-tests A–H) must be REWRITTEN against the new model. Sub-test redistribution:

| Old (Phase 2 framing) | New (Phase 2.5 framing) |
|---|---|
| A. ephemeral happy path — `shell.launch` returns job_id; `_background_jobs[job_id]` populated; reap commits | Engine `launch_background_tool` returns `bg_X`; `BackgroundTaskManager._tasks[bg_X]` populated; underlying RPC is `api.v1.shell` (foreground sig); on task completion overlay destroyed + OCC committed; pipeline body never sees `background` flag in branch logic |
| B. OCC conflict at reap — best-effort surface | Best-effort OCC at end of `run_tool_call`; CancelledError NEVER hits this path (commits happen on happy path only); conflict surfaced via normal `ConflictInfo` field |
| C. cancel discards | `cancel_background_task(bg_X)` → wire cancel → CancelledError → pipeline finally destroys overlay, NO commit; lease released exactly once |
| D. interleaved foreground + background | Two coroutines, two leases, two destroy chokepoints; per-handle locks (Phase 2 §3.1) still apply |
| E. iws happy path — shared session overlay | Background task's coroutine awaits `pipeline.run_tool_call` which uses session overlay; coroutine completion does NOT destroy session overlay (only exit does); changed_paths surfaced via observability |
| F. iws drain at exit | `exit_isolated_workspace` calls `BackgroundTaskManager.cancel_by_agent(agent_id, grace_s)` BEFORE `pipeline.exit`; pipeline.exit becomes pure teardown |
| G. Q4 mode-mix rejection | `enter_isolated_workspace` queries `BackgroundTaskManager.count_by_agent(agent_id)`; non-zero → `LifecycleError(kind="ephemeral_jobs_in_flight", details={"count": "N"})`; pipeline.enter unchanged |
| H. unified-protocol assertion (no public launch_bg_job etc) | UNCHANGED — actually stronger now since the methods don't exist at all (not even private) |
| **I (NEW)** | Daemon-side timeout: shell timed_out_after=Ns returns COMPLETED with `timed_out=True`; status latches to COMPLETED not CANCELLED |
| **J (NEW)** | Engine death TTL reap: simulate engine crash mid-bg-shell; daemon's `InFlightRequestRegistry.ttl_reaper_loop` cancels the in-flight Task after `EOS_INFLIGHT_TTL_S`; lease released; metric `_ttl_reaped_total` incremented |
| **K (NEW)** | Cancel-during-completion race (§7 scenario): cancel landing AFTER natural completion is a no-op; agent sees COMPLETED with real result |
| **L (NEW)** | Cancel ordering invariant: on `asyncio.CancelledError` inside `run_in_namespace`, the namespace-child PG fully exits (`waitpid` returns) BEFORE `pipeline.run_tool_call`'s `finally` calls `_destroy_with_lease_guard`. No orphan `eos-ns-child` processes observable in `ps` post-soak; no `release_lease` event precedes `namespace_child.exited` for the same `lease_id`. |
| **M (NEW)** | Wire-cancel failure tolerance: simulate `sandbox_api.cancel(...)` raising (mock-injected `ConnectionError`). Assert `BackgroundTaskManager.cancel(task_id)` STILL completes — local `asyncio.Task.cancel()` fires; agent does NOT hang; WARN log emitted; daemon TTL reaper eventually cleans up (§6 path). |
| **N (NEW)** | Multi-engine / engine-restart split-brain (§13 scenario 3). Start engine A; launch bg sandbox task for `agent_X`; kill engine A WITHOUT graceful shutdown. Start engine A'; call `enter_isolated_workspace(agent_X)`. Assert engine A' calls `api.v1.inflight_count(agent_X)` against the daemon; the daemon-side count reflects engine A's surviving task (still in `InFlightRequestRegistry` pre-TTL); Q4 rejects with `LifecycleError(kind="ephemeral_jobs_in_flight")`. Wait for TTL or trigger immediate orphan reap (heartbeat with new `engine_process_id`); re-attempt enter; assert success. |

---

## 12. Acceptance criteria

- ✅ `sandbox/daemon/service/shell_job.py` does not exist (`ls` returns "No such file or directory").
- ✅ `sandbox/daemon/service/shell_job_handler.py` does not exist.
- ✅ `grep -rn "ShellJob\|ShellJobRegistry\|shell_launch\|shell_reap\|shell_poll\|shell_cancel\|_background_jobs\|_session_jobs\|_dispatch_background_verb" backend/src/` returns ZERO hits (excluding deleted-doc references).
- ✅ `grep -rn "api.v1.shell.launch\|api.v1.shell.reap\|api.v1.shell.poll\|api.v1.shell.cancel" backend/` returns ZERO hits (also excluding historical CHANGELOG entries).
- ✅ `EphemeralPipeline.run_tool_call` body fits in ≤30 lines and contains no `if req.background:` branch (static lint enforces).
- ✅ `IsolatedPipeline.run_tool_call` body fits in ≤30 lines and contains no `if req.background:` branch.
- ✅ `sandbox/api/protocol.py` envelope carries `request_id: str` on every RPC (`grep -c request_id sandbox/api/protocol.py` ≥ 1).
- ✅ `sandbox/daemon/rpc/in_flight.py::InFlightRequestRegistry` exists and is request-keyed; has `cancel(request_id)`, `register`, `deregister`, TTL reaper.
- ✅ `sandbox/daemon/handler/cancel.py::cancel` handler exists; wire-level `api.v1.cancel` RPC works (round-trip test).
- ✅ `engine/background/manager.py::BackgroundTaskManager` gains `count_by_agent`, `cancel_by_agent`; `TrackedBackgroundTask` gains `agent_id`, `uses_sandbox`, `sandbox_id`, `sandbox_request_id`.
- ✅ `sandbox.lifecycle.enter_isolated_workspace` rejects with `LifecycleError(kind="ephemeral_jobs_in_flight")` when `BackgroundTaskManager.count_by_agent(agent_id) > 0`. The check is BEFORE `pipeline.enter`.
- ✅ `sandbox.lifecycle.exit_isolated_workspace` calls `BackgroundTaskManager.cancel_by_agent(agent_id, grace_s)` BEFORE `pipeline.exit`; reports `evicted_background_jobs` (survivor count) in `phases_ms`.
- ✅ `overlay.run_in_namespace` is cancellation-aware: on `asyncio.CancelledError`, sets `cancel_event` and SIGTERMs the namespace-child pgrp; 2s SIGKILL escalation; re-raises CancelledError.
- ✅ `tool_primitives.shell.run` accepts `cancel_event` and `pid_recorder` from `overlay.run_in_namespace`; existing `cancel_event` plumbing extracted from `shell_job.py` survives unchanged in semantics.
- ✅ `BackgroundTaskManager.cancel(task_id)` for sandbox-bound tasks sends `api.v1.cancel(request_id)` over the wire BEFORE `tracked.asyncio_task.cancel()`. Order preserves cleanup semantics.
- ✅ Terminal-status precedence (`completed > failed > cancelled`) preserved — sub-test K (race scenario §7) passes.
- ✅ Engine-death TTL reap analog preserved — sub-test J passes; metric `_ttl_reaped_total` accessible.
- ✅ Daemon-side timeout enforcement preserved — sub-test I passes; status latches to COMPLETED not CANCELLED.
- ✅ Phase 3 background-shell tier (`test_background_shell_lifetime.py`) rewritten per §11 redistribution; all sub-tests A–N green (A–H redistributed; I/J/K/L/M/N new).
- ✅ **Overview doc redactions land in commit (5):** `unify_sandbox_workspace.md` §2 Principle 1 narrative ("ShellJob owns the handle from `shell.launch` through `shell.reap`") is rewritten to coroutine-bound lifetime; `unify_sandbox_workspace.md` §1 background-shell policy table (lines 43–60) is replaced with a reference to Phase 2.5 §2 architecture diagram + §1 principles; `unify_sandbox_workspace_phase2.md` §3.1, §3.3, §4.1, §7.2 background-specific sub-sections are struck-through with redaction lines pointing to Phase 2.5. **Grep audit must pass:** `grep -rn "ShellJob\|shell_launch\|shell_reap\|_background_jobs\|_session_jobs" docs/plans/unify_sandbox_workspace*.md` returns zero hits OUTSIDE of explicit "DELETED in Phase 2.5" redaction lines.
- ✅ Wire envelope `request_id` migration: rollout-window compatibility — daemon's envelope parser accepts missing `request_id` with WARN log during the documented rollout window (one release cycle); engine-side `_raw_exec.py` ALWAYS populates `request_id` post-Phase-2.5; follow-up plan removes the legacy-tolerance path after the rollout window closes.
- ✅ Soak baseline: median bg-task launch-to-RUNNING latency ≤ Phase 2 baseline (the path is shorter — one RPC vs three).

---

## 13. ADR (delta)

**Decision (Phase 2.5):** Reverse the Phase 2 decision to model background shell as a daemon-side `ShellJob` with four `api.v1.shell.{launch,reap,poll,cancel}` verbs. Instead: background is a `ToolCallRequest` flag; the engine's existing `BackgroundTaskManager` (one source of truth) wraps `pipeline.run_tool_call` as `asyncio.Task` when an agent passes `background=true`. Overlay lease lifetime tracks the coroutine via the existing `try/finally`. Cancellation flows: `asyncio.Task.cancel()` (engine) + `api.v1.cancel(request_id)` (wire, generic over all verbs) + `CancelledError`-aware `overlay.run_in_namespace` and `tool_primitives.shell.run` (kill PG on cancel).

**Drivers:**
1. Two registries (engine + daemon ShellJob) for the same lifecycle is a smell; pick one. Engine wins because it's tool-agnostic and already drives the agent-facing background tools.
2. Principle 4 (one pipeline method, branch-free body) gets stronger when there's no verb-table-keyed background dispatch.
3. Owner-defined lifecycle (Principle 1) becomes coroutine-bound, the simplest possible binding — Python's `try/finally` is the wrapper.

**Alternatives considered:** §3 (Option α — generalize ShellJob → ToolJob; Option β — engine asyncio.cancel only, no wire cancel; Option γ — chosen).

**Consequences:**
- DELETE: `shell_job.py` (609 lines), `shell_job_handler.py` (174 lines), 4 wire RPCs (`api.v1.shell.launch/reap/poll/cancel`), Phase 2 §3.1 + §3.3 + §4.1 + §7.2 background-specific sub-sections, `is_background` branch in `tools/sandbox/shell/shell.py`.
- ADD: `request_id` wire correlation (envelope field on every RPC), `api.v1.cancel(request_id)` RPC, `InFlightRequestRegistry` (request-keyed, lighter than ShellJobRegistry — no overlay/lease accounting), cancellation-aware `overlay.run_in_namespace`, engine-layer Q4 + iws-exit drain (`BackgroundTaskManager.count_by_agent` + `cancel_by_agent`), engine-side wire-cancel hook in `BackgroundTaskManager.cancel`, heartbeat for orphan TTL detection (6.A explicit).
- Pipeline body shrinks (no `_dispatch_background_verb` branch; no `_background_jobs` field; no `_session_jobs` field).
- Wire-cancel becomes a generic primitive (`api.v1.cancel(request_id)`), reusable for any future tool that supports cancellation — including foreground (an agent could in theory cancel a slow read_file, though no UX surface exposes that today).
- Terminal-status precedence (`completed > failed > cancelled > running`) preserved unchanged at the engine layer.
- Daemon timeout enforcement preserved (no engine-side `asyncio.wait_for`).
- Phase 3 test tier `test_background_shell_lifetime.py` REWRITTEN (sub-tests A–H redistributed per §11; sub-tests I, J, K added).

**Reversibility:** Each commit (1)–(5) is independently revert-safe via `git revert <sha>`. The wire-`request_id` addition (commit 1) is the riskiest — if it interacts badly with existing engines that don't populate it, the daemon's envelope parser must tolerate missing `request_id` (use empty string + log a warning) during the rollout window.

**Follow-ups (out of this phase):**
- Expose `api.v1.cancel(request_id)` to foreground tool calls (e.g., user-cancellable long greps) once a UX surface materializes.
- Consider merging `InFlightRequestRegistry`'s TTL reaper with the heartbeat path so a single deadline drives both. Premature optimization for now.
- If transport gains stream-level cancellation primitives (HTTP/2-style), revisit whether `api.v1.cancel(request_id)` becomes redundant.

**Pre-mortem (3 scenarios with leading indicators):**

1. **Wire `request_id` is dropped by an old client AND the daemon's tolerance behavior silently accepts; cancel becomes a no-op for that client.**
   - **Leading indicator:** daemon log `request envelope missing request_id from agent=<id>` fires at INFO level; `_ttl_reaped_total` counter climbs (eventual cleanup via TTL); `api.v1.cancel` return value `cancelled=False` correlates with the same agent_id.
   - **Mitigation:** envelope parser logs WARN (not INFO) on missing request_id during the rollout window; CHANGELOG entry calls out the compat requirement; client-side `_raw_exec.py` always populates request_id (tested by unit `test_raw_exec_populates_request_id.py`).

2. **Cancellation race between `BackgroundTaskManager.cancel`'s wire-cancel + local `asyncio.Task.cancel()` produces a destroy-before-namespace-child-exits ordering, leaking the namespace child as an orphan PG.**
   - **Leading indicator:** Tier 8 soak audit JSONL shows `overlay.destroyed` event with a `pid_holder_alive=true` annotation; `ps` post-soak shows orphan processes with `eos-ns-child` cmdline; `release_lease` event fires before the matching `namespace_child.exited` event.
   - **Mitigation:** `overlay.run_in_namespace`'s `except asyncio.CancelledError` block awaits the namespace-child exit (via the same wait mechanism today's ShellJobRegistry uses — `_await_process_done` lines 398–414 of `shell_job.py`); pipeline's `finally` runs AFTER `run_in_namespace` returns (CancelledError unwind path completes); so destroy happens only after child has exited. Phase 3 sub-test L (NEW — add to §11) pins this ordering.

3. **Multi-engine / engine-restart Q4 split-brain.** Engine-layer `BackgroundTaskManager.count_by_agent` is engine-local in-process state; it cannot see another engine's in-flight tasks. Two concrete failure modes:
   - **Engine crash + restart mid-iws-session:** engine A launches a sandbox-bound bg task for `agent_X`. Engine A crashes. Engine A' (restart) attaches to the daemon; its `BackgroundTaskManager` is empty. `agent_X` re-establishes its session and calls `enter_isolated_workspace`. Engine A' sees `count_by_agent(agent_X) == 0` and proceeds; meanwhile the daemon-side `InFlightRequestRegistry` still has engine A's task (until TTL reaps it). Result: iws session opens while a ghost ephemeral task races to write OCC, violating Q4's invariant.
   - **Multi-engine-per-sandbox:** if a sandbox is ever shared between two engines (today not supported, but the architecture doesn't reject it), engine B's `BackgroundTaskManager` is blind to engine A's bg tasks. Same Q4 violation.
   - **Leading indicator:** Tier 8 soak audit JSONL shows `workspace_lifecycle.enter` succeed for an `agent_id` while the daemon's `InFlightRequestRegistry._by_request` still has at least one live entry whose `agent_id` matches. Concretely: a `workspace_lifecycle.enter.success` event with no preceding `inflight_request.deregistered` event for the same `agent_id` within the last 5 minutes (TTL window). Sub-test N (NEW — added to §11) pins this.
   - **Mitigation enacted:** the §6 heartbeat carries `engine_process_id`; the daemon triggers immediate orphan-TTL-reap when a new engine connects with a fresh `process_id` (closes the engine-restart variant). For multi-engine-per-sandbox, `enter_isolated_workspace` ADDITIONALLY consults the daemon via a lightweight `api.v1.inflight_count(agent_id)` RPC and merges its count with the engine-local count before deciding Q4 rejection. Both are implemented in commit (1) alongside the envelope change so wire-protocol changes land atomically. Pre-Phase-2.5 deployment precondition: "one engine per sandbox" is documented in §14 as a current limitation; the cross-engine RPC defends against forward incompatibility, not today's deployment.

---

## 14. What this phase does NOT touch (and current limitations)

**Deployment precondition: one engine per sandbox.** Today's deployment model assumes a single engine process attaches to a sandbox daemon at any given time. Phase 2.5's engine-layer Q4 (`count_by_agent`) is correct under this assumption. The `api.v1.inflight_count(agent_id)` daemon RPC added in commit (1) defends against forward incompatibility (multi-engine-per-sandbox); the engine MUST call it from `enter_isolated_workspace` and merge with engine-local truth before deciding Q4 rejection — see §5.6 + §13 pre-mortem scenario 3 + §11 sub-test N. If a future plan supports multi-engine-per-sandbox, no further wire changes are needed; only the `enter_isolated_workspace` merge logic needs revisiting (likely already correct as drafted).

### Out of scope

- Overlay primitives (`sandbox/overlay/{handle,lifecycle,namespace,namespace_child,kernel_mount,...}.py`) — unchanged except `namespace.py::run_in_namespace` gains cancellation forwarding (§5.5; the cleanup itself is verb-supplied, NOT a primitive-layer concern).
- OCC source-tag plumbing (Phase 2 §6.1–§6.5) — unchanged.
- `tool_primitives/{read,write,edit,grep,glob,file_ops,capture}.py` — unchanged.
- Lifecycle host API (`sandbox/lifecycle/{enter,exit}_isolated_workspace.py`) — gains pre-check + drain wiring (§5.6); the audit-event scaffold is unchanged.
- Host-path denylist (Phase 2 §7.5) — unchanged.
- Plugin-block gate (Phase 2 §13) — unchanged.
- `O_NOFOLLOW` chokepoint (Phase 1 §6.8) — unchanged.
- `manager.py` decomposition (Phase 1 §3 / Phase 2 §4.0) — unchanged.
- iws-side network policy — unchanged.

Read those sections in `unify_sandbox_workspace_phase1.md` / `unify_sandbox_workspace_phase2.md` for context; do not re-implement them here.
