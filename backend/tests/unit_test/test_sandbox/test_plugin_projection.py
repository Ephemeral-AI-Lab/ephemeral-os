"""Unit tests for sandbox.ephemeral_workspace.plugin.projection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sandbox.layer_stack import WriteLayerChange
from sandbox.ephemeral_workspace.plugin.projection import (
    WorkspaceProjection,
    build_manifest_key,
)


def _seed_source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_acquire_returns_handle_with_manifest_key(tmp_path: Path) -> None:
    layer_stack_root = tmp_path / "stack"
    layer_stack_root.mkdir()
    projection = WorkspaceProjection(layer_stack_root)
    # Publish one layer through the underlying manager so the manifest is
    # non-empty.
    projection._manager.publish_changes(
        [
            WriteLayerChange(
                path="src/app.py",
                source_path=_seed_source(
                    tmp_path, "app.py", b"print('hi')\n"
                ),
            )
        ]
    )

    handle = projection.acquire("test-request")

    assert handle.lease_id
    assert handle.manifest_version >= 1
    assert handle.root_hash
    assert handle.manifest_key == build_manifest_key(
        handle.root_hash, handle.manifest_version
    )
    assert Path(handle.lowerdir).is_dir()
    assert projection.active_lease_count() == 1

    handle.release()
    assert projection.active_lease_count() == 0


def test_release_is_idempotent(tmp_path: Path) -> None:
    projection = WorkspaceProjection(tmp_path / "stack")
    projection._manager.publish_changes(
        [
            WriteLayerChange(
                path="a.txt",
                source_path=_seed_source(tmp_path, "a.txt", b"a"),
            )
        ]
    )
    handle = projection.acquire("test-request")
    handle.release()
    handle.release()  # second release is a no-op
    assert projection.active_lease_count() == 0


def test_manifest_key_changes_after_publish(tmp_path: Path) -> None:
    projection = WorkspaceProjection(tmp_path / "stack")
    projection._manager.publish_changes(
        [
            WriteLayerChange(
                path="a.txt",
                source_path=_seed_source(tmp_path, "a.txt", b"a"),
            )
        ]
    )
    first = projection.acquire("first")
    first_key = first.manifest_key

    projection._manager.publish_changes(
        [
            WriteLayerChange(
                path="b.txt",
                source_path=_seed_source(tmp_path, "b.txt", b"b"),
            )
        ]
    )
    second = projection.acquire("second")

    assert second.manifest_key != first_key
    assert second.manifest_version == first.manifest_version + 1

    first.release()
    second.release()
    assert projection.active_lease_count() == 0


def test_active_manifest_key_matches_handle(tmp_path: Path) -> None:
    projection = WorkspaceProjection(tmp_path / "stack")
    projection._manager.publish_changes(
        [
            WriteLayerChange(
                path="a.txt",
                source_path=_seed_source(tmp_path, "a.txt", b"a"),
            )
        ]
    )
    handle = projection.acquire("probe")
    assert projection.active_manifest_key() == handle.manifest_key
    handle.release()


def test_acquire_retries_transient_missing_layer_file(tmp_path: Path) -> None:
    class FlakyManager:
        def __init__(self) -> None:
            self.calls = 0
            self.released: list[str] = []

        def prepare_workspace_snapshot(self, *, owner_request_id: str):
            self.calls += 1
            if self.calls == 1:
                raise FileNotFoundError("layer file disappeared during materialize")
            lowerdir = tmp_path / "lower"
            lowerdir.mkdir()
            return SimpleNamespace(
                lease_id=f"lease-{owner_request_id}",
                manifest_version=3,
                root_hash="abc123",
                lowerdir=lowerdir.as_posix(),
            )

        def release_lease(self, lease_id: str) -> None:
            self.released.append(lease_id)

    manager = FlakyManager()
    projection = WorkspaceProjection(tmp_path / "stack", manager=manager)  # type: ignore[arg-type]

    handle = projection.acquire("lsp")

    assert manager.calls == 2
    assert handle.lease_id == "lease-lsp"
    handle.release()
    assert manager.released == ["lease-lsp"]
