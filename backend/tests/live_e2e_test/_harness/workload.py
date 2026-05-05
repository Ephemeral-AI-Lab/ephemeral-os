"""Workload generators for live_e2e_test layer-stack suites.

Provides path/content helpers and lease/squash workload primitives the
``layer_stack/`` suite uses to drive :class:`LayerStackManager` without
each test re-implementing the same boilerplate.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator, Sequence
from pathlib import Path

from sandbox.layer_stack.changes import LayerChange
from sandbox.layer_stack.lease_registry import Lease
from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack_manager import LayerStackManager


def unique_path(prefix: str = "wl", suffix: str = ".txt") -> str:
    return f"{prefix}/{secrets.token_hex(4)}{suffix}"


def filled_content(seed: str, size: int) -> str:
    base = (seed + "\n").encode("utf-8")
    out = bytearray()
    while len(out) < size:
        out.extend(base)
    return out[:size].decode("utf-8", errors="replace")


def path_stream(count: int, prefix: str = "wl") -> Iterator[str]:
    for _ in range(count):
        yield unique_path(prefix)


def make_write_change(
    payload_dir: Path,
    name: str,
    body: str,
    *,
    layer_path: str | None = None,
) -> LayerChange:
    """Build a single ``write`` :class:`LayerChange` backed by an on-disk payload."""
    payload = payload_dir / f"payload-{name}"
    payload.write_text(body, encoding="utf-8")
    return LayerChange(
        path=layer_path or f"workload/{name}.txt",
        kind="write",
        source_path=str(payload),
    )


def commit_layers(
    manager: LayerStackManager,
    payload_dir: Path,
    count: int,
    *,
    prefix: str = "wl",
    body_factory: "callable[[int], str] | None" = None,
) -> Manifest:
    """Burst-commit ``count`` single-change layers; return the final manifest."""
    payload_dir.mkdir(parents=True, exist_ok=True)
    factory = body_factory or (lambda i: f"body-{i}\n")
    for index in range(count):
        change = make_write_change(
            payload_dir,
            f"{prefix}{index:04d}",
            factory(index),
            layer_path=f"{prefix}/{prefix}{index:04d}.txt",
        )
        manager.publish_changes([change])
    return manager.read_active_manifest()


def commit_layer(
    manager: LayerStackManager,
    payload_dir: Path,
    name: str,
    *,
    body: str | None = None,
    layer_path: str | None = None,
) -> Manifest:
    """Commit a single named layer; return the post-commit manifest."""
    payload_dir.mkdir(parents=True, exist_ok=True)
    change = make_write_change(
        payload_dir,
        name,
        body if body is not None else f"body-{name}\n",
        layer_path=layer_path,
    )
    return manager.publish_changes([change])


def acquire_lease(manager: LayerStackManager, owner_id: str) -> Lease:
    return manager.acquire_snapshot_lease(owner_id)


def release_lease(manager: LayerStackManager, lease: Lease) -> bool:
    return manager.release_lease(lease.lease_id)


def squash_to(
    manager: LayerStackManager,
    *,
    max_depth: int,
    collect_garbage: bool = True,
) -> Manifest | None:
    """Trigger a squash pass; thin wrapper for symmetry with the other helpers."""
    return manager.squash(max_depth=max_depth, collect_garbage=collect_garbage)


def layer_paths(manifest: Manifest) -> Sequence[str]:
    return tuple(layer.path for layer in manifest.layers)


__all__ = [
    "acquire_lease",
    "commit_layer",
    "commit_layers",
    "filled_content",
    "layer_paths",
    "make_write_change",
    "path_stream",
    "release_lease",
    "squash_to",
    "unique_path",
]
