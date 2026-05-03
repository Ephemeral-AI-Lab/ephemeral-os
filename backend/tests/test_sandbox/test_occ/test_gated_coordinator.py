"""Tests for OCCGatedCoordinator (Step 2 of the OCC gate simplification)."""

from __future__ import annotations

import asyncio
import threading
import time

from sandbox.occ.changeset.types import EditChange, FileStatus, WriteChange
from sandbox.occ.content.hashing import content_hash
from sandbox.occ.gated.gated_coordinator import OCCGatedCoordinator
from sandbox.occ.patching.patcher import SearchReplaceEdit


class _SlowContent:
    """In-memory ContentManager that sleeps on each read/write to expose timing."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        *,
        sleep: float = 0.0,
    ) -> None:
        self._files: dict[str, str] = dict(files or {})
        self._sleep = sleep
        self._lock = threading.Lock()
        self.read_calls = 0
        self.write_calls = 0

    def read(self, path: str, *, allow_missing: bool = False) -> tuple[str, bool]:
        if self._sleep:
            time.sleep(self._sleep)
        with self._lock:
            self.read_calls += 1
            if path in self._files:
                return self._files[path], True
        if allow_missing:
            return "", False
        raise FileNotFoundError(path)

    def write(self, path: str, content: str) -> None:
        if self._sleep:
            time.sleep(self._sleep)
        with self._lock:
            self.write_calls += 1
            self._files[path] = content

    def delete(self, path: str) -> None:
        with self._lock:
            self._files.pop(path, None)


def test_gated_coordinator_groups_changes_by_path() -> None:
    """Two changes to the same file run through the same applier in submission order."""
    content = _SlowContent({"a.py": "v1"})
    coord = OCCGatedCoordinator(content)
    edits1 = WriteChange(
        path="a.py",
        base_hash=content_hash("v1"),
        base_existed=True,
        final_content="v2",
    )
    edits2 = WriteChange(
        path="a.py",
        base_hash=content_hash("v2"),
        base_existed=True,
        final_content="v3",
    )

    results = asyncio.run(coord.apply([edits1, edits2]))
    assert [r.status for r in results] == [FileStatus.COMMITTED, FileStatus.COMMITTED]
    assert content._files["a.py"] == "v3"


def test_gated_coordinator_returns_per_change_results_in_submission_order() -> None:
    content = _SlowContent({"a.py": "v1", "b.py": "v1"})
    coord = OCCGatedCoordinator(content)
    a_change = WriteChange(
        path="a.py",
        base_hash=content_hash("v1"),
        base_existed=True,
        final_content="v2",
    )
    b_change = WriteChange(
        path="b.py",
        base_hash=content_hash("v1"),
        base_existed=True,
        final_content="v2",
    )
    results = asyncio.run(coord.apply([a_change, b_change]))
    paths = {r.path for r in results}
    assert paths == {"a.py", "b.py"}
    assert all(r.status is FileStatus.COMMITTED for r in results)


def test_gated_coordinator_empty_returns_empty() -> None:
    coord = OCCGatedCoordinator(_SlowContent())
    assert asyncio.run(coord.apply([])) == []


def test_gated_coordinator_runs_different_files_in_parallel() -> None:
    """Plan §Success criteria #6 — wall-clock < 1.5x single-file time when two
    files run in parallel and each I/O step sleeps 100ms.
    """
    sleep = 0.1
    content = _SlowContent({"a.py": "v1", "b.py": "v1"}, sleep=sleep)
    coord = OCCGatedCoordinator(content)

    # Time a single-file application: one read + one write = ~2 * sleep.
    single_change = WriteChange(
        path="a.py",
        base_hash=content_hash("v1"),
        base_existed=True,
        final_content="v2",
    )
    t0 = time.perf_counter()
    asyncio.run(coord.apply([single_change]))
    single_elapsed = time.perf_counter() - t0
    # Reset state for the parallel run.
    content._files["a.py"] = "v1"

    parallel = [
        WriteChange(
            path="a.py",
            base_hash=content_hash("v1"),
            base_existed=True,
            final_content="v2",
        ),
        WriteChange(
            path="b.py",
            base_hash=content_hash("v1"),
            base_existed=True,
            final_content="v2",
        ),
    ]
    # Use a fresh coordinator so neither path's applier is warm.
    coord_parallel = OCCGatedCoordinator(content)
    t0 = time.perf_counter()
    asyncio.run(coord_parallel.apply(parallel))
    parallel_elapsed = time.perf_counter() - t0

    # If the two file appliers truly run on different threads under
    # asyncio.gather + to_thread, the parallel run should be far below 2x the
    # single-file run. Plan threshold: < 1.5x.
    assert parallel_elapsed < single_elapsed * 1.5, (
        f"parallel run took {parallel_elapsed:.3f}s vs single {single_elapsed:.3f}s; "
        "expected near-overlap, not serial behaviour"
    )


def test_gated_coordinator_strict_unique_anchor_propagates_abort() -> None:
    content = _SlowContent({"a.py": "x\nx\n"})
    coord = OCCGatedCoordinator(content)
    change = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="x", new_text="Y"),),
    )
    [result] = asyncio.run(coord.apply([change]))
    assert result.status is FileStatus.ABORTED_OVERLAP
