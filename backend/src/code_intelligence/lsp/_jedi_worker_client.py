"""Client for the persistent Jedi worker subprocess.

Manages the lifecycle of a single worker process per :class:`LspClient`:
spawn lazily on first use, serialize requests under a lock, detect
crashes (EOF on stdout, JSON decode error), respawn once automatically,
then surrender to the caller's subprocess-per-call fallback.

Minimal P3 delivery — see ``_jedi_worker`` module docstring for
scope limits (local-mode only, no shadow traffic, no memory ceilings).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKER_SCRIPT = str(Path(__file__).with_name("_jedi_worker.py"))
ENV_FLAG = "CI_JEDI_WORKER_ENABLED"
_CRASH_BACKOFF_SEC = 30.0


def is_enabled() -> bool:
    """Check the env-var kill-switch (default off)."""
    return os.environ.get(ENV_FLAG, "0").strip().lower() in {"1", "true", "yes", "on"}


class WorkerUnavailable(RuntimeError):
    """Raised when the worker is dead and fallback must be used."""


class JediWorkerClient:
    """Owns one long-lived worker process.

    The client is safe to construct eagerly but only spawns on first
    :meth:`request`.
    """

    def __init__(
        self,
        workspace_root: str,
        *,
        worker_script: str | None = None,
        python_executable: str | None = None,
        request_timeout: float = 10.0,
    ) -> None:
        self._workspace_root = str(workspace_root or "")
        self._worker_script = worker_script or WORKER_SCRIPT
        self._python = python_executable or sys.executable or "python3"
        self._request_timeout = float(request_timeout)

        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._seq = 0
        self._crashes_in_window: list[float] = []
        self._dead_until: float = 0.0

    # -- Lifecycle ------------------------------------------------------------

    def _spawn(self) -> subprocess.Popen[str]:
        if self._dead_until > time.time():
            raise WorkerUnavailable("worker in crash-backoff")
        env = os.environ.copy()
        proc = subprocess.Popen(
            [self._python, self._worker_script, self._workspace_root],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )
        try:
            self._send_raw(proc, {"id": "ping", "op": "ping", "args": {}})
            response = self._read_raw(proc)
        except Exception as exc:
            self._kill(proc)
            raise WorkerUnavailable(f"worker ping failed: {exc}") from exc
        if not isinstance(response, dict) or not response.get("ok"):
            self._kill(proc)
            raise WorkerUnavailable(f"worker bad ping response: {response}")
        return proc

    def _ensure_proc(self) -> subprocess.Popen[str]:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return proc
        if proc is not None:
            self._record_crash()
        try:
            self._proc = self._spawn()
        except WorkerUnavailable:
            self._proc = None
            raise
        return self._proc

    def _record_crash(self) -> None:
        now = time.time()
        window_start = now - _CRASH_BACKOFF_SEC
        self._crashes_in_window = [t for t in self._crashes_in_window if t >= window_start]
        self._crashes_in_window.append(now)
        if len(self._crashes_in_window) >= 3:
            self._dead_until = now + _CRASH_BACKOFF_SEC
            logger.warning(
                "jedi worker crashed %d times in %.0fs — backing off until %.0f",
                len(self._crashes_in_window), _CRASH_BACKOFF_SEC, self._dead_until,
            )

    @staticmethod
    def _kill(proc: subprocess.Popen[str]) -> None:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.0)
        except Exception:
            pass

    def shutdown(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                self._send_raw(proc, {"id": "bye", "op": "shutdown", "args": {}})
                proc.wait(timeout=2.0)
        except Exception:
            self._kill(proc)

    # -- Request plumbing -----------------------------------------------------

    @staticmethod
    def _send_raw(proc: subprocess.Popen[str], req: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()

    @staticmethod
    def _read_raw(proc: subprocess.Popen[str]) -> dict[str, Any]:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if not line:
            raise WorkerUnavailable("worker closed stdout (EOF)")
        try:
            return json.loads(line)
        except Exception as exc:
            raise WorkerUnavailable(f"worker emitted non-JSON: {line!r}") from exc

    def request(self, op: str, args: dict[str, Any] | None = None) -> Any:
        """Send one op and return ``result``. Raises :class:`WorkerUnavailable`.

        On a transient crash (EOF, decode error) this method attempts
        exactly one automatic respawn + retry. Persistent crashes latch
        the backoff and raise so the caller falls back to the subprocess
        path.
        """
        if not is_enabled():
            raise WorkerUnavailable("worker disabled (CI_JEDI_WORKER_ENABLED != 1)")
        payload = {"args": args or {}}
        with self._lock:
            for attempt in (0, 1):
                try:
                    proc = self._ensure_proc()
                    self._seq += 1
                    req_id = f"{op}-{self._seq}"
                    self._send_raw(proc, {"id": req_id, "op": op, **payload})
                    response = self._read_raw(proc)
                except WorkerUnavailable:
                    self._teardown_locked()
                    if attempt == 0:
                        continue
                    raise
                if not isinstance(response, dict):
                    self._teardown_locked()
                    if attempt == 0:
                        continue
                    raise WorkerUnavailable("malformed worker response")
                if not response.get("ok"):
                    raise RuntimeError(
                        f"worker op {op!r} failed: {response.get('error')}",
                    )
                return response.get("result")
        raise WorkerUnavailable("unreachable")  # pragma: no cover

    def _teardown_locked(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            self._kill(proc)
