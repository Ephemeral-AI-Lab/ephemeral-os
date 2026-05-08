"""Natural layer-stack squash trigger coverage for OCC publications."""

from __future__ import annotations

import asyncio

from sandbox.layer_stack.manager import LayerStackManager
from sandbox.occ.changeset.types import ChangesetResult, WriteChange
import sandbox.occ.service as occ_service_module
from sandbox.occ.service import OccService


class _Gitignore:
    def is_ignored(self, _path: str) -> bool:
        return False


def test_occ_publications_auto_squash_without_direct_squash_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(occ_service_module, "AUTO_SQUASH_MAX_DEPTH", 4)
    stack = LayerStackManager(tmp_path / "stack")
    service = OccService(gitignore=_Gitignore(), layer_stack=stack)

    for index in range(8):
        result = asyncio.run(
            service.apply_changeset(
                [
                    WriteChange(
                        path=f"tracked/auto/{index:02d}.txt",
                        final_content=f"auto-{index:02d}\n".encode(),
                    )
                ],
                snapshot=stack.read_active_manifest(),
            )
        )
        assert isinstance(result, ChangesetResult)
        assert result.published_manifest_version is not None

    manifest = stack.read_active_manifest()
    assert manifest.depth <= 4
    assert manifest.version > 8
    for index in range(8):
        assert stack.read_text(f"tracked/auto/{index:02d}.txt") == (
            f"auto-{index:02d}\n",
            True,
        )


def test_auto_squash_preserves_active_lease_view(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(occ_service_module, "AUTO_SQUASH_MAX_DEPTH", 3)
    stack = LayerStackManager(tmp_path / "stack")
    service = OccService(gitignore=_Gitignore(), layer_stack=stack)

    seed = asyncio.run(
        service.apply_changeset(
            [WriteChange(path="tracked/value.txt", final_content=b"base\n")],
            snapshot=stack.read_active_manifest(),
        )
    )
    assert isinstance(seed, ChangesetResult)
    assert seed.published_manifest_version is not None

    lease = stack.acquire_snapshot_lease("held-before-auto-squash")
    try:
        for index in range(6):
            result = asyncio.run(
                service.apply_changeset(
                    [
                        WriteChange(
                            path=f"tracked/burst/{index:02d}.txt",
                            final_content=f"burst-{index:02d}\n".encode(),
                        )
                    ],
                    snapshot=stack.read_active_manifest(),
                )
            )
            assert isinstance(result, ChangesetResult)
            assert result.published_manifest_version is not None

        assert stack.read_active_manifest().depth <= 3
        assert stack.read_text("tracked/value.txt", lease.manifest) == (
            "base\n",
            True,
        )
        assert stack.read_text("tracked/burst/05.txt") == ("burst-05\n", True)
    finally:
        assert stack.release_lease(lease.lease_id) is True

    assert stack.active_lease_count() == 0
