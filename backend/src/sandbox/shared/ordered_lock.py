"""Lock-order-asserting ``asyncio.Lock`` wrapper for Phase 4 §AC9.

Lives in ``shared`` so both ``sandbox.daemon`` (entry-side ``entry_lock``)
and ``sandbox.isolated_workspace`` (the pipeline ``_map_lock``) can use it
without re-introducing the daemon-↔-pipeline import cycle.

Outside ``EOS_TEST_MODE=true`` the wrapper is a near-pass-through:
``__aenter__`` skips the per-task acquisition bookkeeping and the order
assertion is a single env-var read returning early. Production cost is
one extra attribute lookup per ``async with``.

In test mode, every acquire registers ``(lock_name, monotonic_ts)`` on
the current task's stack; every release pops it. Acquiring an inner
lock without the required outer raises ``AssertionError`` so the test
fails loudly rather than masking the violation.
"""

from __future__ import annotations

import asyncio
import os

from sandbox.shared.clock import monotonic_now


_TEST_MODE_ENV = "EOS_TEST_MODE"

# Per-task acquisition stack: list of ``(lock_name, monotonic_ts)``.
_LOCK_ACQUISITIONS: dict[int, list[tuple[str, float]]] = {}

# Required outer locks for any given lock name. Lock-order rule (AC9):
# ``entry_lock`` must be acquired before ``_map_lock`` when both are
# held by the same task.
_OUTER_LOCKS: dict[str, frozenset[str]] = {
    "_map_lock": frozenset({"entry_lock"}),
}


def _is_test_mode() -> bool:
    return os.environ.get(_TEST_MODE_ENV, "").strip().lower() == "true"


def _task_acquisition_stack() -> list[tuple[str, float]] | None:
    task = asyncio.current_task()
    if task is None:
        return None
    return _LOCK_ACQUISITIONS.setdefault(id(task), [])


def _assert_lock_order_in_test_mode(lock_name: str) -> None:
    if not _is_test_mode():
        return
    stack = _task_acquisition_stack()
    if stack is None:
        return
    required_outer = _OUTER_LOCKS.get(lock_name, frozenset())
    if not required_outer:
        return
    held = {name for name, _ in stack}
    missing = required_outer - held
    if missing:
        raise AssertionError(
            f"lock-order violation: acquiring {lock_name!r} requires "
            f"outer {sorted(missing)!r} (task already holds {sorted(held)!r})"
        )


def _register_lock_acquisition(lock_name: str) -> None:
    if not _is_test_mode():
        return
    stack = _task_acquisition_stack()
    if stack is None:
        return
    stack.append((lock_name, monotonic_now()))


def _unregister_lock_acquisition(lock_name: str) -> None:
    if not _is_test_mode():
        return
    task = asyncio.current_task()
    if task is None:
        return
    stack = _LOCK_ACQUISITIONS.get(id(task))
    if stack is None:
        return
    for index in range(len(stack) - 1, -1, -1):
        if stack[index][0] == lock_name:
            del stack[index]
            break
    if not stack:
        _LOCK_ACQUISITIONS.pop(id(task), None)


class OrderedLock:
    """``asyncio.Lock`` wrapper that records acquisitions for AC9 assertions."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    async def acquire(self) -> bool:
        _assert_lock_order_in_test_mode(self._name)
        await self._lock.acquire()
        _register_lock_acquisition(self._name)
        return True

    def release(self) -> None:
        _unregister_lock_acquisition(self._name)
        self._lock.release()

    async def __aenter__(self) -> "OrderedLock":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()


__all__ = ["OrderedLock"]
