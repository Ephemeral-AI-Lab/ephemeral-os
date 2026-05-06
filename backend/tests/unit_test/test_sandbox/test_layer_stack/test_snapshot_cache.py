"""Materialized lowerdir cache tests for layer-stack snapshots."""

from __future__ import annotations

from pathlib import Path

from sandbox.layer_stack import LayerChange, LayerStackManager
from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.snapshot_cache import MaterializedSnapshotCache


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_prepare_workspace_snapshot_reuses_lowerdir_and_pins_until_release(
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

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.manifest_version == second.manifest_version
    assert first.root_hash == second.root_hash
    assert first.lowerdir == second.lowerdir
    assert (Path(first.lowerdir) / "src" / "app.py").read_text(
        encoding="utf-8",
    ) == "print('hi')\n"
    assert manager.lowerdir_refcount(first.lowerdir) == 2

    metrics = manager.lowerdir_cache_metrics()
    assert metrics.hits == 1
    assert metrics.misses == 1
    assert metrics.materialized_bytes >= len(b"print('hi')\n")
    assert metrics.last_lookup_s >= 0

    assert manager.release_lease(first.lease_id) is True
    assert manager.lowerdir_refcount(first.lowerdir) == 1
    gc_with_second_lease = manager.collect_garbage(young_staging_age_seconds=0)
    assert Path(first.lowerdir).is_dir()
    assert Path(first.lowerdir).parent.name not in (
        gc_with_second_lease.orphan_lowerdirs_removed
    )

    assert manager.release_lease(second.lease_id) is True
    gc_after_release = manager.collect_garbage(young_staging_age_seconds=0)

    assert Path(first.lowerdir).exists() is False
    assert Path(first.lowerdir).parent.name in gc_after_release.orphan_lowerdirs_removed


def test_cache_hit_does_not_rematerialize_payload(tmp_path: Path) -> None:
    calls = 0

    def materialize(lowerdir: Path, manifest: Manifest) -> None:
        del manifest
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("cache hit should not call the materializer")
        lowerdir.mkdir(parents=True)
        (lowerdir / "README.md").write_text("cached\n", encoding="utf-8")

    cache = MaterializedSnapshotCache(
        tmp_path / "stack",
        materializer=materialize,
        clock=lambda: 123.0,
    )
    manifest = Manifest(version=7, layers=())
    root_hash = "a" * 64

    miss = cache.get_or_create(manifest, root_hash=root_hash)
    hit = cache.get_or_create(manifest, root_hash=root_hash)

    assert miss.cache_hit is False
    assert hit.cache_hit is True
    assert calls == 1
    assert miss.snapshot.lowerdir == hit.snapshot.lowerdir
    assert "layer_stack.snapshot_cache.materialize_s" not in hit.timings
