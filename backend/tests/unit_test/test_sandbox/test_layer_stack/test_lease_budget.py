"""Lease-budget and publish-backpressure tests for layer stacks."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.layer_stack import (
    CommitBackpressureError,
    LayerChange,
    LayerStackManager,
    LeaseBudgetWorker,
)


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_lease_budget_backpressures_when_active_depth_reaches_limit() -> None:
    worker = LeaseBudgetWorker(max_active_depth=2)

    decision = worker.evaluate(active_depth=2)

    assert decision.kind == "backpressure_commits"


def test_lease_budget_zero_depth_is_closed() -> None:
    worker = LeaseBudgetWorker(max_active_depth=0)

    decision = worker.evaluate(active_depth=0)

    assert decision.kind == "backpressure_commits"


def test_lease_budget_backpressures_when_pinned_bytes_reach_limit() -> None:
    worker = LeaseBudgetWorker(max_pinned_bytes=5)

    decision = worker.evaluate(active_depth=1, pinned_bytes=5)

    assert decision.kind == "backpressure_commits"


def test_publish_backpressure_uses_unique_pinned_layer_bytes(tmp_path: Path) -> None:
    manager = LayerStackManager(
        tmp_path / "stack",
        lease_budget=LeaseBudgetWorker(max_pinned_bytes=5),
    )
    manager.publish_changes(
        [
            LayerChange(
                path="payload.txt",
                kind="write",
                source_path=_source(tmp_path, "payload.txt", b"bytes"),
            )
        ]
    )

    lease = manager.acquire_snapshot_lease("request-a")
    try:
        with pytest.raises(CommitBackpressureError):
            manager.publish_changes(
                [
                    LayerChange(
                        path="next.txt",
                        kind="write",
                        source_path=_source(tmp_path, "next.txt", b"next"),
                    )
                ]
            )
    finally:
        manager.release_lease(lease.lease_id)

    manager.publish_changes(
        [
            LayerChange(
                path="next.txt",
                kind="write",
                source_path=_source(tmp_path, "next-retry.txt", b"next"),
            )
        ]
    )
    assert manager.read_text("next.txt") == ("next", True)


def test_publish_backpressure_blocks_before_staging(tmp_path: Path) -> None:
    manager = LayerStackManager(
        tmp_path / "stack",
        lease_budget=LeaseBudgetWorker(max_active_depth=1),
    )
    manager.publish_changes(
        [
            LayerChange(
                path="first.txt",
                kind="write",
                source_path=_source(tmp_path, "first.txt", b"first"),
            )
        ]
    )

    with pytest.raises(CommitBackpressureError):
        manager.publish_changes(
            [
                LayerChange(
                    path="second.txt",
                    kind="write",
                    source_path=_source(tmp_path, "second.txt", b"second"),
                )
            ]
        )

    assert tuple((manager.storage_root / "staging").iterdir()) == ()
    assert manager.read_text("second.txt") == ("", False)
