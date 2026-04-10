"""Backend-driven atlas maintenance scheduler.

Atlas maintenance is runtime infrastructure, not planner-visible work.
This scheduler consumes runtime signals (lookup misses/staleness,
write-time dirty marks, and run-start cold starts) and executes
``atlas_builder`` / ``atlas_refresher`` off-DAG in the background.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from hooks.agent_posthook import execute_with_posthook
from team.atlas.freshness import canonical_subsystem_key
from team.atlas.persistence import build_chunk_from_brief
from team.atlas.store import AtlasStore, get_default_store
from team.builtins import ATLAS_BUILDER, ATLAS_REFRESHER
from team.models import WorkItem, WorkItemKind, WorkItemStatus

if TYPE_CHECKING:
    from agents.types import AgentDefinition
    from team.runtime.context_builder import TeamAgentContext
    from team.runtime.team_run import TeamRun

logger = logging.getLogger(__name__)

QueryRunner = Callable[["AgentDefinition", Any], Awaitable[Any]]
QueryContextBuilder = Callable[["AgentDefinition", "TeamRun", WorkItem], "TeamAgentContext"]
PosthookContextBuilder = Callable[["AgentDefinition", Any], "TeamAgentContext"]
AgentLookup = Callable[[str], "AgentDefinition | None"]

_BOOTSTRAP_KEY = "__bootstrap__"
_LOOKUP_REFRESH_PRIORITY = 10
_LOOKUP_SCOUT_PRIORITY = 20
_EDIT_DIRTY_PRIORITY = 40
_TEAM_RUN_BOOTSTRAP_PRIORITY = 60
_ATLAS_POLICY_DEFERRED_PERSIST = "deferred_persist"
_ATLAS_POLICY_REFRESH_ONLY = "refresh_only"
_ATLAS_POLICY_FULL = "full"
_VALID_POLICIES = {
    _ATLAS_POLICY_DEFERRED_PERSIST,
    _ATLAS_POLICY_REFRESH_ONLY,
    _ATLAS_POLICY_FULL,
}


@dataclass(frozen=True)
class AtlasMaintenanceJob:
    key: str
    agent_name: str
    subsystems: tuple[str, ...]
    priority: int
    reason: str


@dataclass
class _InFlightLease:
    task: asyncio.Task[None]
    agent_name: str
    started_at: float
    expires_at: float


@dataclass
class _BackoffState:
    failures: int = 0
    next_retry_at: float = 0.0
    last_error: str = ""


class AtlasMaintenanceScheduler:
    """Sidecar scheduler for backend atlas maintenance."""

    def __init__(
        self,
        *,
        team_run: "TeamRun",
        runner: QueryRunner,
        build_query_context: QueryContextBuilder,
        build_posthook_context: PosthookContextBuilder,
        agent_lookup: AgentLookup,
        store: AtlasStore | None = None,
        max_concurrent_jobs: int = 1,
        policy: str = _ATLAS_POLICY_FULL,
        lease_ttl_seconds: float = 600.0,
        dirty_flush_seconds: float = 2.0,
    ) -> None:
        self.team_run = team_run
        self.runner = runner
        self.build_query_context = build_query_context
        self.build_posthook_context = build_posthook_context
        self.agent_lookup = agent_lookup
        self.store = store if store is not None else get_default_store()
        self.max_concurrent_jobs = max(1, int(max_concurrent_jobs))
        self.policy = policy if policy in _VALID_POLICIES else _ATLAS_POLICY_FULL
        self.lease_ttl_seconds = max(30.0, float(lease_ttl_seconds))
        self.dirty_flush_seconds = max(0.5, float(dirty_flush_seconds))

        self._queue: asyncio.PriorityQueue[tuple[int, int, AtlasMaintenanceJob]] = (
            asyncio.PriorityQueue()
        )
        self._queued_keys: set[str] = set()
        self._dirty_subsystems: set[str] = set()
        self._inflight: dict[str, _InFlightLease] = {}
        self._backoff: dict[str, _BackoffState] = {}
        self._semaphore = asyncio.Semaphore(self.max_concurrent_jobs)
        self._seq = 0
        self._stop = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker_task is not None or self.policy == _ATLAS_POLICY_DEFERRED_PERSIST:
            return
        self._stop.clear()
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name=f"atlas-scheduler-worker:{self.team_run.id}",
        )
        self._idle_task = asyncio.create_task(
            self._idle_loop(),
            name=f"atlas-scheduler-idle:{self.team_run.id}",
        )
        self._schedule_team_run_bootstrap()

    async def stop(self) -> None:
        self._stop.set()
        tasks = [t for t in (self._worker_task, self._idle_task) if t is not None]
        for task in tasks:
            task.cancel()
        for lease in list(self._inflight.values()):
            lease.task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._inflight:
            await asyncio.gather(
                *(lease.task for lease in list(self._inflight.values())),
                return_exceptions=True,
            )
        self._worker_task = None
        self._idle_task = None
        self._inflight.clear()
        self._queued_keys.clear()

    def note_lookup(self, entries: list[dict[str, Any]], *, source: str = "atlas_lookup") -> None:
        """Queue background maintenance implied by a lookup result."""
        if not self._atlas_enabled() or not entries or self.policy == _ATLAS_POLICY_DEFERRED_PERSIST:
            return
        self._prune_expired_leases()
        if not self._project_has_chunks():
            if self.policy == _ATLAS_POLICY_FULL:
                self._enqueue_builder(reason=f"{source}:cold-start", priority=_LOOKUP_REFRESH_PRIORITY)
            return
        for entry in entries:
            action = str(entry.get("action") or "").strip()
            subsystem = str(entry.get("subsystem") or "").strip()
            if not subsystem or action not in {"refresh", "scout"}:
                continue
            if self.policy == _ATLAS_POLICY_REFRESH_ONLY and action != "refresh":
                continue
            self._enqueue_refresh(
                subsystem,
                reason=f"{source}:{action}",
                priority=(
                    _LOOKUP_REFRESH_PRIORITY
                    if action == "refresh"
                    else _LOOKUP_SCOUT_PRIORITY
                ),
            )

    def mark_dirty_path(self, file_path: str, *, reason: str = "edit") -> None:
        """Record a changed file so the idle loop can refresh affected chunks."""
        if not self._atlas_enabled() or self.policy == _ATLAS_POLICY_DEFERRED_PERSIST:
            return
        subsystem_keys = self._match_subsystems(file_path)
        if not subsystem_keys:
            fallback = canonical_subsystem_key([self._normalise_path(file_path)])
            if fallback:
                subsystem_keys = {fallback}
        if not subsystem_keys:
            return
        self._dirty_subsystems.update(subsystem_keys)
        logger.debug(
            "atlas scheduler dirty mark: run=%s reason=%s subsystems=%s",
            self.team_run.id,
            reason,
            sorted(subsystem_keys),
        )

    def _schedule_team_run_bootstrap(self) -> None:
        if not self._atlas_enabled() or self.policy != _ATLAS_POLICY_FULL:
            return
        if self._project_has_chunks():
            return
        self._enqueue_builder(
            reason="team-run-hook:cold-start",
            priority=_TEAM_RUN_BOOTSTRAP_PRIORITY,
        )

    def _enqueue_builder(self, *, reason: str, priority: int) -> None:
        key = self._job_key(_BOOTSTRAP_KEY)
        if self._should_skip_enqueue(key):
            return
        self._enqueue(
            AtlasMaintenanceJob(
                key=key,
                agent_name=ATLAS_BUILDER,
                subsystems=(),
                priority=priority,
                reason=reason,
            )
        )

    def _enqueue_refresh(self, subsystem: str, *, reason: str, priority: int) -> None:
        subsystem = subsystem.strip()
        if not subsystem:
            return
        if self._job_key(_BOOTSTRAP_KEY) in self._queued_keys or self._job_key(
            _BOOTSTRAP_KEY
        ) in self._inflight:
            self._dirty_subsystems.add(subsystem)
            return
        key = self._job_key(subsystem)
        if self._should_skip_enqueue(key):
            return
        self._enqueue(
            AtlasMaintenanceJob(
                key=key,
                agent_name=ATLAS_REFRESHER,
                subsystems=(subsystem,),
                priority=priority,
                reason=reason,
            )
        )

    def _enqueue(self, job: AtlasMaintenanceJob) -> None:
        self._seq += 1
        self._queued_keys.add(job.key)
        self._queue.put_nowait((job.priority, self._seq, job))
        logger.info(
            "atlas scheduler queued: run=%s agent=%s key=%s priority=%s reason=%s",
            self.team_run.id,
            job.agent_name,
            job.key,
            job.priority,
            job.reason,
        )

    def _should_skip_enqueue(self, key: str) -> bool:
        self._prune_expired_leases()
        state = self._backoff.get(key)
        now = time.monotonic()
        if state is not None and now < state.next_retry_at:
            logger.debug(
                "atlas scheduler backoff: run=%s key=%s retry_in=%.1fs",
                self.team_run.id,
                key,
                state.next_retry_at - now,
            )
            return True
        return key in self._queued_keys or key in self._inflight

    async def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                _, _, job = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            self._queued_keys.discard(job.key)
            if self._stop.is_set():
                break
            if self._is_backed_off(job.key):
                continue
            await self._semaphore.acquire()
            task = asyncio.create_task(
                self._run_job(job),
                name=f"atlas-maintenance:{job.agent_name}:{job.key}",
            )
            self._inflight[job.key] = _InFlightLease(
                task=task,
                agent_name=job.agent_name,
                started_at=time.monotonic(),
                expires_at=time.monotonic() + self.lease_ttl_seconds,
            )

    async def _idle_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(self.dirty_flush_seconds)
            except asyncio.CancelledError:
                break
            dirty = sorted(self._dirty_subsystems)
            self._dirty_subsystems.difference_update(dirty)
            for subsystem in dirty:
                self._enqueue_refresh(
                    subsystem,
                    reason="idle-dirty-flush",
                    priority=_EDIT_DIRTY_PRIORITY,
                )

    async def _run_job(self, job: AtlasMaintenanceJob) -> None:
        try:
            submitted = await self._execute(job)
            self._clear_backoff(job.key)
            self._clear_dirty(submitted, fallback=list(job.subsystems))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_failure(job.key, exc)
        finally:
            self._inflight.pop(job.key, None)
            self._semaphore.release()

    async def _execute(self, job: AtlasMaintenanceJob) -> Any:
        defn = self.agent_lookup(job.agent_name)
        if defn is None:
            raise RuntimeError(f"atlas maintenance agent {job.agent_name!r} is not registered")

        payload: dict[str, Any] = {
            "description": (
                "Backend atlas maintenance task. Refresh the atlas without "
                "changing product code."
            ),
            "maintenance_reason": job.reason,
            "atlas_background": True,
        }
        if job.agent_name == ATLAS_REFRESHER:
            payload["stale_subsystems"] = list(job.subsystems)

        wi = WorkItem(
            id=f"atlas-{uuid.uuid4().hex}",
            team_run_id=self.team_run.id,
            agent_name=job.agent_name,
            status=WorkItemStatus.PENDING,
            kind=WorkItemKind.ATOMIC,
            root_id=self.team_run.root_work_item_id or "",
            payload=payload,
        )
        ctx = self.build_query_context(defn, self.team_run, wi)
        ctx.tool_metadata["atlas_maintenance"] = True
        ctx.tool_metadata["atlas_scheduler_job_key"] = job.key
        ctx.tool_metadata["atlas_maintenance_reason"] = job.reason

        logger.info(
            "atlas scheduler start: run=%s agent=%s subsystems=%s reason=%s",
            self.team_run.id,
            job.agent_name,
            list(job.subsystems),
            job.reason,
        )
        _, submitted = await execute_with_posthook(
            work_defn=defn,
            work_ctx=ctx,
            runner=self.runner,
            agent_lookup=self.agent_lookup,
            posthook_ctx_builder=self.build_posthook_context,
        )
        logger.info(
            "atlas scheduler done: run=%s agent=%s subsystems=%s",
            self.team_run.id,
            job.agent_name,
            list(job.subsystems),
        )
        return submitted

    def persist_direct_scout_brief(
        self,
        brief: dict[str, Any],
        *,
        ci_service: Any | None = None,
        reason: str = "direct-scout",
    ) -> bool:
        """Persist a scout brief directly without spawning atlas-owned scout work."""
        if not self._atlas_enabled() or not isinstance(brief, dict):
            return False
        project_key = getattr(self.team_run.project_context, "project_key", "") or ""
        repo_root = getattr(self.team_run.project_context, "repo_root", "") or ""
        if not project_key or not repo_root or self.store is None:
            return False
        try:
            chunk = build_chunk_from_brief(
                brief=brief,
                repo_root=repo_root,
                ci_service=ci_service,
            )
            applied = self.store.upsert_chunks(
                project_key=project_key,
                repo_root=repo_root,
                chunks=[chunk],
            )
            if applied:
                self._dirty_subsystems.discard(chunk.subsystem)
            logger.info(
                "atlas scheduler persisted direct scout brief: run=%s subsystem=%s reason=%s applied=%s",
                self.team_run.id,
                chunk.subsystem,
                reason,
                bool(applied),
            )
            return bool(applied)
        except Exception:
            logger.debug("direct scout atlas persistence failed", exc_info=True)
            return False

    def _clear_dirty(self, submitted: Any, *, fallback: list[str]) -> None:
        artifact = getattr(submitted, "artifact", None)
        if isinstance(artifact, dict):
            subsystems = artifact.get("subsystems")
            if isinstance(subsystems, list):
                cleaned = {str(item).strip() for item in subsystems if str(item).strip()}
                self._dirty_subsystems.difference_update(cleaned)
                return
        self._dirty_subsystems.difference_update(fallback)

    def _record_failure(self, key: str, exc: Exception) -> None:
        state = self._backoff.setdefault(key, _BackoffState())
        state.failures += 1
        state.last_error = str(exc)
        delay = min(300.0, 5.0 * (2 ** max(0, state.failures - 1)))
        state.next_retry_at = time.monotonic() + delay
        logger.warning(
            "atlas scheduler failure: run=%s key=%s failures=%d retry_in=%.1fs error=%s",
            self.team_run.id,
            key,
            state.failures,
            delay,
            exc,
        )

    def _clear_backoff(self, key: str) -> None:
        self._backoff.pop(key, None)

    def _is_backed_off(self, key: str) -> bool:
        state = self._backoff.get(key)
        return bool(state is not None and time.monotonic() < state.next_retry_at)

    def _prune_expired_leases(self) -> None:
        now = time.monotonic()
        expired = [key for key, lease in self._inflight.items() if lease.expires_at <= now]
        for key in expired:
            logger.warning(
                "atlas scheduler lease expired: run=%s key=%s agent=%s",
                self.team_run.id,
                key,
                self._inflight[key].agent_name,
            )
            self._inflight.pop(key, None)

    def _atlas_enabled(self) -> bool:
        project_key = getattr(self.team_run.project_context, "project_key", "") or ""
        return bool(project_key and self.store is not None and self.store.is_initialised())

    def _project_has_chunks(self) -> bool:
        project_key = getattr(self.team_run.project_context, "project_key", "") or ""
        if not project_key or self.store is None or not self.store.is_initialised():
            return False
        return self.store.has_chunks(project_key)

    def _match_subsystems(self, file_path: str) -> set[str]:
        project_key = getattr(self.team_run.project_context, "project_key", "") or ""
        if not project_key or self.store is None or not self.store.is_initialised():
            return set()
        normalised = self._normalise_path(file_path)
        if not normalised:
            return set()
        matches: dict[str, tuple[int, int]] = {}
        best_specificity: tuple[int, int] | None = None
        for subsystem in self.store.list_subsystems(project_key):
            scopes = [part.strip() for part in subsystem.split("|") if part.strip()]
            subsystem_best: tuple[int, int] | None = None
            for scope in scopes:
                if not self._scope_matches(normalised, scope):
                    continue
                specificity = self._scope_specificity(scope)
                if subsystem_best is None or specificity > subsystem_best:
                    subsystem_best = specificity
            if subsystem_best is None:
                continue
            matches[subsystem] = subsystem_best
            if best_specificity is None or subsystem_best > best_specificity:
                best_specificity = subsystem_best
        if best_specificity is None:
            return set()
        return {
            subsystem
            for subsystem, specificity in matches.items()
            if specificity == best_specificity
        }

    @staticmethod
    def _scope_matches(path: str, scope: str) -> bool:
        scope = scope.strip()
        if not scope:
            return False
        return (
            path == scope
            or path.startswith(scope.rstrip("/") + "/")
            or scope.startswith(path.rstrip("/") + "/")
        )

    @staticmethod
    def _scope_specificity(scope: str) -> tuple[int, int]:
        scope = scope.strip().rstrip("/")
        parts = [part for part in scope.split("/") if part]
        return (len(parts), len(scope))

    def _normalise_path(self, file_path: str) -> str:
        raw = (file_path or "").strip()
        if not raw:
            return ""
        raw = os.path.normpath(raw)
        repo_root = (getattr(self.team_run.project_context, "repo_root", "") or "").rstrip("/")
        if repo_root and raw.startswith(repo_root + "/"):
            raw = raw[len(repo_root) + 1 :]
        if raw.startswith("./"):
            raw = raw[2:]
        return raw.strip("/.")

    def _job_key(self, subsystem: str) -> str:
        project_key = getattr(self.team_run.project_context, "project_key", "") or ""
        return f"{project_key}:{subsystem}"
