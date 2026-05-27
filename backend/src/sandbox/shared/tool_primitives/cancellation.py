"""Verb-supplied cancellation hooks for namespace execution."""

from __future__ import annotations

import os
import signal
import threading
from typing import Protocol


class VerbCancellation(Protocol):
    """Cancellation hook supplied by a tool verb."""

    @property
    def cancel_event(self) -> threading.Event | None: ...

    def record_pid(self, pid: int) -> None: ...

    def on_cancel(self) -> None: ...


class _NoopCancellation:
    @property
    def cancel_event(self) -> threading.Event | None:
        return None

    def record_pid(self, pid: int) -> None:
        return

    def on_cancel(self) -> None:
        return


class ShellPgrpCancellation:
    """Signal the namespace child process group when a shell request is cancelled."""

    def __init__(self) -> None:
        self._cancel_event = threading.Event()
        self._pgrp = 0

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def record_pid(self, pid: int) -> None:
        self._pgrp = int(pid)
        if self._cancel_event.is_set() and self._pgrp:
            _signal_pgrp(self._pgrp, signal.SIGTERM)

    def on_cancel(self) -> None:
        self._cancel_event.set()
        if self._pgrp:
            _signal_pgrp(self._pgrp, signal.SIGTERM)


def _signal_pgrp(pgrp: int, sig: int) -> None:
    try:
        os.killpg(pgrp, sig)
    except (ProcessLookupError, PermissionError):
        pass


NO_OP_CANCELLATION: VerbCancellation = _NoopCancellation()


__all__ = [
    "NO_OP_CANCELLATION",
    "ShellPgrpCancellation",
    "VerbCancellation",
]
