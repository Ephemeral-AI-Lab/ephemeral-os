"""Unit tests for daemon-owned SandboxOverlay lifecycle."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.daemon.service import sandbox_overlay as overlay_mod
from sandbox.daemon.service.sandbox_overlay import SandboxOverlay
from sandbox.layer_stack.manifest import LayerRef, Manifest


class _LayerStack:
    def __init__(self, storage_root: Path, manifest: Manifest) -> None:
        self.storage_root = storage_root
        self.manifest = manifest
        self.released: list[str] = []

    def read_active_manifest(self) -> Manifest:
        return self.manifest

    def prepare_workspace_snapshot(
        self,
        *,
        request_id: str,
        lowerdir_root: str | Path | None = None,
        materialize: bool = True,
    ) -> object:
        del request_id, lowerdir_root, materialize
        return SimpleNamespace(
            lease_id=f"lease-{self.manifest.version}",
            manifest=self.manifest,
            layer_paths=tuple(
                (self.storage_root / "layers" / layer.path).as_posix()
                for layer in self.manifest.layers
            ),
        )

    def release_lease(self, *, lease_id: str) -> bool:
        self.released.append(lease_id)
        return True


class _OccClient:
    async def run_maintenance_after_publish(self, *args, **kwargs) -> dict[str, float]:
        del args, kwargs
        return {}


@pytest.mark.asyncio
async def test_start_mounts_active_manifest_and_stop_unmounts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = Manifest(
        version=1,
        layers=(LayerRef(layer_id="L1", path="L1"),),
    )
    layer_stack = _LayerStack(tmp_path / "stack", manifest)
    (layer_stack.storage_root / "layers" / "L1").mkdir(parents=True)
    workspace = tmp_path / "testbed"
    workspace.mkdir()
    mounts: list[tuple[Path, tuple[Path, ...], Path, Path]] = []
    unmounts: list[Path] = []

    def fake_mount_overlay(**kwargs) -> None:
        mounts.append(
            (
                kwargs["workspace_root"],
                kwargs["layer_paths"],
                kwargs["upperdir"],
                kwargs["workdir"],
            )
        )

    monkeypatch.setattr(overlay_mod, "mount_overlay", fake_mount_overlay)
    monkeypatch.setattr(overlay_mod, "umount", lambda path: unmounts.append(path))

    overlay = SandboxOverlay(
        occ_client=_OccClient(),  # type: ignore[arg-type]
        workspace_ref=layer_stack.storage_root.as_posix(),
        layer_stack=layer_stack,
        workspace_root=workspace.as_posix(),
    )

    await overlay.start()
    await overlay.stop()

    assert mounts[0][0] == workspace
    assert len(mounts[0][1]) == 1
    assert all(path.as_posix().startswith("/proc/self/fd/") for path in mounts[0][1])
    assert mounts[0][2].as_posix().startswith("/proc/self/fd/")
    assert mounts[0][3].as_posix().startswith("/proc/self/fd/")
    assert unmounts == [workspace]
    assert layer_stack.released == ["lease-1"]


@pytest.mark.asyncio
async def test_ensure_current_remounts_and_emits_foreign_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Manifest(version=1, layers=(LayerRef(layer_id="L1", path="L1"),))
    second = Manifest(version=2, layers=(LayerRef(layer_id="L2", path="L2"),))
    layer_stack = _LayerStack(tmp_path / "stack", first)
    (layer_stack.storage_root / "layers" / "L1").mkdir(parents=True)
    (layer_stack.storage_root / "layers" / "L2").mkdir(parents=True)
    workspace = tmp_path / "testbed"
    workspace.mkdir()
    mounts: list[tuple[Path, ...]] = []
    unmounts: list[Path] = []

    monkeypatch.setattr(
        overlay_mod,
        "mount_overlay",
        lambda **kwargs: mounts.append(kwargs["layer_paths"]),
    )
    monkeypatch.setattr(overlay_mod, "umount", lambda path: unmounts.append(path))

    overlay = SandboxOverlay(
        occ_client=_OccClient(),  # type: ignore[arg-type]
        workspace_ref=layer_stack.storage_root.as_posix(),
        layer_stack=layer_stack,
        workspace_root=workspace.as_posix(),
    )
    queue = overlay.event_bus.subscribe("test")

    await overlay.start()
    layer_stack.manifest = second
    await overlay.ensure_current(reason="lsp:hover:enter")

    assert len(mounts[-1]) == 1
    assert mounts[-1][0].as_posix().startswith("/proc/self/fd/")
    assert unmounts == [workspace]
    assert layer_stack.released == ["lease-1"]
    event = queue.get_nowait()
    assert event.reason == "foreign_publish"
    assert event.from_version == 1
    assert event.to_version == 2
    await overlay.stop()
