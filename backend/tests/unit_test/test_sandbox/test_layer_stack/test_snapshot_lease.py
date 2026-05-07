"""Snapshot lease pinning tests for layer stacks."""

from __future__ import annotations

import shutil
from pathlib import Path

from sandbox.layer_stack import LayerChange, LayerStackManager


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_acquire_and_release_pin_exact_layer_refs(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    manifest = manager.publish_changes(
        [
            LayerChange(
                path="a.txt",
                kind="write",
                source_path=_source(tmp_path, "a.txt", b"a"),
            )
        ]
    )
    top_layer = manifest.layers[0]

    lease_a = manager.acquire_snapshot_lease("request-a")
    lease_b = manager.acquire_snapshot_lease("request-b")

    assert lease_a.manifest == manifest
    assert lease_b.manifest == manifest
    assert manager.pinned_layers() == (top_layer,)

    assert manager.release_lease(lease_a.lease_id) is True
    assert manager.pinned_layers() == (top_layer,)
    assert manager.release_lease(lease_a.lease_id) is False
    assert manager.pinned_layers() == (top_layer,)

    assert manager.release_lease(lease_b.lease_id) is True
    assert manager.pinned_layers() == ()


def test_releasing_old_snapshot_does_not_unpin_new_active_layer(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    manager.publish_changes(
        [
            LayerChange(
                path="a.txt",
                kind="write",
                source_path=_source(tmp_path, "old.txt", b"old"),
            )
        ]
    )
    old_lease = manager.acquire_snapshot_lease("old-request")
    new_manifest = manager.publish_changes(
        [
            LayerChange(
                path="b.txt",
                kind="write",
                source_path=_source(tmp_path, "new.txt", b"new"),
            )
        ]
    )
    new_lease = manager.acquire_snapshot_lease("new-request")

    assert set(manager.pinned_layers()) == set(new_manifest.layers)

    manager.release_lease(old_lease.lease_id)

    assert set(manager.pinned_layers()) == set(new_manifest.layers)
    assert manager.release_lease(new_lease.lease_id) is True
    assert manager.pinned_layers() == ()


def test_prepare_workspace_snapshot_returns_distinct_transient_lowerdirs_per_lease(
    tmp_path: Path,
) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    manager.publish_changes(
        [
            LayerChange(
                path="src/app.py",
                kind="write",
                source_path=_source(tmp_path, "app.py", b"print('hi')\n"),
            )
        ]
    )

    first = manager.prepare_workspace_snapshot("request-a")
    second = manager.prepare_workspace_snapshot("request-b")

    assert first.manifest_version == second.manifest_version
    assert first.root_hash == second.root_hash
    assert first.lowerdir != second.lowerdir
    assert Path(first.lowerdir).is_dir()
    assert Path(second.lowerdir).is_dir()
    assert (Path(first.lowerdir) / "src" / "app.py").read_text(
        encoding="utf-8",
    ) == "print('hi')\n"

    # release_lease drops bookkeeping; the transient lowerdir is the caller's
    # responsibility. Simulate the caller cleanup that command_exec_server does.
    assert manager.release_lease(first.lease_id) is True
    shutil.rmtree(Path(first.lowerdir).parent, ignore_errors=True)
    assert Path(first.lowerdir).exists() is False

    assert manager.release_lease(second.lease_id) is True
    shutil.rmtree(Path(second.lowerdir).parent, ignore_errors=True)
    assert Path(second.lowerdir).exists() is False


def test_layer_stack_manager_purges_legacy_materialized_dir_on_init(
    tmp_path: Path,
) -> None:
    stack = tmp_path / "stack"
    legacy = stack / "materialized" / "manifest-000001" / "lower"
    legacy.mkdir(parents=True)
    (legacy / "marker").write_text("stale\n", encoding="utf-8")

    manager = LayerStackManager(stack)
    assert (stack / "materialized").exists() is False
    # Sanity: subsequent prepare/release cycle never recreates ``materialized/``.
    manifest = manager.publish_changes(
        [
            LayerChange(
                path="a.txt",
                kind="write",
                source_path=_source(tmp_path, "a.txt", b"a"),
            )
        ]
    )
    del manifest
    result = manager.prepare_workspace_snapshot("request-x")
    manager.release_lease(result.lease_id)
    shutil.rmtree(Path(result.lowerdir).parent, ignore_errors=True)
    assert (stack / "materialized").exists() is False
