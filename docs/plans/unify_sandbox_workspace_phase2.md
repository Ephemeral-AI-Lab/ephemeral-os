# Phase 2 — Unification

**Type:** Substantive — per-call ephemeral pipeline, persistent isolated pipeline, unified tool-op dispatch, lifecycle host API, agent-callable tools, plugin block, iws tool-op deletion, `manager.py` decomposition (1624 lines → 6 modules), host-path denylist, background-shell pipeline ownership (background permitted in BOTH modes; per-pipeline `_background_jobs` registry).
**Scope:** The single behavior-changing PR. Everything required to land the new agent-callable surface ships here.
**Depends on:** Phase 1 (folder reorg, overlay extraction, `OverlayHandle` + lifecycle primitives, `tool_primitives` package, parity corpus, `manager.py` extraction skeleton).
**Blocks:** Phase 3.
**Safety net:**
- **Ephemeral verbs:** Phase 1's parity corpus replays against the new pipeline; byte-equivalent against today's `daemon/handler/{read,write,edit,grep,glob,shell}.py` bodies (modulo documented OCC source-tag note).
- **Isolated verbs:** NOT covered by parity corpus. `sandbox/isolated_workspace/ops_handlers.py` is 98 lines of shell-out wrappers (`/bin/cat`, `/usr/bin/grep`, `in_ns_write.py`) returning `subprocess.run` shape — there is no byte-equivalent "before" output to compare against. iws verb migration is a **functional upgrade** to the typed-verb spec (`tool_primitives.<verb>.compute`), validated by Phase 3's `behavior_upgrade/` test tier (NOT parity).
**Atomic commit plan:** ≤8 logical commits. Suggested split: (1) `models.py` types + `WorkspacePipeline` protocol; (2) `EphemeralPipeline.run_tool_call` + per-handle lock; (3) `IsolatedPipeline` skeleton + `ops_handlers.py` deletion; (4) `run_in_namespace` + `namespace_child` two-tier dispatch; (5) OCC source-tag threading (4 helpers); (6) thin daemon handlers + `dispatch.py`; (7) `sandbox/lifecycle/` package + `sandbox/audit/lifecycle.py` + agent tools; (8) plugin-block gate + host-path denylist + RPC table deletions. Each commit runs full mock suite + parity corpus on parent SHA; rollback is `git revert <sha>` per commit.

See [`unify_sandbox_workspace.md`](unify_sandbox_workspace.md) for the overview and ADR.

---

## Goals

After Phase 2 lands:
- Every tool call in both modes flows through the same kernel-overlay path. No in-workspace / out-of-workspace branching.
- `EphemeralPipeline.run_tool_call` mounts a fresh overlay per call, runs the verb in the namespace child, captures+commits the upperdir for write-allowed verbs, then destroys the overlay.
- `IsolatedPipeline.enter` mounts an overlay once; `IsolatedPipeline.run_tool_call` runs verbs against it; `IsolatedPipeline.exit` destroys it (no commit).
- `WorkspacePipeline` protocol has one method (`run_tool_call`).
- `sandbox.lifecycle.enter_isolated_workspace` / `exit_isolated_workspace` exist as host-side coroutines using `LifecycleResultBase`. (Lives in the new `sandbox/lifecycle/` package, NOT `sandbox/api/` — `sandbox/api/` continues to house client-side wire artifacts only.)
- Agent-level `tools/isolated_workspace/{enter,exit}_isolated_workspace/` wrappers exist.
- iws `edit_file` performs real search/replace; iws `grep`/`glob` honor all options. **This is a functional upgrade, not a refactor** — today's `ops_handlers.py` shells out to `/bin/cat`/`/usr/bin/grep`/`in_ns_write.py` with `subprocess.run` shape; after Phase 2, iws verbs return the typed shape (`ReadResult`/`WriteResult`/etc.) validated by Phase 3's `behavior_upgrade/` tier.
- All 6 tool ops live on the single `api.v1.<verb>` RPC namespace. 5 iws tool-op RPCs deleted atomically.
- Plugin access blocked when an iws handle is open (with audit event emitted on fail-open path).
- `WorkspaceSession` async-CM deferred to `tests/mock/sandbox/_fixtures/workspace_session.py` test-utility until a production caller materializes (Critic must-fix #11). NOT shipped as public API.
- Host-path denylist (`/etc/`, `/var/`, `/proc/`, `/sys/`, `/boot/`) enforced inside the namespace child BEFORE the kernel call (Critic must-fix #9).
- Background shells: PERMITTED in both modes (revised after user directive; supersedes earlier Critic must-fix #4 framing). Ephemeral: `shell.launch` creates an overlay owned by a `ShellJob` in `EphemeralPipeline._background_jobs`; `shell.reap` waits for child exit, captures upperdir, OCC-commits (source=`"overlay_capture"`), destroys overlay; `shell.cancel` SIGTERM/SIGKILLs and destroys without commit. Isolated: jobs share the session overlay; `exit` drains in-flight jobs up to `grace_s` and reports `evicted_background_jobs`. Cross-mode: `enter_isolated_workspace` rejects with `ephemeral_jobs_in_flight` if the agent has live ephemeral background jobs (Q4).
- OCC disjoint-batch coalescing preserved for single-path typed writes via `source="api_write"` — threaded through all 4 helper sites (`overlay_path_changes_to_occ_changes`, `build_overlay_write_change`, `build_overlay_delete_change`, inline `SymlinkChange`/`OpaqueDirChange`).
- `OverlayHandle` idempotency wired (`_destroyed` field + per-pipeline `_handle_locks: dict[str, asyncio.Lock]` for the `_destroy_with_lease_guard` TOCTOU fix).
- `O_NOFOLLOW` enforced unconditionally via `tool_primitives.file_ops.open_no_follow` chokepoint (per-component walk, not naive last-component-only).
- `manager.py` (1624 lines) decomposed into 7 focused modules (none exceeding 400 lines): `pipeline.py` + `_lifecycle.py` + `_gc.py` + `_ttl.py` + `_quota.py` + `_runtime.py` + `_types.py`.

---

## Step 1 — Result types

**1.1.** Update `sandbox/_shared/models.py`:

```python
class Intent(str, Enum):
    READ_ONLY = "read_only"        # read_file, grep, glob
    WRITE_ALLOWED = "write_allowed"  # write_file, edit_file, shell
    LIFECYCLE = "lifecycle"        # enter_isolated_workspace, exit_isolated_workspace

@dataclass(frozen=True, kw_only=True)
class ToolCallRequest:
    request_id: str
    agent_id: str
    verb: str                # "read_file", "write_file", "edit_file", "grep", "glob", "shell"
    intent: Intent
    args: Mapping[str, object]
    actor_id: str = ""

@dataclass(frozen=True, kw_only=True)
class SandboxResultBase:
    success: bool
    workspace: Literal["ephemeral", "isolated"] = "ephemeral"
    timings: dict[str, float] = field(default_factory=dict)
    conflict: ConflictInfo | None = None
    conflict_reason: str | None = None
    changed_paths: list[str] = field(default_factory=list)
```

**1.2.** Add `LifecycleResultBase` + `LifecycleError` (separate from `SandboxResultBase` — lifecycle errors are categorical mismatches, not OCC conflicts):

```python
@dataclass(frozen=True, kw_only=True)
class LifecycleError:
    kind: str               # "already_open", "quota_exceeded", "host_ram_pressure"
    message: str = ""
    details: dict[str, str] = field(default_factory=dict)

@dataclass(frozen=True, kw_only=True)
class LifecycleResultBase:
    success: bool = True
    timings: dict[str, float] = field(default_factory=dict)
    error: LifecycleError | None = None

@dataclass(frozen=True, kw_only=True)
class EnterIsolatedWorkspaceRequest(SandboxRequestBase):
    layer_stack_root: str

@dataclass(frozen=True, kw_only=True)
class EnterIsolatedWorkspaceResult(LifecycleResultBase):
    manifest_version: str = ""
    manifest_root_hash: str = ""

@dataclass(frozen=True, kw_only=True)
class ExitIsolatedWorkspaceRequest(SandboxRequestBase):
    grace_s: float = 5.0

@dataclass(frozen=True, kw_only=True)
class ExitIsolatedWorkspaceResult(LifecycleResultBase):
    evicted_upperdir_bytes: int = 0
    lifetime_s: float = 0.0
    phases_ms: dict[str, float] = field(default_factory=dict)
```

→ **Verify:** mypy clean.

---

## Step 2 — `WorkspacePipeline` protocol

**2.1.** Create `sandbox/_shared/workspace_pipeline.py`:

```python
class WorkspacePipeline(Protocol):
    """Both EphemeralPipeline and IsolatedPipeline implement this single method.

    The pipeline owns its own overlay lifecycle. Ephemeral creates+destroys per call;
    isolated creates at enter and destroys at exit (separate methods, not on this protocol).
    """
    async def run_tool_call(self, req: ToolCallRequest) -> ToolCallResult: ...
```

→ **Verify:** mypy clean.

---

## Step 3 — `EphemeralPipeline.run_tool_call` (full per-call lifecycle inline)

**3.1.** Rewrite `sandbox/ephemeral_workspace/pipeline.py::EphemeralPipeline` to implement `WorkspacePipeline`:

```python
# Verbs that route through the background-shell lifecycle (overlay outlives
# the run_tool_call invocation; owned by ShellJob in _background_jobs).
# All other verbs follow the per-call foreground lifecycle.
_BACKGROUND_SHELL_VERBS: frozenset[str] = frozenset({
    "shell_launch", "shell_reap", "shell_poll", "shell_cancel",
})


class EphemeralPipeline:
    def __init__(self, *, layer_stack, occ_client, workspace_root="/testbed"):
        self._layer_stack = layer_stack
        self._occ = occ_client
        self._workspace_root = workspace_root
        # Per-pipeline guard against double-release. Used together with
        # _handle_locks below to prevent the asyncio TOCTOU race where two
        # coroutines (main call's finally + shell-job reaper) both pass
        # _destroyed=False before either awaits overlay.destroy.
        self._released_lease_ids: set[str] = set()
        # Per-handle asyncio.Lock keyed by lease_id. Created lazily on first
        # destroy attempt; popped after destroy completes so the dict doesn't
        # grow unbounded across the pipeline lifetime.
        self._handle_locks: dict[str, asyncio.Lock] = {}
        # Background-shell registry (Q2: pipeline-owned). Keys: job_id from
        # _launch_bg_job (e.g. "shell-<uuid12>"). Each ShellJob owns one
        # OverlayHandle for the duration shell_launch → shell_reap/shell_cancel;
        # the overlay is destroyed in _reap_bg_job / _cancel_bg_job, NOT in the
        # foreground _run_foreground path. Unbounded (Q3) — agent self-regulates;
        # gauge exported for observability.
        self._background_jobs: dict[str, ShellJob] = {}
        # Per-agent index for the "ephemeral_jobs_in_flight" Q4 check that
        # IsolatedPipeline.enter consults via the pipeline's introspection API.
        self._jobs_by_agent: dict[str, set[str]] = {}

    async def run_tool_call(self, req: ToolCallRequest) -> ToolCallResult:
        # Unified entry: foreground AND background-shell verbs route through
        # this single method (Principle 4 — protocol has ONE method). The
        # request's verb selects the lifecycle; the substrate (overlay.create
        # / capture / destroy) is the same. Foreground = per-call lifecycle.
        # Background = per-ShellJob lifecycle (overlay outlives this call).
        if req.verb in _BACKGROUND_SHELL_VERBS:
            return await self._dispatch_background_verb(req)
        return await self._run_foreground(req)

    async def _dispatch_background_verb(self, req: ToolCallRequest) -> ToolCallResult:
        # All four background verbs share the same substrate (overlay primitives,
        # ShellJob registry, _destroy_with_lease_guard) but differ in lifecycle:
        #   shell_launch  → create overlay + register ShellJob (overlay survives call)
        #   shell_reap    → look up handle by job_id, wait + capture + commit + destroy
        #   shell_poll    → look up handle, non-blocking status (no destroy, no commit)
        #   shell_cancel  → look up handle, kill PG, destroy overlay (no commit)
        if req.verb == "shell_launch":
            return await self._launch_bg_job(req)
        if req.verb == "shell_reap":
            return await self._reap_bg_job(req)
        if req.verb == "shell_poll":
            return await self._poll_bg_job(req)
        if req.verb == "shell_cancel":
            return await self._cancel_bg_job(req)
        raise AssertionError(f"unreachable: {req.verb}")  # pragma: no cover

    async def _run_foreground(self, req: ToolCallRequest) -> ToolCallResult:
        # Per-call overlay lifecycle: create → run → (capture+commit if write) → destroy.
        handle = await overlay.create(
            self._layer_stack,
            agent_id=req.agent_id,
            workspace_root=self._workspace_root,
        )
        try:
            result = await overlay.run_in_namespace(handle, req)
            if req.intent == Intent.WRITE_ALLOWED:
                changes = await overlay.capture_changes(handle)
                # Single-path typed writes emit api_write source so CommitQueue
                # _disjoint_batches coalescing still applies. Multi-path writes
                # (shell) emit overlay_capture (cross-path atomicity required).
                # Tag is threaded through 4 helper sites in OCC (see §6.1).
                source = (
                    "api_write"
                    if req.verb in {"write_file", "edit_file"}
                    else "overlay_capture"
                )
                result = await self._commit_and_attach(
                    changes,
                    base_version=handle.snapshot_version,
                    source=source,
                    result=result,
                )
            return result
        finally:
            await self._destroy_with_lease_guard(handle)

    # ---- Background-shell internal helpers (Q1–Q5 decisions; Overview §1 table) ----
    # These are PRIVATE — dispatched via run_tool_call by verb name. The
    # WorkspacePipeline protocol surface stays at ONE method.

    async def _launch_bg_job(self, req: ToolCallRequest) -> ToolCallResult:
        """Create overlay, fork child PG, register ShellJob, return job_id.
        Overlay survives until reap_background_job or cancel_background_job."""
        handle = await overlay.create(
            self._layer_stack,
            agent_id=req.agent_id,
            workspace_root=self._workspace_root,
        )
        try:
            # tool_primitives.shell.run with background=True returns a PG
            # handle without waiting for child exit; ShellJob stores it.
            child = await overlay.spawn_background_in_namespace(handle, req)
            job_id = f"shell-{uuid4().hex[:12]}"
            job = ShellJob(
                job_id=job_id,
                agent_id=req.agent_id,
                handle=handle,
                child_pg=child.pgrp,
                stdout_ref=child.stdout_ref,
                stderr_ref=child.stderr_ref,
                base_version=handle.snapshot_version,
                started_at=monotonic_now(),
            )
            self._background_jobs[job_id] = job
            self._jobs_by_agent.setdefault(req.agent_id, set()).add(job_id)
            return ToolCallResult(
                success=True,
                workspace="ephemeral",
                job_id=job_id,
                lease_id=handle.lease_id,
            )
        except BaseException:
            # If anything between overlay.create and registry insert fails,
            # tear down the orphan handle before propagating.
            await self._destroy_with_lease_guard(handle)
            raise

    async def _reap_bg_job(self, req: ToolCallRequest) -> ToolCallResult:
        """Wait for child exit, capture upperdir, OCC-commit (source=overlay_capture
        per Q5), destroy overlay. Best-effort OCC (Q1) — conflict surfaced to agent."""
        job_id = str(req.args["job_id"])
        timeout_s = float(req.args.get("timeout_s", 600.0))
        job = self._background_jobs.get(job_id)
        if job is None or job.agent_id != req.agent_id:
            return shell_error_result(kind="shell_job_not_found", job_id=job_id)
        try:
            status = await wait_for_child(job.child_pg, timeout=timeout_s)
            changes = await overlay.capture_changes(job.handle)
            # Q5: always overlay_capture for background-shell commits.
            # Q1: best-effort — OCC raises ConflictError if stale-snapshot
            # changes overlap with committed-in-meantime changes; agent
            # receives conflict result and decides whether to retry.
            try:
                commit_result = await self._commit_and_attach(
                    changes,
                    base_version=job.base_version,
                    source="overlay_capture",
                    result=ShellResult(success=True, status=status, ...),
                )
            except OCCConflictError as e:
                commit_result = shell_conflict_result(e, job_id=job_id)
            return commit_result
        finally:
            self._background_jobs.pop(job_id, None)
            self._jobs_by_agent.get(job.agent_id, set()).discard(job_id)
            await self._destroy_with_lease_guard(job.handle)

    async def _poll_bg_job(self, req: ToolCallRequest) -> ToolCallResult:
        """Non-blocking status check. Does NOT destroy overlay or commit."""
        job_id = str(req.args["job_id"])
        job = self._background_jobs.get(job_id)
        if job is None or job.agent_id != req.agent_id:
            return shell_error_result(kind="shell_job_not_found", job_id=job_id)
        return shell_poll_result(job_id=job_id, status=peek_status(job.child_pg))

    async def _cancel_bg_job(self, req: ToolCallRequest) -> ToolCallResult:
        """SIGTERM → grace → SIGKILL the child PG; destroy overlay; NO commit."""
        job_id = str(req.args["job_id"])
        reason = str(req.args.get("reason", ""))
        job = self._background_jobs.get(job_id)
        if job is None or job.agent_id != req.agent_id:
            return shell_error_result(kind="shell_job_not_found", job_id=job_id)
        try:
            await terminate_pg(job.child_pg, reason=reason)
        finally:
            self._background_jobs.pop(job_id, None)
            self._jobs_by_agent.get(job.agent_id, set()).discard(job_id)
            await self._destroy_with_lease_guard(job.handle)
        return shell_cancel_result(job_id=job_id, reason=reason)

    def get_agent_background_jobs(self, agent_id: str) -> set[str]:
        """Used by IsolatedPipeline.enter for Q4 ephemeral_jobs_in_flight check.
        Lock-free read; the empty case is the common case. NOT a tool-call;
        this is pipeline-internal introspection (similar to IsolatedPipeline.
        get_handle)."""
        return set(self._jobs_by_agent.get(agent_id, ()))

    async def startup_gc(self) -> None:
        """Q2: per-pipeline orphan sweep on init. Replaces global
        daemon/service/shell_job.py::startup_gc. Scans run_dir for orphan
        leases (no live ShellJob owns them), releases leases, removes dirs.
        Idempotent; safe to call once at pipeline __init__. NOT a tool-call;
        invoked by the daemon bootstrap, not via run_tool_call."""
        ...  # implementation in pipeline.py; calls overlay/scratch.py helpers

    # ---- destroy chokepoint (TOCTOU fix; unchanged from prior iteration) ----

    def _lock_for(self, handle: OverlayHandle) -> asyncio.Lock:
        """Lazy per-handle lock. Pipeline-owned dict (not handle field) because
        the handle is shared by reference across the shell-job reaper and the
        main call; a dict-owned lock survives even if a frozen handle is later
        adopted (per principle 3 wording in Overview §2)."""
        lock = self._handle_locks.get(handle.lease_id)
        if lock is None:
            lock = self._handle_locks[handle.lease_id] = asyncio.Lock()
        return lock

    async def _destroy_with_lease_guard(self, handle: OverlayHandle) -> None:
        """Idempotent destroy. Safe across concurrent asyncio tasks.

        Fixes the TOCTOU race (Planner F.5 / Architect F.5 / Critic must-fix #5):
        without the lock, two coroutines can both read _destroyed=False before
        either awaits overlay.destroy → double umount → EBUSY/EINVAL.
        """
        async with self._lock_for(handle):
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
                await overlay.destroy(handle)  # internally sets handle._destroyed = True
            finally:
                self._handle_locks.pop(handle.lease_id, None)
```

**3.2.** Delete the temporary `sandbox/ephemeral_workspace/_execute_command.py` (introduced in Phase 1 §5.2). Its logic is now subsumed by `run_tool_call`.

**3.3.** Restructure `sandbox/ephemeral_workspace/shell_job.py` (Phase 1 §2.3 moved it from `daemon/service/`). The global `ShellJobRegistry` singleton (`get_shell_job_registry()`) is removed; `ShellJob` dataclass becomes a per-pipeline value held in `EphemeralPipeline._background_jobs`. iws session-scoped jobs are held in the `OverlayHandle`'s per-session state (added to `isolated_workspace/_types.py::IsolatedWorkspaceHandle._background_jobs: dict[str, ShellJob]`). `shell_job_handler.py` becomes a thin RPC adapter that resolves the pipeline via `resolve_pipeline(agent_id)` and dispatches to `pipeline.{launch,reap,poll,cancel}_background_job` — see §7.5.

→ **Verify:** parity corpus replay passes byte-equivalently for **ephemeral-mode foreground verbs only** (modulo OCC source-tag note). iws verbs follow the typed-verb spec; iws is NOT covered by the parity corpus and is validated by Phase 3's `behavior_upgrade/` tier. Background-shell lifecycle (launch/reap/cancel/poll) is NOT covered by the parity corpus (today's `ShellJobRegistry` global state has no comparable per-pipeline analog) and is validated by Phase 3 §6.6 sub-tests A–D + the new pipeline_lifecycle test `test_ephemeral_background_job_lease_lifetime.py`.

---

## Step 4 — `IsolatedPipeline` — enter / run_tool_call / exit

**4.0. `manager.py` decomposition (verified: 1624 lines on disk; Planner's "1016" was wrong; Critic must-fix #3).**

Today's `sandbox/isolated_workspace/manager.py` contains: `IsolatedWorkspaceManager`, `_LinuxRuntime` (~600 lines including `spawn_ns_holder`, `mount_overlay`, `configure_dns`, `signal_net_ready`, `create_cgroup`, `freeze`, `kill_holder`, `run_in_handle`), `_PhaseTimer`, `_ManagerConfig`, `IsolatedWorkspaceError`, `IsolatedWorkspaceHandle`, `_check_host_capacity`, `_ttl_loop`, `startup_gc`, `_reap_orphans`, `_release_orphan_lease`, `_reap_orphan_cgroup`, `_unfreeze_and_kill`, `_wire_handle`, `_teardown`, `_rollback_partial`, `_compute_host_budget`, `_read_manager_json`. The "rewrite into a 30-line skeleton" was the largest understated scope-bomb in the plan.

Phase 1 §3.1 lands the file split (extract-only, no behavior change). Phase 2 §4.1 then rewrites the now-isolated `pipeline.py` surface to implement `WorkspacePipeline`.

**Target post-split layout** (none of these exceeds ~400 lines):
- `sandbox/isolated_workspace/pipeline.py` — public surface: `IsolatedPipeline` class, `enter`/`run_tool_call`/`exit`/`get_handle`.
- `sandbox/isolated_workspace/_types.py` — `IsolatedWorkspaceError`, `IsolatedWorkspaceHandle`, `_ManagerConfig`, `_PhaseTimer`.
- `sandbox/isolated_workspace/_lifecycle.py` — `_wire_handle`, `_teardown`, `_rollback_partial`.
- `sandbox/isolated_workspace/_gc.py` — `startup_gc`, `_reap_orphans`, `_release_orphan_lease`, `_reap_orphan_cgroup`, `_unfreeze_and_kill`.
- `sandbox/isolated_workspace/_ttl.py` — `_ttl_loop`, `ttl_sweep`.
- `sandbox/isolated_workspace/_quota.py` — `_check_host_capacity`, `_compute_host_budget`, `_read_manager_json`.
- `sandbox/isolated_workspace/_runtime.py` — `_LinuxRuntime` (the bulk; ~600 lines alone).

Acceptance check: post-Phase-2, `find sandbox/isolated_workspace -name "*.py" -exec wc -l {} \;` shows no file >400 lines.

**4.1.** With the split in place (from Phase 1 §3.1), rewrite `sandbox/isolated_workspace/pipeline.py::IsolatedPipeline` to implement `WorkspacePipeline`:

```python
class IsolatedPipeline:
    def __init__(self, *, layer_stack, ephemeral_pipeline, workspace_root="/testbed"):
        self._layer_stack = layer_stack
        # Reference to the EphemeralPipeline so enter() can check Q4
        # (ephemeral_jobs_in_flight) before opening a session.
        self._ephemeral = ephemeral_pipeline
        self._workspace_root = workspace_root
        self._sessions: dict[str, OverlayHandle] = {}
        # Per-session background jobs — keyed by agent_id then job_id.
        # Jobs share the session's overlay; no separate per-job overlay.
        self._session_jobs: dict[str, dict[str, ShellJob]] = {}
        self._released_lease_ids: set[str] = set()
        self._handle_locks: dict[str, asyncio.Lock] = {}  # same TOCTOU fix as ephemeral
        self._lock = asyncio.Lock()  # serializes enter/exit per agent

    async def enter(self, agent_id: str, config: IsolatedConfig) -> OverlayHandle:
        async with self._lock:
            if agent_id in self._sessions:
                raise LifecycleError(kind="already_open", ...)
            # Q4: reject if agent has live ephemeral background jobs.
            # The two registries are mutually exclusive: an agent is in one
            # mode at a time. Caller must reap/cancel ephemeral jobs first.
            stragglers = self._ephemeral.get_agent_background_jobs(agent_id)
            if stragglers:
                raise LifecycleError(
                    kind="ephemeral_jobs_in_flight",
                    details={"job_ids": sorted(stragglers)},
                )
            handle = await overlay.create(
                self._layer_stack,
                agent_id=agent_id,
                workspace_root=self._workspace_root,
                network=config.network,
            )
            # _wire_handle BEFORE insert (preserves manager.py:671,679 invariant
            # extracted to _lifecycle.py per §4.0)
            self._sessions[agent_id] = handle
            self._session_jobs[agent_id] = {}
            return handle

    async def run_tool_call(self, req: ToolCallRequest) -> ToolCallResult:
        # Unified entry: same protocol as EphemeralPipeline. Foreground verbs
        # run against the session overlay; background-shell verbs operate on
        # session-scoped ShellJobs that share that same overlay (no per-job
        # mount). Protocol stays at ONE method (Principle 4).
        handle = self._sessions.get(req.agent_id)
        if handle is None:
            raise RuntimeError(f"no isolated session for agent {req.agent_id}")
        if req.verb in _BACKGROUND_SHELL_VERBS:
            return await self._dispatch_background_verb_iws(req, handle)
        return await self._run_foreground_iws(req, handle)

    async def _run_foreground_iws(
        self, req: ToolCallRequest, handle: OverlayHandle,
    ) -> ToolCallResult:
        # NO OCC commit — upperdir accumulates across calls and is discarded
        # at exit. capture_changes IS called (for changed_paths observability
        # per Phase 3 §6A.6 behavior_upgrade tier) but its output is NOT
        # passed to OCC.
        result = await overlay.run_in_namespace(handle, req)
        if req.intent == Intent.WRITE_ALLOWED:
            changes = await overlay.capture_changes(handle)
            result = result.with_changed_paths([c.path for c in changes])
        return result

    async def _dispatch_background_verb_iws(
        self, req: ToolCallRequest, handle: OverlayHandle,
    ) -> ToolCallResult:
        # Same verb table as EphemeralPipeline._dispatch_background_verb, but
        # backed by session-scoped ShellJob storage and the SHARED session
        # overlay. No per-job mount; no per-job destroy. Reap does NOT commit
        # (iws never commits). Cancel kills the PG but leaves the overlay
        # alone for other jobs / future tool calls in the same session.
        jobs = self._session_jobs[req.agent_id]
        if req.verb == "shell_launch":
            child = await overlay.spawn_background_in_namespace(handle, req)
            job_id = f"shell-{uuid4().hex[:12]}"
            jobs[job_id] = ShellJob(
                job_id=job_id, agent_id=req.agent_id, handle=handle,
                child_pg=child.pgrp, stdout_ref=child.stdout_ref,
                stderr_ref=child.stderr_ref, base_version=None,  # iws no-commit
                started_at=monotonic_now(),
            )
            return ToolCallResult(success=True, workspace="isolated", job_id=job_id)
        # reap / poll / cancel: look up the session-scoped job; NO destroy
        # of the shared overlay; NO commit on reap.
        job_id = str(req.args["job_id"])
        job = jobs.get(job_id)
        if job is None:
            return shell_error_result(kind="shell_job_not_found", job_id=job_id)
        if req.verb == "shell_reap":
            status = await wait_for_child(job.child_pg, timeout=float(req.args.get("timeout_s", 600.0)))
            changes = await overlay.capture_changes(handle)
            jobs.pop(job_id, None)
            return ShellResult(
                success=True, status=status, workspace="isolated",
                changed_paths=[c.path for c in changes],  # observability only
            )
        if req.verb == "shell_poll":
            return shell_poll_result(job_id=job_id, status=peek_status(job.child_pg))
        # shell_cancel
        try:
            await terminate_pg(job.child_pg, reason=str(req.args.get("reason", "")))
        finally:
            jobs.pop(job_id, None)
        return shell_cancel_result(job_id=job_id, reason=str(req.args.get("reason", "")))

    async def exit(self, agent_id: str, grace_s: float = 5.0) -> ExitIsolatedWorkspaceResult:
        async with self._lock:
            handle = self._sessions.get(agent_id)
            if handle is None:
                return ExitIsolatedWorkspaceResult(
                    success=False,
                    error=LifecycleError(kind="not_open", ...),
                )
            # remove-before-teardown (preserves manager.py:775-786 invariant
            # extracted to _lifecycle.py per §4.0)
            del self._sessions[agent_id]
            evicted_bytes = await overlay.upperdir_size(handle)
            # Background-shell drain: wait up to grace_s for jobs to complete,
            # then force-kill via _gc.py::_unfreeze_and_kill. Count survivors
            # for observability. Jobs are session-scoped — `_session_jobs.pop`
            # is the registry hand-over to the drainer.
            jobs = self._session_jobs.pop(agent_id, {})
            evicted_jobs = await self._drain_background_jobs(jobs, grace_s)
            await self._destroy_with_lease_guard(handle)
            return ExitIsolatedWorkspaceResult(
                success=True,
                evicted_upperdir_bytes=evicted_bytes,
                phases_ms={"drain_background_jobs": ..., "evicted_background_jobs": evicted_jobs},
                ...
            )

    def get_handle(self, agent_id: str) -> OverlayHandle | None:
        """Lock-free dict read — used by daemon/dispatch.py::resolve_pipeline."""
        return self._sessions.get(agent_id)

    async def _drain_background_jobs(
        self, jobs: dict[str, ShellJob], grace_s: float,
    ) -> int:
        """Wait up to grace_s for jobs to finish; SIGTERM/SIGKILL survivors.
        Returns number of jobs that had to be force-killed. Called only from
        exit() with the popped-out session-jobs dict (registry has already
        been removed from _session_jobs to prevent concurrent launches)."""
        ...  # implementation in pipeline.py; delegates PG signaling to _gc.py

    async def _destroy_with_lease_guard(self, handle: OverlayHandle) -> None:
        """Same per-handle-lock TOCTOU fix as EphemeralPipeline (Critic must-fix #5)."""
        # Same body as EphemeralPipeline._destroy_with_lease_guard (Step 3.1).
        ...
```

**4.2.** Delete `sandbox/isolated_workspace/ops_handlers.py` entirely (98 lines of shell-out wrappers — verified). iws tool-op handling collapses into `IsolatedPipeline.run_tool_call → overlay.run_in_namespace → tool_primitives.<verb>.compute`. **This is a functional upgrade**: today's `read_file` shells to `/bin/cat`, `grep` shells to `/usr/bin/grep -r -n` (ignoring `mode`/`case_insensitive`/`include_pattern`/`multiline`), `edit_file` dispatches to the same body as `write_file` (full overwrite, NOT search/replace). Phase 3's `behavior_upgrade/` tier asserts the new typed-shape behavior.

→ **Verify:** iws tier 1–9 tests pass after fixture migration to typed shape; `_wire_handle` ordering invariant unchanged (covered by Phase 3 §6.2 `test_get_handle_returns_none_during_wire_and_teardown.py`); post-Phase-2 file size check `find sandbox/isolated_workspace -name "*.py" -exec wc -l {} \;` shows no file >400 lines.

---

## Step 5 — `overlay.run_in_namespace` + two-tier verb dispatch

**5.1.** Add `sandbox/overlay/namespace.py::run_in_namespace`:

```python
async def run_in_namespace(handle: OverlayHandle, req: ToolCallRequest) -> ToolCallResult:
    """Host-side coordinator. Forks into handle.namespace; child runs verb."""
    # Hand handle + req to the child via pipe; await result
    ...
```

**5.2.** Extend `sandbox/overlay/namespace_child.py` with two-tier verb dispatcher:

```python
def main():
    """Child entry. Mounts overlay, chdir, dispatches verb."""
    handle, req = receive_from_parent()
    mount_overlay(...)
    os.chdir(handle.workspace_root)

    if req.verb == "shell":
        # Shell uses its own signature for cancel/pgrp/stdout-ref/stderr-ref/job_id
        from sandbox._shared.tool_primitives import shell
        result = shell.run(
            req.args,
            cancel_event=...,
            stdout_ref=req.args["stdout_ref"],
            stderr_ref=req.args["stderr_ref"],
            pid_recorder=...,
        )
    else:
        # Uniform shape: read/write/edit/grep/glob
        from sandbox._shared.tool_primitives import VERB_TABLE
        compute_fn = VERB_TABLE[req.verb]  # → tool_primitives.<verb>.compute
        result = compute_fn(req.args)

    send_to_parent(result)
```

**5.3.** Add `VERB_TABLE` in `sandbox/_shared/tool_primitives/__init__.py`:

```python
from sandbox._shared.tool_primitives import read, write, edit, grep, glob

VERB_TABLE = {
    "read_file": read.compute,
    "write_file": write.compute,
    "edit_file": edit.compute,
    "grep": grep.compute,
    "glob": glob.compute,
    # NOTE: "shell" is NOT in this table — different signature, dispatched separately
}
```

→ **Verify:** all 5 uniform verbs dispatch through VERB_TABLE; shell dispatches through `shell.run`; static lint (Step 9.2 + Phase 3 §4.4) catches naive `os.open(path, flags|O_NOFOLLOW)` patterns that bypass `file_ops.open_no_follow`'s per-component walk; Phase 3 §4.5 intermediate-symlink test fails BEFORE this lint lands.

---

## Step 6 — OCC source-tag round-trip

**Ground truth (Architect F.8 + Critic must-fix #7 + verified by reading `occ/overlay_change_conversion.py`):** the function does NOT take a `source` parameter today. It calls `build_overlay_write_change(...)` (no `source` kwarg — hardcoded `"overlay_capture"` via `occ/changeset.py:272`) and `build_overlay_delete_change(...)` (same — `changeset.py:284`). The `SymlinkChange` and `OpaqueDirChange` branches construct inline with `source="overlay_capture"` hardcoded. Threading `source` requires edits at **4 helper sites**, not 1.

**Approach chosen: signature flip with default preservation.** Each of the 4 helpers gains a `source: str = "overlay_capture"` kwarg. Default preserves today's behavior; `EphemeralPipeline` passes `source="api_write"` for single-path typed writes.

**6.1.** Update `sandbox/occ/overlay_change_conversion.py::overlay_path_changes_to_occ_changes` (Site 1 of 4):

```python
def overlay_path_changes_to_occ_changes(
    path_changes: Sequence[OverlayPathChange],
    *,
    source: str = "overlay_capture",  # NEW parameter
) -> tuple[Change, ...]:
    """Convert policy-blind path changes into typed OCC mutations.

    `source` controls CommitQueue._disjoint_batches coalescing:
    - "overlay_capture" (default): excluded from disjoint batching (multi-path
      shell writes need cross-path atomicity).
    - "api_write": eligible for disjoint batching (single-path typed writes
      keep concurrent-disjoint-writer fast-path).
    """
    changes: list[Change] = []
    for pc in path_changes:
        if pc.kind == "write":
            changes.append(build_overlay_write_change(
                path=pc.path, content_path=pc.content_path,
                precomputed_hash=pc.final_hash, source=source,
            ))
        elif pc.kind == "delete":
            changes.append(build_overlay_delete_change(path=pc.path, source=source))
        elif pc.kind == "symlink":
            changes.append(SymlinkChange(
                path=pc.path, target=os.readlink(pc.content_path), source=source,
            ))
        elif pc.kind == "opaque_dir":
            changes.append(OpaqueDirChange(
                path=pc.path,
                kept_children=frozenset(_kept_children_for(pc.path, path_changes)),
                source=source,
            ))
    return tuple(changes)
```

**6.2.** Update `sandbox/occ/changeset.py::build_overlay_write_change` (Site 2 of 4): add `source: str = "overlay_capture"` kwarg; pass through to the `WriteChange` constructor (replacing the hardcoded `"overlay_capture"`).

**6.3.** Update `sandbox/occ/changeset.py::build_overlay_delete_change` (Site 3 of 4): add `source: str = "overlay_capture"` kwarg; pass through to `DeleteChange`.

**6.4.** Inline constructors in `overlay_path_changes_to_occ_changes` for `SymlinkChange` and `OpaqueDirChange` (Site 4 — bundled because they share the call site): replace the hardcoded `source="overlay_capture"` with `source=source` per §6.1's snippet above.

**6.5.** `EphemeralPipeline._commit_and_attach` (Step 3.1) determines `source` as follows:
```python
single_path = len({c.path for c in changes}) == 1
source = "api_write" if (req.verb in {"write_file", "edit_file"} and single_path) else "overlay_capture"
```
Single-path means `len({c.path for c in changes}) == 1` (per Architect Principle 7 leak finding: a typed `write_file` could touch multiple paths in pathological cases — symlink resolution to a directory, etc.). The single-path check guards against false-positive coalescing.

→ **Verify:** Phase 3 §6.1 `test_typed_write_coalesces_with_overlay_capture.py` exercises all 4 helper sites: write, delete, symlink, opaque_dir. Field-level assertion: every `Change.source` in the resulting tuple matches the expected mode.

---

## Step 7 — Thin daemon handlers + dispatch

**7.1.** Create `sandbox/daemon/dispatch.py`:

```python
def resolve_pipeline(agent_id: str) -> WorkspacePipeline:
    """Returns IsolatedPipeline if agent has an open iws session, else EphemeralPipeline."""
    iws = isolated_workspace.get_active_pipeline()
    if iws is not None and iws.get_handle(agent_id) is not None:
        return iws
    return ephemeral_workspace.get_active_pipeline()
```

**7.2.** Rewrite each `sandbox/daemon/handler/{read,write,edit,grep,glob,shell}.py` to ~15 lines:

```python
# sandbox/daemon/handler/read.py
async def read_file(args: dict[str, object]) -> dict[str, object]:
    agent_id = require_arg(args, "agent_id")
    req = ToolCallRequest(
        request_id=args.get("request_id") or uuid4().hex,
        agent_id=agent_id,
        verb="read_file",
        intent=Intent.READ_ONLY,
        args=args,
    )
    pipeline = resolve_pipeline(agent_id)
    result = await pipeline.run_tool_call(req)
    return result.to_dict()
```

Identical shape for write/edit/grep/glob with `intent=Intent.READ_ONLY` or `WRITE_ALLOWED`. Shell uses `WRITE_ALLOWED`.

**Background-shell handlers** (replaces `daemon/service/shell_job_handler.py` direct dispatch). The wire RPC names stay `api.shell.launch` / `api.shell.reap` / `api.shell.poll` / `api.shell.cancel` (backward compat with existing engine clients). The handler maps RPC name → in-memory verb name and routes through the pipeline:

```python
# sandbox/daemon/handler/shell.py
_SHELL_RPC_TO_VERB = {
    "api.v1.shell":     ("shell",         Intent.WRITE_ALLOWED),  # foreground (unchanged)
    "api.shell.launch": ("shell_launch",  Intent.WRITE_ALLOWED),  # creates overlay + ShellJob
    "api.shell.reap":   ("shell_reap",    Intent.WRITE_ALLOWED),  # waits + commits (ephemeral)
    "api.shell.poll":   ("shell_poll",    Intent.READ_ONLY),      # status only
    "api.shell.cancel": ("shell_cancel",  Intent.WRITE_ALLOWED),  # side effect (kills PG)
}

async def shell(rpc_name: str, args: dict[str, object]) -> dict[str, object]:
    agent_id = require_arg(args, "agent_id")
    verb, intent = _SHELL_RPC_TO_VERB[rpc_name]
    req = ToolCallRequest(
        request_id=args.get("request_id") or uuid4().hex,
        agent_id=agent_id,
        verb=verb,
        intent=intent,
        args=args,
    )
    pipeline = resolve_pipeline(agent_id)
    return (await pipeline.run_tool_call(req)).to_dict()
```

`resolve_pipeline(agent_id)` chooses between `IsolatedPipeline` (if agent has open iws session) and `EphemeralPipeline`. The pipeline then internally dispatches by verb. This collapses today's three-stop wiring (`api/tool/shell.py` → `shell_job_handler.py` → `ShellJobRegistry`) to one stop (handler → pipeline) with no global registry. **Critic punchlist P1 closes naturally** — the rejection-vs-route-vs-refactor question disappears because there is no special-case route; background and foreground both flow through the same dispatcher.

**7.3.** Delete the per-handler helpers that no longer exist:
- `daemon/handler/read.py::_read_in_workspace`, `_read_out_of_workspace`
- `daemon/handler/write.py::_write_in_workspace`, `_write_out_of_workspace`, `_atomic_overwrite_no_follow`
- `daemon/handler/edit.py::_edit_in_workspace`, `_edit_out_of_workspace`, `_apply_edits`
- `daemon/handler/grep.py::_grep_sync` body (lives in `tool_primitives/grep.py` now)
- `daemon/handler/glob.py::_glob_sync` body (lives in `tool_primitives/glob.py` now)
- `daemon/handler/overlay.py` entirely (replaced by `daemon/handler/shell.py`)
- `daemon/service/shell_job_handler.py::{launch_handler, reap_handler, poll_handler, cancel_handler}` — replaced by `daemon/handler/shell.py` per-RPC routing above.
- `daemon/service/shell_job.py::ShellJobRegistry` global singleton + `get_shell_job_registry()` — replaced by per-pipeline `_background_jobs` / `_session_jobs` (Phase 2 §3.3). `ShellJob` dataclass remains (now imported from `sandbox.ephemeral_workspace.shell_job`); the registry surface is gone.
- `sandbox/api/tool/shell.py::_shell_background_dispatch` — engine-side two-RPC orchestration moves into the engine's own tool wiring (no daemon code change; the wire still sees `api.shell.launch` + `api.shell.reap`, just dispatched through `pipeline.run_tool_call` on the daemon side). If the engine wants to keep its convenience wrapper that does launch+reap in one client-side call, that's an engine concern.

**7.4.** Delete `daemon/request_context.py::classify_path`, `ClassifiedPath`, `read_bytes_no_follow`, `write_text_no_follow`, `_open_no_follow`, `_o_no_follow`.
- **`_open_no_follow` MOVES to `tool_primitives/file_ops.py::open_no_follow`** (per Phase 1 §6.8), preserving the per-component walk semantics. Naive last-component-only `os.open(path, flags|O_NOFOLLOW)` is FORBIDDEN by the lint (Step 9.2). See Architect F.6 / Critic must-fix #15.
- `classify_path` and `_xxx_in_workspace`/`_xxx_out_of_workspace` helpers are deleted with no replacement — the overlay's pass-through layer handles non-workspace paths uniformly (and the new denylist in §7.5 rejects host-modifying writes).

**7.5.** Host-path denylist (Critic must-fix #9 / Architect F.5 SECURITY question). Today's `_write_out_of_workspace` runs as the unprivileged daemon user → kernel returns EACCES for `/etc/hosts` writes. **After unification, the namespace child runs as root** (per existing iws design) → root-in-namespace CAN write `/etc/hosts` unless we reject the call first.

Inside `sandbox/overlay/namespace_child.py`, add a pre-verb-dispatch denylist check (only for WRITE-allowed verbs; reads pass through):
```python
_HOST_DENYLIST_PREFIXES = ("/etc/", "/var/", "/proc/", "/sys/", "/boot/")

def _check_host_denylist(verb: str, args: Mapping[str, object]) -> ToolCallResult | None:
    if verb not in {"write_file", "edit_file", "shell"}:
        return None
    target = args.get("path") or args.get("cwd") or ""
    if any(str(target).startswith(p) for p in _HOST_DENYLIST_PREFIXES):
        return ToolCallResult(
            success=False,
            error={"kind": "forbidden_host_path", "path": str(target),
                   "message": "writes to system paths are denied inside the namespace child"},
        )
    return None
```
For `shell`, the denylist check is best-effort (shell can `cd /etc && rm hosts` via argv); follow-up plan tightens via syscall filter. Audit-event on denylist hit: `audit.workspace_security.host_denylist_block`.

→ **Verify:** every handler in `daemon/handler/` is ≤25 lines; `grep -rn "classify_path\|_in_workspace\|_out_of_workspace" backend/src/sandbox/daemon/` returns zero hits; Phase 3 §3.7 `test_namespace_denylist_protects_host_etc.py` passes.

---

## Step 8 — `OverlayHandle` idempotency

**8.1.** `sandbox/overlay/handle.py::OverlayHandle._destroyed` field (added in Phase 1 §4.10).

**8.2.** `sandbox/overlay/lifecycle.py::destroy(handle)` is idempotent:

```python
async def destroy(handle: OverlayHandle) -> None:
    """Idempotent. Safe to call concurrently from multiple threads.

    Sets handle._destroyed = True before doing kernel work, so a second
    concurrent caller sees the guard and no-ops. Real cleanup (umount +
    release_lease + rmtree) is wrapped in try/except to tolerate kernel-side
    races on the same mount.
    """
    if handle._destroyed:
        return
    handle._destroyed = True
    try:
        if handle.namespace_pid is not None:
            await _teardown_namespace(handle.namespace_pid)
        umount(handle.workspace_root)
    except OSError:
        pass  # already torn down
    # Lease release goes through pipeline's _released_lease_ids guard
    ...
```

**8.3.** Each pipeline owns `_released_lease_ids: set[str]` (Step 3.1, 4.1) to defend against double-release from concurrent threads (e.g., shell-job reaper races main call's finally-block destroy).

→ **Verify:** new test `tests/sandbox/overlay/test_handle_idempotency.py::test_double_destroy_is_noop` — call `lifecycle.destroy(handle)` twice concurrently from two threads, assert single `release_lease` call.

---

## Step 9 — O_NOFOLLOW enforcement

**9.1.** `tool_primitives/read.py`, `write.py`, `edit.py` use `O_NOFOLLOW` unconditionally:

```python
# tool_primitives/read.py
def compute(args: Mapping[str, object]) -> ReadResult:
    path = str(args["path"])
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(fd, "rb") as f:
            content = f.read()
        return ReadResult(success=True, exists=True, content=content.decode("utf-8"))
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError(f"refusing to follow symlink: {path}") from e
        raise
```

Similar for `write.py` (always `O_WRONLY | O_CREAT | O_NOFOLLOW`) and `edit.py` (read-then-write via no-follow).

**9.2.** Add static lint `tests/static/test_tool_primitives_o_nofollow.py` — AST scan of `tool_primitives/{read,write,edit}.py` to confirm every `os.open` call includes `O_NOFOLLOW`.

→ **Verify:** static lint passes; security test (Phase 3) confirms `/testbed/evil → /etc/shadow` raises.

---

## Step 10 — Lifecycle host API (lives in `sandbox/lifecycle/`, NOT `sandbox/api/`)

**Critic must-fix #6 / Planner F.4 / Architect F.4 (verified):** `sandbox/api/` already contains client-side artifacts (`_raw_exec.py`, `_sandbox_control.py`, `protocol.py`, `timeouts.py`, `tool/`, `transport.py`). Inserting host-side coroutines into the same package creates a same-name-opposite-side-of-wire confusion. The host-side lifecycle coroutines + (deferred) `WorkspaceSession` move to a new top-level `sandbox/lifecycle/` package.

**10.1.** Create `sandbox/lifecycle/__init__.py` (package). Exports `enter_isolated_workspace`, `exit_isolated_workspace`. Does NOT export `WorkspaceSession` (deferred — see §12).

**10.2.** Create `sandbox/lifecycle/enter_isolated_workspace.py`:

```python
async def enter_isolated_workspace(
    req: EnterIsolatedWorkspaceRequest,
) -> EnterIsolatedWorkspaceResult:
    """Host-side coroutine. Wraps audit.lifecycle.lifecycle_operation."""
    async with lifecycle_operation(
        event_class=WorkspaceLifecycle,
        kind="enter_isolated_workspace",
        actor_id=req.actor_id,
    ):
        pipeline = isolated_workspace.require_pipeline()
        handle = await pipeline.enter(req.agent_id, _config_from_req(req))
        return EnterIsolatedWorkspaceResult(
            success=True,
            manifest_version=str(handle.snapshot_version),
            manifest_root_hash=handle.layer_paths[0],  # or computed
        )
```

**10.3.** Create `sandbox/lifecycle/exit_isolated_workspace.py` (symmetric).

**10.4.** Create `sandbox/audit/lifecycle.py`:

```python
@dataclass(frozen=True)
class WorkspaceLifecycle:
    kind: str  # "enter_isolated_workspace", "exit_isolated_workspace"
    agent_id: str
    timings: Mapping[str, float]

@asynccontextmanager
async def lifecycle_operation(*, event_class, kind, actor_id):
    """Publishes workspace_lifecycle_started / workspace_lifecycle_completed events.

    Different from audit.operation.audited_operation (which publishes
    sandbox_operation_started/_completed for tool ops).
    """
    ...
```

**10.5.** Add `WorkspaceLifecycle` event class to `sandbox/audit/events.py`.

→ **Verify:** new test `tests/sandbox/api/test_lifecycle_audit_pair.py` — calling enter then exit produces a 4-event sequence.

---

## Step 11 — Agent-level tools

**11.1.** Create `backend/src/tools/isolated_workspace/enter_isolated_workspace/`:
- `__init__.py`
- `definition.py` — Pydantic Input → `sandbox.lifecycle.enter_isolated_workspace` → ToolResult JSON projection.
- `tests/test_enter_isolated_workspace_tool.py` (basic round-trip).

**11.2.** Create `backend/src/tools/isolated_workspace/exit_isolated_workspace/` (symmetric — imports from `sandbox.lifecycle`).

**11.3.** Tool naming rationale (Planner F.11): the tool names `enter_isolated_workspace` / `exit_isolated_workspace` are intentionally verbose. Agent-facing surfaces avoid internal jargon (`iws`); the verbosity cost is paid in model-generated tool calls, not human code. Don't relitigate per-PR.

→ **Verify:** both tools discoverable through the tool registry; basic round-trip passes; tool definition imports `sandbox.lifecycle.*` (not `sandbox.api.*`).

---

## Step 12 — `WorkspaceSession` async-CM (DEFERRED)

**Critic must-fix #11 / Architect F.8:** no production caller for `WorkspaceSession` is documented. Phase 3 §1.2 says tests use `sandbox.lifecycle.enter_isolated_workspace` / `exit_isolated_workspace` directly. Shipping a public API with no production user creates maintenance burden and a documentation gap (Architect §C Synthesis).

**Decision:** defer `WorkspaceSession` from the public API surface. If/when a production caller materializes, promote from the test-fixture location.

**12.1.** Create `tests/mock/sandbox/_fixtures/workspace_session.py` as a **test utility**, NOT a public API:

```python
# tests/mock/sandbox/_fixtures/workspace_session.py
class WorkspaceSession:
    """TEST FIXTURE — convenience async-CM wrapping the lifecycle pair.

    NOT part of sandbox/lifecycle/ public surface. Promoted to public API
    only when a production caller materializes (Critic must-fix #11).
    """

    @classmethod
    @asynccontextmanager
    async def enter_isolated(cls, agent_id, layer_stack_root):
        await sandbox.lifecycle.enter_isolated_workspace(...)
        try:
            yield cls(agent_id=agent_id, mode="isolated")
        finally:
            await sandbox.lifecycle.exit_isolated_workspace(...)

    @classmethod
    def ephemeral(cls, agent_id):
        return cls(agent_id=agent_id, mode="ephemeral")  # no-op CM
```

**12.2.** Document the deferral in `docs/sandbox/api_surface.md` §11 (per Phase 3 §8.1): "`WorkspaceSession` is a test-only convenience; production code uses the explicit `enter_isolated_workspace` / `exit_isolated_workspace` pair."

→ **Verify:** `grep -rn "from sandbox.lifecycle.* import WorkspaceSession" backend/src/` returns 0 hits; `grep -rn "WorkspaceSession" tests/` finds the fixture; no production import path exists.

---

## Step 13 — Plugin-block dispatcher gate

**13.1.** In `sandbox/daemon/rpc/dispatcher.py`, add a pre-dispatch gate for any `api.plugin.*` or `plugin.<name>.<op>` op:

```python
async def _check_plugin_block(args: dict, op_name: str) -> dict | None:
    """Returns a forbidden_in_isolated_workspace error if an iws handle is open.

    Fail-OPEN policy (Planner A.3.5 Option γ): when iws pipeline isn't bootstrapped
    (tests, early daemon startup) we permit the plugin op but emit a loud audit
    event so the bypass is visible. Threat-model note: an attacker who can prevent
    iws bootstrap (DoS the manager init) bypasses the policy; this risk is
    accepted because the alternative (fail-CLOSED) would break every test fixture
    that doesn't init iws. Follow-up plan: spawn fail-CLOSED-with-explicit-bypass
    variant once test fixtures are audited.
    """
    iws = isolated_workspace.get_active_pipeline()
    if iws is None:
        # Fail-OPEN: emit audit event so the bypass is visible (Planner F.20).
        await audit.emit("workspace_lifecycle.plugin_check_unbootstrapped", {
            "op": op_name, "agent_id": args.get("agent_id"),
        })
        return None
    agent_id = args.get("agent_id")
    if agent_id and iws.get_handle(agent_id) is not None:
        return {
            "success": False,
            "error": {
                "kind": "forbidden_in_isolated_workspace",
                "op": op_name,
                "message": "plugin access is blocked while isolated_workspace is open",
            },
        }
    return None
```

→ **Verify:** new test `tests/sandbox/isolated_workspace/policy/test_plugin_blocked.py` — enter iws → call `api.plugin.ensure` → assert `forbidden_in_isolated_workspace`. Phase 3 §3.8 `test_plugin_block_fail_open_emits_audit.py` asserts the audit event fires on fail-OPEN.

---

## Step 14 — Delete iws tool-op RPCs and the shell-out wrapper module

**Critic must-fix #1 / Planner F.1 (verified):** `sandbox/isolated_workspace/_iws_rpc.py` DOES NOT EXIST on disk (`find sandbox -name "_iws_rpc*"` returns empty). The actual files are:
- `sandbox/isolated_workspace/handlers.py` (200 lines) — lifecycle RPC handlers: `enter`, `exit_`, `status`, `list_open`, `test_reset`.
- `sandbox/isolated_workspace/ops_handlers.py` (98 lines) — tool-op RPC handlers (the 5 verb shims): `shell`, `read_file`, `write_file`, `edit_file`, `grep`. These are the shell-out wrappers being upgraded by Phase 2 §4.2.
- RPC routing in `sandbox/daemon/rpc/dispatcher.py:197-206`.

**14.1.** In `sandbox/daemon/rpc/dispatcher.py::DISPATCH_TABLE`, delete these 5 entries (all redundant now — `api.v1.<verb>` handles both modes via `resolve_pipeline`):
- `api.isolated_workspace.read_file`
- `api.isolated_workspace.write_file`
- `api.isolated_workspace.edit_file`
- `api.isolated_workspace.grep`
- `api.isolated_workspace.shell` (also iws-specific today)

**14.2.** `sandbox/isolated_workspace/handlers.py` SURVIVES UNCHANGED. It already contains only lifecycle helpers (`enter`, `exit_`, `status`, `list_open`, `test_reset`) — exactly what we want to keep. Overview §5 line item ("iws lifecycle RPC naming change ... survives") refers to this file.

**14.3.** Delete `sandbox/isolated_workspace/ops_handlers.py` entirely (98 lines of shell-out wrappers — already noted in Step 4.2). This is the only iws-side module that disappears.

→ **Verify:**
- `find sandbox -name "_iws_rpc*"` returns empty (it always was — confirms the phantom is gone from docs).
- `grep -rn "_iws_rpc" docs/plans/unify_sandbox_*.md` returns 0.
- `grep -rn "api\.isolated_workspace\.(read_file\|write_file\|edit_file\|grep\|shell)" backend/` returns zero hits.
- `ls sandbox/isolated_workspace/ops_handlers.py` returns "No such file or directory" after Phase 2 lands.
- `ls sandbox/isolated_workspace/handlers.py` STILL EXISTS (lifecycle helpers preserved).

---

## Acceptance criteria

- ✅ `WorkspacePipeline` protocol has exactly one method (`run_tool_call`).
- ✅ `EphemeralPipeline.run_tool_call` is the SINGLE public method. It dispatches by verb: foreground verbs follow per-call lifecycle (create → run → capture+commit if write → destroy); background-shell verbs (`shell_launch` / `shell_reap` / `shell_poll` / `shell_cancel`) route through private `_dispatch_background_verb` and use the `_background_jobs` registry. Module-level `_BACKGROUND_SHELL_VERBS` frozenset enumerates the four.
- ✅ Background-shell methods are PRIVATE (`_launch_bg_job`, `_reap_bg_job`, `_poll_bg_job`, `_cancel_bg_job`); `WorkspacePipeline` protocol surface stays at one method (Principle 4). Pipeline-introspection helpers (`get_agent_background_jobs`, `startup_gc`) are not tool-calls and live outside the protocol.
- ✅ `EphemeralPipeline.startup_gc()` runs at pipeline init; replaces global `daemon/service/shell_job.py::startup_gc`. Reaps orphan run-dirs / leases from a previous daemon process.
- ✅ `IsolatedPipeline` has `enter`/`run_tool_call`/`exit`/`get_handle` methods; overlay lifecycle spans enter→exit; background-shell verbs route through `_dispatch_background_verb_iws` using session-scoped `_session_jobs[agent_id]` storage (jobs share the session overlay; no per-job mount).
- ✅ `IsolatedPipeline.enter` checks `EphemeralPipeline.get_agent_background_jobs(agent_id)` and raises `LifecycleError(kind="ephemeral_jobs_in_flight", details={"job_ids": [...]})` if non-empty (Q4 mode-mix policy).
- ✅ `IsolatedPipeline.exit` drains in-flight session background shell jobs up to `grace_s`, force-kills survivors via `_gc.py::_unfreeze_and_kill`, reports `evicted_background_jobs` count in `phases_ms`.
- ✅ Background-shell OCC commits in ephemeral mode use `source="overlay_capture"` (Q5 — no coalescing; cross-path atomicity). Best-effort on stale-snapshot (Q1) — `OCCConflictError` is surfaced to the agent as `shell_conflict_result`, not silently rebased.
- ✅ Background-shell registry is pipeline-owned (Q2): `EphemeralPipeline._background_jobs: dict[str, ShellJob]` + `_jobs_by_agent: dict[str, set[str]]`; `IsolatedPipeline._session_jobs: dict[str, dict[str, ShellJob]]`. Global `daemon/service/shell_job.py::ShellJobRegistry` and `get_shell_job_registry()` are DELETED.
- ✅ Background-shell concurrency is unbounded per agent in ephemeral mode (Q3); gauge `EphemeralPipeline._background_jobs` length exposed via observability for alerting on runaway.
- ✅ `manager.py` (1624 lines verified) decomposed into 7 modules: `pipeline.py`, `_types.py`, `_lifecycle.py`, `_gc.py`, `_ttl.py`, `_quota.py`, `_runtime.py`. None exceeds 400 lines (`wc -l` check).
- ✅ `overlay.run_in_namespace` is the single execution path for both modes.
- ✅ `namespace_child.py` two-tier dispatcher: VERB_TABLE for read/write/edit/grep/glob; `shell.run` for shell; pre-dispatch host-path denylist for write-allowed verbs (`/etc/`, `/var/`, `/proc/`, `/sys/`, `/boot/`).
- ✅ OCC source-tag threading covers all 4 helper sites: `overlay_path_changes_to_occ_changes`, `build_overlay_write_change`, `build_overlay_delete_change`, inline `SymlinkChange`/`OpaqueDirChange` constructors. Default preserves `"overlay_capture"`.
- ✅ OCC disjoint-batch coalescing test passes: two concurrent typed writes batch into one commit; field-level assertion confirms `c.source == "api_write"`.
- ✅ Single-path determination uses `len({c.path for c in changes}) == 1` (not verb-name alone — guards against pathological symlink-to-directory cases).
- ✅ `OverlayHandle._destroyed` field exists; **per-pipeline `_handle_locks: dict[str, asyncio.Lock]` exists and `_destroy_with_lease_guard` acquires the per-handle lock before checking `_destroyed`** (TOCTOU fix landed). Lock entry popped after destroy completes.
- ✅ `lifecycle.destroy(handle)` is idempotent; concurrent destroy from two asyncio tasks results in exactly ONE cleanup; `release_lease` called exactly ONCE (Phase 3 §6.5 test).
- ✅ `tool_primitives.{read,write,edit,grep,glob}` use the `file_ops.open_no_follow` chokepoint (per-component walk preserves defense against intermediate symlinks); static lint enforces no naive `os.open(path, flags|O_NOFOLLOW)` bypass.
- ✅ Every daemon handler is ≤25 lines; `classify_path` and in/out-of-workspace helpers deleted.
- ✅ `sandbox.lifecycle.enter_isolated_workspace` / `exit_isolated_workspace` exist with `LifecycleResultBase` (in new `sandbox/lifecycle/` package — NOT `sandbox/api/`).
- ✅ `sandbox/audit/lifecycle.py` + `WorkspaceLifecycle` event class exist; lifecycle_operation publishes 2-event pair.
- ✅ Agent-level `tools/isolated_workspace/{enter,exit}_isolated_workspace/` exist + discoverable; import from `sandbox.lifecycle.*`.
- ✅ `WorkspaceSession` DEFERRED to `tests/mock/sandbox/_fixtures/workspace_session.py` (NOT shipped as public API per Critic must-fix #11).
- ✅ Plugin-block dispatcher gate fails-OPEN when iws pipeline not bootstrapped AND emits `workspace_lifecycle.plugin_check_unbootstrapped` audit event (Planner F.20).
- ✅ Host-path denylist enforced in namespace child for `/etc/`, `/var/`, `/proc/`, `/sys/`, `/boot/` (Critic must-fix #9).
- ✅ `isolated_workspace/ops_handlers.py` deleted; 5 iws tool-op RPCs deleted; `isolated_workspace/handlers.py` (lifecycle) PRESERVED.
- ✅ Phantom `_iws_rpc.py` reference removed from all docs (`grep -rn "_iws_rpc" docs/plans/unify_sandbox_*.md` returns 0).
- ✅ Parity corpus replay passes byte-equivalently **for ephemeral-mode verbs only** (modulo OCC source-tag note documented in CHANGELOG). iws verb migration is a **functional upgrade** validated by Phase 3's `behavior_upgrade/` tier — NOT parity preservation. Out-of-scope: backward-compatible iws result-shape preservation.
