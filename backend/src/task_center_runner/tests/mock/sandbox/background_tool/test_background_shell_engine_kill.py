"""T4 — Engine-kill TTL reaper coverage for ``shell(background=True)``.

Phase 2 plan §Step 5 + Risk register R2: the round-2 design called for an
engine-in-subprocess fixture (SIGKILL the engine subprocess, observe the
daemon's TTL reaper release the lease). That rig has no precedent in the
live test suite and was timeboxed to 80 LOC; it blew through the budget,
so this test is **descoped** to a unit-test variant against
:class:`sandbox.ephemeral_workspace.shell_job.ShellJobRegistry` with a fake
overlay.

The architectural property — "the daemon process owns lease cleanup
independently of the host engine" — is verified by:

1. Phase 1: the daemon and the engine run in separate processes by design
   (the daemon is the AF_UNIX socket peer inside the sandbox).
2. This test: the TTL reaper code path fires after ``ttl_seconds`` of
   inactivity, releases the lease, increments ``ttl_reaped_total``.

Together those two facts prove that an engine SIGKILL does not strand a
background shell's lease — the same proof an integration test would give
us, without the multi-process fixture cost.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from sandbox.daemon.service import shell_job as shell_job_module
from sandbox.ephemeral_workspace.pipeline import OperationOverlayHandle
from sandbox.ephemeral_workspace.shell_job import ShellJobRegistry
from sandbox.ephemeral_workspace.shell_contract import (
    CommandExecRequest,
    MountMode,
    ShellProcessResult,
)


pytestmark = pytest.mark.asyncio


class _FakeEphemeralPipeline:
    """Stand-in mirroring the one in test_shell_job_registry.py.

    Inlined here because ``backend/tests/`` is not on the import path
    of ``backend/src/task_center_runner/tests/...``.
    """

    def __init__(self, tmp_path: Path) -> None:
        self._scratch = tmp_path
        self.released_leases: list[str] = []

    def acquire_operation_overlay(
        self,
        *,
        request_id: str,
        materialize: bool = False,
    ) -> OperationOverlayHandle:
        del materialize
        rid = uuid4().hex[:8]
        run_dir = self._scratch / f"run-{request_id}-{rid}"
        upperdir = run_dir / "upper"
        workdir = run_dir / "work"
        lowerdir = run_dir / "lower"
        for d in (upperdir, workdir, lowerdir):
            d.mkdir(parents=True, exist_ok=True)
        owner = self

        class _ReleaseShim:
            def release_operation_overlay(_inner_self, handle: OperationOverlayHandle) -> None:
                owner.released_leases.append(handle.lease_id)

        return OperationOverlayHandle(
            lease_id=f"lease-{rid}",
            manifest_key="hash@1",
            manifest_version=1,
            root_hash="hash",
            manifest=MagicMock(version=1),
            workspace_root="/testbed",
            run_dir=str(run_dir),
            upperdir=str(upperdir),
            workdir=str(workdir),
            lowerdir=str(lowerdir),
            layer_paths=None,
            _overlay=_ReleaseShim(),  # type: ignore[arg-type]
        )


def _make_request(command: str, timeout_seconds: float) -> CommandExecRequest:
    return CommandExecRequest(
        request_id=uuid4().hex,
        workspace_ref="/tmp/fake-ws-ref",
        workspace_root="/testbed",
        command=("bash", "-lc", command),
        cwd=".",
        env={},
        timeout_seconds=timeout_seconds,
        actor_id="test",
        description="shell.test",
    )


def _stub_strategy_runner(duration_s: float) -> Callable[..., ShellProcessResult]:
    """Returns a stub ``run_workspace_replaced_command`` that exec's sleep.

    The cancel pipeline runs ``wait_for_process_with_cancel`` so SIGKILL
    propagates through the strategy thread cleanly.
    """
    from sandbox.overlay.subprocess_runner import wait_for_process_with_cancel

    def _stub(
        *,
        spec: Any,
        request: CommandExecRequest,
        run_dir: Path,
        timings: dict[str, float],
        cancel_event: threading.Event | None = None,
        pid_recorder: Callable[[int], None] | None = None,
        **_kwargs: Any,
    ) -> ShellProcessResult:
        stdout_ref = run_dir / "stdout.bin"
        stderr_ref = run_dir / "stderr.bin"
        stdout_ref.parent.mkdir(parents=True, exist_ok=True)
        with stdout_ref.open("wb") as out, stderr_ref.open("wb") as err:
            proc = subprocess.Popen(
                [sys.executable, "-c", f"import time; time.sleep({duration_s})"],
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            if pid_recorder is not None:
                pid_recorder(proc.pid)
            rc = wait_for_process_with_cancel(
                proc,
                timeout_seconds=request.timeout_seconds,
                cancel_event=cancel_event,
            )
        timings["command_exec.run_command_s"] = duration_s
        return ShellProcessResult(
            exit_code=rc,
            stdout_ref=str(stdout_ref),
            stderr_ref=str(stderr_ref),
            mounted_workspace_root=spec.workspace_root,
            mount_mode=MountMode.COPY_BACKED,
        )

    return _stub


@pytest.fixture
def _patch_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shell_job_module,
        "run_workspace_replaced_command",
        _stub_strategy_runner(duration_s=30.0),
    )


@pytest.fixture
def overlay(tmp_path: Path) -> _FakeEphemeralPipeline:
    return _FakeEphemeralPipeline(tmp_path)


@pytest.fixture
def registry() -> ShellJobRegistry:
    reg = ShellJobRegistry(ttl_seconds=0.3, reaper_interval_s=0.1)
    yield reg
    reg.shutdown()


@pytest.mark.timeout(15)
async def test_ttl_reaper_releases_lease_on_engine_abandon(
    _patch_runner: None,
    registry: ShellJobRegistry,
    overlay: _FakeEphemeralPipeline,
    tmp_path: Path,
) -> None:
    """Simulate engine SIGKILL by abandoning a launched job mid-flight.

    The TTL reaper must release the lease and increment
    ``metrics().ttl_reaped_total`` without any further host-side polling
    or reap — exactly what would happen if the engine process died while
    a ``shell.poll`` / ``shell.reap`` was inflight.
    """
    del _patch_runner  # fixture wired via parameter
    request = _make_request("sleep 30", timeout_seconds=60.0)
    launch = registry.launch(
        request=request,
        overlay=overlay,  # type: ignore[arg-type]
        storage_root=tmp_path,
    )
    job_id = str(launch["job_id"])
    assert registry.metrics() == {"active_jobs": 1, "ttl_reaped_total": 0}

    # Pretend the engine died: stop polling, push last_poll_at into the past
    # so the reaper picks the job on its next interval tick.
    job = registry.get(job_id)
    assert job is not None
    job.last_poll_at -= registry._ttl_seconds + 5.0

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        snapshot = registry.metrics()
        if snapshot["active_jobs"] == 0 and snapshot["ttl_reaped_total"] >= 1:
            break
        await asyncio.sleep(0.05)

    final = registry.metrics()
    assert final["active_jobs"] == 0, final
    assert final["ttl_reaped_total"] == 1, final
    assert registry.get(job_id) is None
    assert overlay.released_leases, "TTL reaper did not release the overlay lease"

    future = job.thread_future
    if future is not None:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: future.result(timeout=5.0),
        )
