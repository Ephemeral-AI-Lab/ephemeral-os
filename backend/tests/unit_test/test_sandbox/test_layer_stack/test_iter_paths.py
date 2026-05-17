"""``MergedView.iter_paths`` snapshot-wide path enumeration."""

from __future__ import annotations

from pathlib import Path

from sandbox.layer_stack import (
    DeleteLayerChange,
    LayerStack,
    OpaqueDirLayerChange,
    SymlinkLayerChange,
    WriteLayerChange,
)
from sandbox.layer_stack.manifest import empty_manifest


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_iter_paths_empty_manifest_yields_nothing(tmp_path: Path) -> None:
    manager = LayerStack(tmp_path / "stack")
    assert list(manager.iter_paths(empty_manifest())) == []


def test_iter_paths_single_layer_lists_all_files_sorted(tmp_path: Path) -> None:
    manager = LayerStack(tmp_path / "stack")
    manifest = manager.publish_changes(
        [
            WriteLayerChange(
                path="z.txt", source_path=_source(tmp_path, "z.txt", b"z")
            ),
            WriteLayerChange(
                path="a.txt", source_path=_source(tmp_path, "a.txt", b"a")
            ),
            WriteLayerChange(
                path="pkg/b.py",
                source_path=_source(tmp_path, "b.py", b"b"),
            ),
        ]
    )

    assert list(manager.iter_paths(manifest)) == ["a.txt", "pkg/b.py", "z.txt"]


def test_iter_paths_excludes_whiteout_files(tmp_path: Path) -> None:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path="keep.txt",
                source_path=_source(tmp_path, "keep.txt", b"keep"),
            ),
            WriteLayerChange(
                path="gone.txt",
                source_path=_source(tmp_path, "gone.txt", b"gone"),
            ),
        ]
    )
    manifest = manager.publish_changes([DeleteLayerChange(path="gone.txt")])

    assert list(manager.iter_paths(manifest)) == ["keep.txt"]


def test_iter_paths_opaque_dir_masks_older_children(tmp_path: Path) -> None:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path="pkg/old_a.py",
                source_path=_source(tmp_path, "a.py", b"a"),
            ),
            WriteLayerChange(
                path="pkg/old_b.py",
                source_path=_source(tmp_path, "b.py", b"b"),
            ),
            WriteLayerChange(
                path="root.txt",
                source_path=_source(tmp_path, "root.txt", b"root"),
            ),
        ]
    )
    # Layer 2: opaque pkg/ + add a fresh child.
    manifest = manager.publish_changes(
        [
            OpaqueDirLayerChange(path="pkg"),
            WriteLayerChange(
                path="pkg/new.py",
                source_path=_source(tmp_path, "new.py", b"new"),
            ),
        ]
    )

    assert list(manager.iter_paths(manifest)) == ["pkg/new.py", "root.txt"]


def test_iter_paths_top_layer_overrides_lower_same_path(tmp_path: Path) -> None:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path="pkg/value.txt",
                source_path=_source(tmp_path, "base.txt", b"base"),
            )
        ]
    )
    manifest = manager.publish_changes(
        [
            WriteLayerChange(
                path="pkg/value.txt",
                source_path=_source(tmp_path, "top.txt", b"top"),
            ),
            WriteLayerChange(
                path="other.txt",
                source_path=_source(tmp_path, "other.txt", b"other"),
            ),
        ]
    )

    paths = list(manager.iter_paths(manifest))
    # Each path appears at most once even when shadowed.
    assert paths == ["other.txt", "pkg/value.txt"]
    # And the listed entry resolves to the newest layer's content.
    assert manager.read_text("pkg/value.txt", manifest=manifest) == ("top", True)


def test_iter_paths_lists_symlinks_without_following(tmp_path: Path) -> None:
    manager = LayerStack(tmp_path / "stack")
    manifest = manager.publish_changes(
        [
            WriteLayerChange(
                path="target.txt",
                source_path=_source(tmp_path, "target.txt", b"target"),
            ),
            SymlinkLayerChange(
                path="links/current",
                source_path="../target.txt",
            ),
        ]
    )

    paths = list(manager.iter_paths(manifest))
    assert paths == ["links/current", "target.txt"]
    # And the symlink target is exposed via read_symlink, not iter_paths.
    assert manager.read_symlink("links/current", manifest=manifest) == (
        "../target.txt",
        "symlink",
    )


def test_iter_paths_uses_leased_manifest_not_advanced_active(tmp_path: Path) -> None:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path="base.txt",
                source_path=_source(tmp_path, "base.txt", b"base"),
            )
        ]
    )
    lease = manager.acquire_snapshot_lease("iter-test")
    # Publish a newer layer adding a file the lease must NOT see.
    manager.publish_changes(
        [
            WriteLayerChange(
                path="future.txt",
                source_path=_source(tmp_path, "future.txt", b"future"),
            )
        ]
    )

    leased_paths = list(manager.iter_paths(lease.manifest))
    assert leased_paths == ["base.txt"]
    manager.release_lease(lease.lease_id)
