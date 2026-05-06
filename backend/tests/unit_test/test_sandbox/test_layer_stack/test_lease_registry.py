"""Workspace lease registry tests for materialized lowerdir pins."""

from __future__ import annotations

from sandbox.layer_stack.lease_registry import LeaseRegistry
from sandbox.layer_stack.manifest import LayerRef, Manifest


def test_workspace_leases_refcount_materialized_lowerdirs() -> None:
    ids = iter(("lease-a", "lease-b"))
    registry = LeaseRegistry(id_factory=lambda: next(ids), clock=lambda: 10.0)
    manifest = Manifest(
        version=3,
        layers=(LayerRef(layer_id="L000003", path="layers/L000003"),),
    )
    lowerdir = "/tmp/eos/layer-stack/materialized/manifest-000003/lower"

    lease_a = registry.acquire(
        manifest,
        "request-a",
        materialized_lowerdir=lowerdir,
    )
    lease_b = registry.acquire(
        manifest,
        "request-b",
        materialized_lowerdir=lowerdir,
    )

    assert lease_a.manifest.version == 3
    assert lease_a.owner_request_id == "request-a"
    assert registry.lowerdir_refcount(lowerdir) == 2
    assert registry.pinned_lowerdirs() == (lowerdir,)

    assert registry.release(lease_a.lease_id) == lease_a
    assert registry.lowerdir_refcount(lowerdir) == 1
    assert registry.pinned_lowerdirs() == (lowerdir,)

    assert registry.release(lease_b.lease_id) == lease_b
    assert registry.lowerdir_refcount(lowerdir) == 0
    assert registry.pinned_lowerdirs() == ()


def test_pin_lowerdir_attaches_cache_pin_to_existing_manifest_lease() -> None:
    registry = LeaseRegistry(id_factory=lambda: "lease-a", clock=lambda: 10.0)
    manifest = Manifest(
        version=4,
        layers=(LayerRef(layer_id="L000004", path="layers/L000004"),),
    )
    lease = registry.acquire(manifest, "request-a")

    updated = registry.pin_lowerdir(lease.lease_id, "/cache/lower")

    assert updated.materialized_lowerdir == "/cache/lower"
    assert registry.refcount(manifest.layers[0]) == 1
    assert registry.lowerdir_refcount("/cache/lower") == 1

    assert registry.release(lease.lease_id) == updated
    assert registry.refcount(manifest.layers[0]) == 0
    assert registry.lowerdir_refcount("/cache/lower") == 0
