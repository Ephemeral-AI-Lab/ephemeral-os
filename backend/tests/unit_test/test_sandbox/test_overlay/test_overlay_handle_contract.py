"""OverlayHandle contract tests."""

from __future__ import annotations

import shutil
from pathlib import Path

from sandbox.overlay.handle import OverlayHandle


def test_overlay_handle_release_is_idempotent(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    upperdir = run_dir / "upper"
    workdir = run_dir / "work"
    upperdir.mkdir(parents=True)
    workdir.mkdir()
    releases: list[str] = []
    cleanups: list[Path] = []

    def release() -> None:
        releases.append("lease-1")
        cleanups.append(run_dir)
        shutil.rmtree(run_dir, ignore_errors=True)

    handle = OverlayHandle(
        workspace_root="/testbed",
        layer_paths=("/layers/L1",),
        upperdir=upperdir,
        workdir=workdir,
        lease_id="lease-1",
        holder_pid=None,
        run_dir=run_dir,
        snapshot_manifest=None,
        _release=release,
    )

    handle.release()
    handle.release()

    assert releases == ["lease-1"]
    assert cleanups == [run_dir]
    assert handle.released is True
    assert not run_dir.exists()
