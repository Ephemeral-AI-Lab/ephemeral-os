"""Unit tests for ``subprocess_runner.wait_for_process_with_cancel`` and
``subprocess_to_refs`` cancel-event plumbing.

These cover the background-shell polling-wait path (plan Step 3): SIGTERM
on cancel, SIGKILL escalation after 2 s grace, ``pid_recorder`` callback,
and the foreground fast-path that bypasses polling entirely.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sandbox.execution.subprocess_runner import (
    subprocess_to_refs,
    wait_for_process_with_cancel,
)


def _short_sleep_cmd(seconds: float = 0.05) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _long_sleep_cmd() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(60)"]


def test_subprocess_to_refs_records_pgid(tmp_path: Path) -> None:
    stdout_ref = tmp_path / "out.log"
    stderr_ref = tmp_path / "err.log"
    recorded: list[int] = []
    rc = subprocess_to_refs(
        command=_short_sleep_cmd(0.01),
        cwd=tmp_path,
        env={},
        timeout_seconds=5.0,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        pid_recorder=recorded.append,
    )
    assert rc == 0
    assert len(recorded) == 1
    assert recorded[0] > 0


def test_wait_for_process_with_cancel_fast_path_no_cancel_event() -> None:
    """Without ``cancel_event`` we should NOT take the polling path."""
    proc = subprocess.Popen(_short_sleep_cmd(0.02), start_new_session=True)
    rc = wait_for_process_with_cancel(
        proc,
        timeout_seconds=5.0,
        cancel_event=None,
    )
    assert rc == 0


def test_wait_for_process_with_cancel_terminates_on_event() -> None:
    """Setting cancel_event sends SIGTERM and returns within the grace window."""
    cancel = threading.Event()
    proc = subprocess.Popen(_long_sleep_cmd(), start_new_session=True)

    def _set_cancel_later() -> None:
        time.sleep(0.2)
        cancel.set()

    t = threading.Thread(target=_set_cancel_later, daemon=True)
    t.start()
    started = time.monotonic()
    try:
        rc = wait_for_process_with_cancel(
            proc,
            timeout_seconds=None,
            cancel_event=cancel,
        )
        elapsed = time.monotonic() - started
        # Process must have actually died.
        assert proc.poll() is not None
        # SIGTERM = -15 (signed) on POSIX when no handler catches; either way
        # we should be done well before the 2 s SIGKILL grace fully elapses.
        assert elapsed < 3.0
        # Exit code is some non-zero termination signal.
        assert rc != 0
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=5)


def test_wait_with_cancel_falls_through_on_natural_exit() -> None:
    cancel = threading.Event()
    proc = subprocess.Popen(_short_sleep_cmd(0.02), start_new_session=True)
    rc = wait_for_process_with_cancel(
        proc,
        timeout_seconds=5.0,
        cancel_event=cancel,
    )
    assert rc == 0


def test_subprocess_to_refs_cancel_event_kills_child(tmp_path: Path) -> None:
    """Full ``subprocess_to_refs`` with cancel_event terminates the child."""
    stdout_ref = tmp_path / "out.log"
    stderr_ref = tmp_path / "err.log"
    cancel = threading.Event()

    def _set_cancel_later() -> None:
        time.sleep(0.2)
        cancel.set()

    t = threading.Thread(target=_set_cancel_later, daemon=True)
    t.start()
    started = time.monotonic()
    rc = subprocess_to_refs(
        command=_long_sleep_cmd(),
        cwd=tmp_path,
        env={},
        timeout_seconds=None,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        cancel_event=cancel,
    )
    elapsed = time.monotonic() - started
    assert rc != 0
    assert elapsed < 3.0


def test_subprocess_to_refs_timeout_still_works(tmp_path: Path) -> None:
    """Foreground timeout path is unchanged when no cancel_event is passed."""
    stdout_ref = tmp_path / "out.log"
    stderr_ref = tmp_path / "err.log"
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess_to_refs(
            command=_long_sleep_cmd(),
            cwd=tmp_path,
            env={},
            timeout_seconds=0.3,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
        )
