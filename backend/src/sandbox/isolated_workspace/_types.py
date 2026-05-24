"""Shared isolated workspace types and lightweight ports."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from sandbox.isolated_workspace.network import VethPair

logger = logging.getLogger("sandbox.isolated_workspace.manager")

SCHEMA_VERSION = 1
HANDLE_PREFIX = "eos-iws-"
CGROUP_ROOT = Path("/sys/fs/cgroup")
DEFAULT_WORKSPACE_ROOT = "/testbed"

# PLAN §14: SUBSET-COVER invariant floor.
# sum(phases_ms.values()) <= total_ms + max(_PHASE_TIMER_OVERHEAD_BUDGET_MS,
# 0.05 * total_ms). Exposed for tests; do NOT raise without reviewing
# `assert_subset_cover` callers.
_PHASE_TIMER_OVERHEAD_BUDGET_MS = 2.0


HandleStatus = Literal["active", "exiting", "stopped", "reaping"]


# Test-only failure-injection knobs (PLAN §9.3). The env vars are read at the
# phase boundary every time so tests can change them between enter() calls
# without restarting the daemon. Production keeps these unset; the branches
# are dead code at runtime.
_TEST_HANG_AT_ENV = "EOS_ISOLATED_WORKSPACE_TEST_HANG_AT"
_TEST_FAIL_AT_ENV = "EOS_ISOLATED_WORKSPACE_TEST_FAIL_AT"
_TEST_HOLDER_CRASH_ENV = "EOS_ISOLATED_WORKSPACE_TEST_HOLDER_CRASH"
_TEST_PHASE_DELAY_ENV = "EOS_ISOLATED_WORKSPACE_TEST_PHASE_DELAY"


def _maybe_inject_failure(phase: str) -> None:
    """Raise the configured test failure for ``phase`` if any knob points here.

    Two knobs are supported:

    * ``EOS_ISOLATED_WORKSPACE_TEST_HANG_AT=<phase>`` — surface as a
      ``setup_timeout`` error so the rollback path runs (matches what a real
      hung kernel call would look like once the timeout fired).
    * ``EOS_ISOLATED_WORKSPACE_TEST_FAIL_AT=<phase>`` — surface as a
      generic ``setup_failed`` error.

    Both branches share the same rollback contract (lease released, partial
    state torn down) so a single helper keeps the call sites a one-liner.
    """
    hang_at = os.environ.get(_TEST_HANG_AT_ENV, "").strip()
    if hang_at == phase:
        raise IsolatedWorkspaceError(
            "setup_timeout",
            f"test-only setup_timeout injected at {phase}",
            failed_step=phase,
        )
    fail_at = os.environ.get(_TEST_FAIL_AT_ENV, "").strip()
    if fail_at == phase:
        raise IsolatedWorkspaceError(
            "setup_failed",
            f"test-only setup_failed injected at {phase}",
            failed_step=phase,
        )
    # Tier 9 latency-regression knob: ``<phase>:<ms>`` (comma-separated to
    # delay multiple phases in one run). Sleeps synchronously inside the
    # ``with timer.measure(phase)`` block so the injected ms is reflected in
    # the audit ``phases_ms[<phase>]`` value.
    delays = os.environ.get(_TEST_PHASE_DELAY_ENV, "").strip()
    if delays:
        for spec in delays.split(","):
            entry = spec.strip()
            if not entry or ":" not in entry:
                continue
            target_phase, _, ms_text = entry.partition(":")
            if target_phase.strip() != phase:
                continue
            try:
                delay_ms = float(ms_text.rstrip("ms").strip())
            except ValueError:
                continue
            time.sleep(max(0.0, delay_ms) / 1000.0)


class IsolatedWorkspaceError(Exception):
    """Base class for isolated-workspace lifecycle errors.

    ``kind`` becomes the wire error kind on the daemon RPC response.
    """

    def __init__(self, kind: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.kind = kind
        self.details = details


class LayerSnapshotLike(Protocol):
    lease_id: str
    manifest_version: int
    root_hash: str
    layer_paths: tuple[str, ...] | None


class LayerStackPort(Protocol):
    """The subset of ``workspace_server`` calls the manager needs."""

    def prepare_workspace_snapshot(
        self, layer_stack_root: str, *, owner_request_id: str
    ) -> LayerSnapshotLike: ...

    def release_workspace_snapshot(
        self, layer_stack_root: str, *, lease_id: str
    ) -> bool: ...


class AuditSink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None: ...


@dataclass
class IsolatedWorkspaceHandle:
    """Per-workspace state. Not a subclass of ``OperationOverlayHandle`` (C1)."""

    handle_id: str
    agent_id: str
    lease_id: str
    manifest_version: int
    manifest_root_hash: str
    workspace_root: str
    scratch_dir: Path
    upperdir: Path
    workdir: Path
    ns_fds: dict[str, int] = field(default_factory=dict)
    root_pid: int = 0
    # FDs into the ns_holder's readiness/control pipes. ``-1`` means "not yet
    # opened". Closed on teardown / rollback.
    readiness_fd: int = -1
    control_fd: int = -1
    veth: VethPair | None = None
    cgroup_path: Path | None = None
    # Set to True when R11's SIGSTOP fallback fires because cgroup.freeze is
    # missing, write_text raises EACCES/EPERM, or the read-back doesn't
    # match what we wrote (file shadowed, kernel ignored the write).
    # Surfaced in audit + status. Stays False on healthy hosts where the
    # cgroup v2 freezer accepts writes normally.
    freezer_degraded: bool = False
    created_at: float = 0.0
    last_activity: float = 0.0
    status: HandleStatus = "active"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def to_persisted(self) -> dict[str, Any]:
        return {
            "handle_id": self.handle_id,
            "agent_id": self.agent_id,
            "lease_id": self.lease_id,
            "manifest_version": self.manifest_version,
            "manifest_root_hash": self.manifest_root_hash,
            "veth_host_name": self.veth.host_name if self.veth else None,
            "ns_ip": str(self.veth.ns_ip) if self.veth else None,
            "cgroup_path": self.cgroup_path.as_posix() if self.cgroup_path else None,
            "scratch_dir_path": self.scratch_dir.as_posix(),
            "root_pid": self.root_pid,
            "freezer_degraded": self.freezer_degraded,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class _ManagerConfig:
    enabled: bool
    ttl_s: float
    per_agent_quota: int
    total_cap: int
    upperdir_bytes: int
    memavail_fraction: float
    setup_timeout_s: float
    rfc1918_egress: Literal["allow", "deny"]
    fallback_dns: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> _ManagerConfig:
        env = env or dict(os.environ)
        return cls(
            enabled=env.get("EOS_ISOLATED_WORKSPACE_ENABLED", "false").lower() == "true",
            ttl_s=float(env.get("EOS_ISOLATED_WORKSPACE_TTL_S", "1800")),
            per_agent_quota=int(env.get("EOS_ISOLATED_WORKSPACE_PER_AGENT", "1")),
            total_cap=int(env.get("EOS_ISOLATED_WORKSPACE_TOTAL_CAP", "5")),
            upperdir_bytes=int(env.get("EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES",
                                      str(1024 * 1024 * 1024))),
            memavail_fraction=float(env.get("EOS_ISOLATED_WORKSPACE_MEMAVAIL_FRACTION", "0.5")),
            setup_timeout_s=float(env.get("EOS_ISOLATED_WORKSPACE_SETUP_TIMEOUT_S", "30")),
            rfc1918_egress="deny" if env.get(
                "EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS", "allow"
            ).lower() == "deny" else "allow",
            fallback_dns=env.get("EOS_ISOLATED_WORKSPACE_FALLBACK_DNS", "1.1.1.1"),
        )


class _PhaseTimer:
    """Per-operation phase-timing helper (PLAN §14).

    ``measure(name)`` is a context manager that records the elapsed
    millisecond cost of a phase ONLY when its body exits normally — phases
    that raised mid-flight are intentionally absent from ``phases_ms`` so
    that "absent" stays distinct from "ran in zero time" (P5).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self._phases: dict[str, float] = {}

    @contextlib.contextmanager
    def measure(self, name: str):
        t0 = self._clock()
        success = False
        try:
            yield
            success = True
        finally:
            if success:
                self._phases[name] = (self._clock() - t0) * 1000.0

    def total_ms(self) -> float:
        return (self._clock() - self._start) * 1000.0

    @property
    def phases_ms(self) -> dict[str, float]:
        return dict(self._phases)


class _Runtime(Protocol):
    """Kernel-touching operations the manager delegates to.

    The only concrete implementation is :class:`_LinuxRuntime`; the Protocol
    exists so individual tests can swap in lightweight doubles without
    importing the kernel-touching module. ``mount_overlay`` and
    ``configure_dns`` are ``async def`` so concurrent enters (Tier 6 / Tier 8)
    do not serialize on subprocess wait — both helpers spawn long-running
    (up to 30 s) setns subprocesses that would otherwise block the event
    loop under N=5 fan-out.
    """

    def spawn_ns_holder(self, handle: IsolatedWorkspaceHandle, *, setup_timeout_s: float) -> int: ...
    def open_ns_fds(self, root_pid: int) -> dict[str, int]: ...
    async def mount_overlay(
        self, handle: IsolatedWorkspaceHandle, *, layer_paths: tuple[str, ...]
    ) -> None: ...
    async def configure_dns(
        self, handle: IsolatedWorkspaceHandle, *, fallback_dns: str
    ) -> bool: ...
    def signal_net_ready(
        self, handle: IsolatedWorkspaceHandle, *, setup_timeout_s: float
    ) -> None: ...
    def create_cgroup(self, handle: IsolatedWorkspaceHandle) -> Path: ...
    def freeze(self, handle: IsolatedWorkspaceHandle, *, freeze: bool) -> None: ...
    def kill_holder(self, root_pid: int, *, grace_s: float) -> None: ...
    def run_in_handle(
        self,
        handle: IsolatedWorkspaceHandle,
        *,
        argv: list[str],
        stdin: bytes | None = None,
        timeout_s: float | None = None,
    ) -> tuple[int, bytes, bytes]: ...



__all__ = [
    "AuditSink",
    "CGROUP_ROOT",
    "DEFAULT_WORKSPACE_ROOT",
    "HANDLE_PREFIX",
    "HandleStatus",
    "IsolatedWorkspaceError",
    "IsolatedWorkspaceHandle",
    "LayerSnapshotLike",
    "LayerStackPort",
    "SCHEMA_VERSION",
    "_ManagerConfig",
    "_PHASE_TIMER_OVERHEAD_BUDGET_MS",
    "_PhaseTimer",
    "_Runtime",
    "_maybe_inject_failure",
    "logger",
]
