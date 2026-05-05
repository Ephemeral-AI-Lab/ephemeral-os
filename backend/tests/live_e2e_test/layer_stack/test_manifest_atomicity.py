"""E9 — manifest atomicity & fsck cleanup invariants.

Backs §4.1 of ``../../live-e2e-test-suite-plan.md``. All assertions are
host-side concurrency properties of ``LayerStackManager``; the live
sandbox is held up by the session fixture only to keep the gate honest.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from sandbox.layer_stack.changes import LayerChange
from sandbox.layer_stack.manifest import (
    LayerRef,
    Manifest,
    manifest_path,
    read_manifest,
    write_manifest_atomic,
)

from .._harness.assertions import assert_manifest_layers_referenced_on_disk
from .._harness.sandbox_fixture import SandboxHandle


def _write_change(tmp_path: Path, name: str, body: str) -> LayerChange:
    payload = tmp_path / f"payload-{name}"
    payload.write_text(body, encoding="utf-8")
    return LayerChange(
        path=f"workload/{name}.txt", kind="write", source_path=str(payload)
    )


def test_manifest_swap_is_atomic_under_concurrent_readers(
    layer_stack_sandbox: SandboxHandle, tmp_path: Path
) -> None:
    """Concurrent readers must never observe a manifest pointing at a
    missing layer or a partially-written manifest file."""
    manager = layer_stack_sandbox.layer_stack
    assert manager is not None

    n_writes = 50
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()

    stop = threading.Event()
    torn_observations: list[str] = []
    reader_iters = [0]

    def reader() -> None:
        while not stop.is_set():
            manifest = manager.read_active_manifest()
            for layer in manifest.layers:
                layer_dir = manager.storage_root / layer.path
                if not layer_dir.is_dir():
                    torn_observations.append(
                        f"v={manifest.version} missing {layer.layer_id}@{layer.path}"
                    )
                    return
            reader_iters[0] += 1

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for thread in readers:
        thread.start()
    try:
        for index in range(n_writes):
            change = _write_change(payload_dir, f"w{index:03d}", f"body-{index}\n")
            manager.publish_changes([change])
    finally:
        stop.set()
        for thread in readers:
            thread.join(timeout=5.0)

    assert torn_observations == [], (
        f"readers observed torn manifests: {torn_observations[:5]}"
    )
    assert reader_iters[0] > 0, "readers never sampled a manifest"
    final = manager.read_active_manifest()
    assert final.depth == n_writes
    assert_manifest_layers_referenced_on_disk(manager, final)


def test_orphan_staging_dirs_swept_by_fsck(
    layer_stack_sandbox: SandboxHandle, tmp_path: Path
) -> None:
    """fsck must remove abandoned staging dirs but leave manifest layers
    and young (in-flight) staging dirs alone."""
    manager = layer_stack_sandbox.layer_stack
    assert manager is not None

    change = _write_change(tmp_path, "live", "live-body\n")
    manager.publish_changes([change])
    expected_layers = {layer.path for layer in manager.read_active_manifest().layers}

    staging_root = manager.storage_root / "staging"
    orphan_old = staging_root / "S00abandoned"
    orphan_old.mkdir(parents=True)
    (orphan_old / "leftover").write_text("x", encoding="utf-8")
    # Backdate well past the young-staging window so fsck collects it.
    old = time.time() - 24 * 3600
    import os

    os.utime(orphan_old, (old, old))

    young = staging_root / "S00inflight"
    young.mkdir()
    (young / "wip").write_text("y", encoding="utf-8")

    fsck = manager.collect_garbage()

    assert orphan_old.name in fsck.orphan_staging_removed
    assert not orphan_old.exists()
    assert young.exists(), "young staging dirs must be preserved"
    assert young.name not in fsck.orphan_staging_removed

    surviving_layers = {
        layer.path for layer in manager.read_active_manifest().layers
    }
    assert surviving_layers == expected_layers
    for layer_path in surviving_layers:
        assert (manager.storage_root / layer_path).is_dir()


def test_manifest_referencing_missing_layer_is_hard_error(
    layer_stack_sandbox: SandboxHandle,
) -> None:
    """A manifest written with a dangling layer ref must be flagged by
    fsck as ``missing_active_layers`` and refuse to read silently."""
    manager = layer_stack_sandbox.layer_stack
    assert manager is not None

    rogue = Manifest(
        version=99,
        layers=(LayerRef(layer_id="L_missing", path="layers/L_missing"),),
    )
    write_manifest_atomic(manifest_path(manager.storage_root), rogue)

    reread = read_manifest(manifest_path(manager.storage_root))
    assert reread == rogue

    fsck = manager.collect_garbage()
    missing = {layer.layer_id for layer in fsck.missing_active_layers}
    assert "L_missing" in missing, (
        f"fsck must surface dangling layer refs; got missing={missing!r}"
    )

    with pytest.raises(AssertionError, match="missing layers"):
        assert_manifest_layers_referenced_on_disk(manager, reread)
