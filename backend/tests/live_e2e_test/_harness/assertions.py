"""Cross-cutting invariants used by every live suite.

Helpers only realize what the current slice needs; the rest are stubs
that raise ``NotImplementedError`` so the contract is visible without
forcing the harness to ship dead implementations. Suites add real
implementations as they land.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sandbox.layer_stack.manifest import LayerRef, Manifest
from sandbox.layer_stack.stack_manager import LayerStackManager


def assert_manifest_depth_within(
    manager: LayerStackManager, lo: int, hi: int
) -> Manifest:
    manifest = manager.read_active_manifest()
    if not lo <= manifest.depth <= hi:
        raise AssertionError(
            f"manifest depth {manifest.depth} outside [{lo}, {hi}]"
        )
    return manifest


def assert_manifest_layers_referenced_on_disk(
    manager: LayerStackManager, manifest: Manifest | None = None
) -> None:
    """Every manifest layer must point at an existing layer dir."""
    target = manifest if manifest is not None else manager.read_active_manifest()
    fsck = manager.collect_garbage()
    missing: tuple[LayerRef, ...] = fsck.missing_active_layers
    if missing:
        raise AssertionError(
            "manifest references missing layers: "
            + ", ".join(f"{layer.layer_id}@{layer.path}" for layer in missing)
            + f" (manifest version={target.version})"
        )


def assert_no_orphan_layers(manager: LayerStackManager) -> None:
    fsck = manager.collect_garbage()
    if fsck.orphan_layers_removed or fsck.orphan_staging_removed:
        raise AssertionError(
            "fsck removed orphans on a clean stack: "
            f"layers={fsck.orphan_layers_removed!r} "
            f"staging={fsck.orphan_staging_removed!r}"
        )


def assert_no_torn_reads(captures: Iterable[Mapping[str, Any]]) -> None:
    raise NotImplementedError(
        "torn-read detector lands with overlay/integrated suites"
    )


def assert_accepts_visible_rejects_invisible(
    captures: Iterable[Mapping[str, Any]],
    final_view: Mapping[str, Any],
) -> None:
    raise NotImplementedError(
        "accept/reject reconciliation lands with the integrated suite"
    )


def assert_classification_pure(captures: Iterable[Mapping[str, Any]]) -> None:
    raise NotImplementedError(
        "gitignore-classification leak check lands with the occ suite"
    )


def assert_telemetry_present(result: Mapping[str, Any]) -> None:
    raise NotImplementedError(
        "manifest_lag/shell_age telemetry assertions land with the occ suite"
    )


__all__ = [
    "assert_manifest_depth_within",
    "assert_manifest_layers_referenced_on_disk",
    "assert_no_orphan_layers",
    "assert_no_torn_reads",
    "assert_accepts_visible_rejects_invisible",
    "assert_classification_pure",
    "assert_telemetry_present",
]
