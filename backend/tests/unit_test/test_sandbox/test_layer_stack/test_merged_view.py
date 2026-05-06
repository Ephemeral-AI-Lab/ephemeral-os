"""Merged-view behavior for frozen layer-stack manifests."""

from __future__ import annotations

from pathlib import Path

from sandbox.layer_stack import LayerChange, LayerStackManager


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_read_uses_leased_manifest_not_advanced_active_manifest(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    manager.publish_changes(
        [
            LayerChange(
                path="pkg/value.txt",
                kind="write",
                source_path=_source(tmp_path, "base.txt", b"base"),
            )
        ]
    )
    lease = manager.acquire_snapshot_lease("request-a")

    manager.publish_changes(
        [
            LayerChange(
                path="pkg/value.txt",
                kind="write",
                source_path=_source(tmp_path, "new.txt", b"new"),
            )
        ]
    )

    assert manager.read_text("pkg/value.txt") == ("new", True)
    assert manager.read_text("pkg/value.txt", manifest=lease.manifest) == ("base", True)


def test_whiteout_hides_older_file(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    manager.publish_changes(
        [
            LayerChange(
                path="old.txt",
                kind="write",
                source_path=_source(tmp_path, "old.txt", b"old"),
            )
        ]
    )
    manager.publish_changes([LayerChange(path="old.txt", kind="delete")])

    assert manager.read_bytes("old.txt") == (None, False)
    assert manager.list_dir("") == ()


def test_opaque_dir_hides_older_children(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    manager.publish_changes(
        [
            LayerChange(
                path="pkg/a.py",
                kind="write",
                source_path=_source(tmp_path, "a.py", b"a"),
            ),
            LayerChange(
                path="pkg/b.py",
                kind="write",
                source_path=_source(tmp_path, "b.py", b"b"),
            ),
        ]
    )
    manager.publish_changes(
        [
            LayerChange(path="pkg", kind="opaque_dir"),
            LayerChange(
                path="pkg/new.py",
                kind="write",
                source_path=_source(tmp_path, "new.py", b"new"),
            ),
        ]
    )

    assert manager.read_bytes("pkg/a.py") == (None, False)
    assert manager.list_dir("pkg") == ("new.py",)


def test_materialize_matches_point_reads_and_preserves_symlinks(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    manager.publish_changes(
        [
            LayerChange(
                path="target.txt",
                kind="write",
                source_path=_source(tmp_path, "target.txt", b"target"),
            ),
            LayerChange(path="links/current", kind="symlink", source_path="../target.txt"),
        ]
    )
    destination = tmp_path / "materialized"

    manager.materialize(destination)

    assert (destination / "target.txt").read_text(encoding="utf-8") == "target"
    assert (destination / "links" / "current").is_symlink()
    assert (destination / "links" / "current").readlink().as_posix() == "../target.txt"
    assert manager.read_symlink("links/current") == ("../target.txt", True)
