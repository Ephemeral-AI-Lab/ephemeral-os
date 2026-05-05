"""Phase 4 — path-bucketed commit-gate tests.

Replaces the prior single-asyncio.Lock per ``layer_stack_root`` with N
hashed buckets. Two callers with disjoint paths must take different
buckets and proceed concurrently; two callers with the same path must
serialize. Lock acquisition is in sorted bucket-id order so callers
that span multiple buckets cannot deadlock against each other.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sandbox.runtime import api_handlers


@pytest.fixture(autouse=True)
def _isolate_buckets() -> None:
    saved = dict(api_handlers._PROCESS_COMMIT_LOCK_BUCKETS)
    api_handlers._PROCESS_COMMIT_LOCK_BUCKETS.clear()
    try:
        yield
    finally:
        api_handlers._PROCESS_COMMIT_LOCK_BUCKETS.clear()
        api_handlers._PROCESS_COMMIT_LOCK_BUCKETS.update(saved)


def test_bucket_locks_are_lazy_and_stable(tmp_path: Path) -> None:
    a = api_handlers._bucket_locks(tmp_path)
    b = api_handlers._bucket_locks(tmp_path)
    assert a is b
    assert len(a) == api_handlers._PROCESS_COMMIT_BUCKETS
    assert all(isinstance(lock, asyncio.Lock) for lock in a)


def test_bucket_indices_for_paths_sorted_and_unique() -> None:
    indices = api_handlers._bucket_indices_for_paths(["a.txt", "a.txt", "b.txt"])
    assert tuple(sorted(set(indices))) == indices


def test_bucket_indices_empty_paths_falls_back_to_zero() -> None:
    """An overlay capture with no concrete path still needs *a* bucket."""
    assert api_handlers._bucket_indices_for_paths(()) == (0,)
    assert api_handlers._bucket_indices_for_paths(None) == (0,)


async def test_disjoint_paths_proceed_concurrently(tmp_path: Path) -> None:
    """Two callers with disjoint paths must not block each other.

    If they did (i.e. shared a single Lock), the second caller would
    enter its critical section only after the first released — observable
    by the order in which they record their entry timestamps.
    """
    held_event = asyncio.Event()
    other_acquired = asyncio.Event()

    # Hash collisions on a 16-wide bucket space are common, so probe a
    # small batch of distinct paths until we find a pair that lands in
    # different buckets (within one Python process the hash is stable).
    paths_a = ["alpha-0.py"]
    paths_b: list[str] | None = None
    for i in range(1, 64):
        candidate = [f"bravo-{i}.py"]
        if api_handlers._bucket_indices_for_paths(
            candidate
        ) != api_handlers._bucket_indices_for_paths(paths_a):
            paths_b = candidate
            break
    assert paths_b is not None, "could not find a path pair in distinct buckets"

    async def caller_a() -> None:
        async with api_handlers._process_commit_gate(tmp_path, paths_a):
            held_event.set()
            await asyncio.wait_for(other_acquired.wait(), timeout=1.0)

    async def caller_b() -> None:
        await held_event.wait()
        async with api_handlers._process_commit_gate(tmp_path, paths_b):
            other_acquired.set()

    await asyncio.wait_for(asyncio.gather(caller_a(), caller_b()), timeout=2.0)


async def test_same_path_callers_serialize(tmp_path: Path) -> None:
    """Two callers writing the same path land in the same bucket and
    must serialize."""
    enter_order: list[str] = []
    leave_order: list[str] = []

    async def caller(name: str) -> None:
        async with api_handlers._process_commit_gate(tmp_path, ["same.py"]):
            enter_order.append(name)
            await asyncio.sleep(0.01)
            leave_order.append(name)

    await asyncio.gather(caller("a"), caller("b"), caller("c"))
    # No interleaving inside the critical section: a sequence of
    # enters and leaves must be paired in the same order.
    assert enter_order == leave_order


async def test_multi_bucket_caller_does_not_deadlock(tmp_path: Path) -> None:
    """Caller-1 holds buckets {3, 7}; caller-2 wants buckets {7, 3}.

    Sorted-acquire makes both pick up bucket 3 first, so caller-2 simply
    blocks until caller-1 releases — no deadlock.
    """
    # Pick two paths that fall into distinct buckets so this test actually
    # exercises the multi-bucket acquisition path.
    base = "deadlock-base"
    other: str | None = None
    for i in range(64):
        candidate = f"deadlock-{i}"
        if api_handlers._bucket_indices_for_paths(
            [candidate]
        ) != api_handlers._bucket_indices_for_paths([base]):
            other = candidate
            break
    assert other is not None, "could not find a multi-bucket path pair"
    paths_1 = [base, other]
    paths_2 = [other, base]
    indices_1 = api_handlers._bucket_indices_for_paths(paths_1)
    indices_2 = api_handlers._bucket_indices_for_paths(paths_2)
    assert indices_1 == indices_2  # sorted → identical acquisition order
    assert len(indices_1) == 2

    progress: list[str] = []

    async def first() -> None:
        async with api_handlers._process_commit_gate(tmp_path, paths_1):
            progress.append("first.in")
            await asyncio.sleep(0.02)
            progress.append("first.out")

    async def second() -> None:
        await asyncio.sleep(0.005)
        async with api_handlers._process_commit_gate(tmp_path, paths_2):
            progress.append("second.in")

    await asyncio.wait_for(asyncio.gather(first(), second()), timeout=2.0)
    assert progress == ["first.in", "first.out", "second.in"]


async def test_gate_releases_buckets_on_exception(tmp_path: Path) -> None:
    """An exception inside the gate must not leak a held lock."""

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        async with api_handlers._process_commit_gate(tmp_path, ["foo"]):
            raise _Boom

    # If a lock leaked, this acquire would hang. Bound it tightly.
    async with asyncio.timeout(0.5):
        async with api_handlers._process_commit_gate(tmp_path, ["foo"]):
            pass
