"""Unit tests for the namespace-only overlay layout."""

from __future__ import annotations

import pytest

from sandbox.overlay.layout import LayerPathsLayout


def _valid_layer_paths_layout(
    *,
    layer_paths: tuple[str, ...] = ("/storage/layers/L1", "/storage/layers/L2"),
    layer_storage_root: str = "/storage/layers",
    scratch_root: str = "/scratch",
) -> LayerPathsLayout:
    return LayerPathsLayout(
        workspace_root="/workspace",
        layer_paths=layer_paths,
        layer_storage_root=layer_storage_root,
        writes="/scratch/upper",
        kernel_scratch="/scratch/work",
        scratch_root=scratch_root,
    )


def test_layer_paths_layout_valid() -> None:
    spec = _valid_layer_paths_layout()
    assert spec.layer_paths == ("/storage/layers/L1", "/storage/layers/L2")


def test_layer_paths_layout_rejects_empty_layer_paths() -> None:
    with pytest.raises(ValueError, match="layer_paths must not be empty"):
        LayerPathsLayout(
            workspace_root="/workspace",
            layer_paths=(),
            layer_storage_root="/storage/layers",
            writes="/scratch/upper",
            kernel_scratch="/scratch/work",
            scratch_root="/scratch",
        )


def test_layer_paths_layout_rejects_path_outside_layer_storage_root() -> None:
    with pytest.raises(ValueError, match="must be under layer_storage_root"):
        LayerPathsLayout(
            workspace_root="/workspace",
            layer_paths=("/etc/passwd",),
            layer_storage_root="/storage/layers",
            writes="/scratch/upper",
            kernel_scratch="/scratch/work",
            scratch_root="/scratch",
        )


def test_layer_paths_layout_accepts_deep_layer_paths() -> None:
    deep_layers = tuple(f"/storage/layers/L{i}" for i in range(250))
    spec = LayerPathsLayout(
        workspace_root="/workspace",
        layer_paths=deep_layers,
        layer_storage_root="/storage/layers",
        writes="/scratch/upper",
        kernel_scratch="/scratch/work",
        scratch_root="/scratch",
    )
    assert len(spec.layer_paths) == len(deep_layers)


def test_layer_paths_layout_rejects_writes_outside_scratch_root() -> None:
    with pytest.raises(ValueError, match="writes must be strictly under scratch_root"):
        LayerPathsLayout(
            workspace_root="/workspace",
            layer_paths=("/storage/layers/L1",),
            layer_storage_root="/storage/layers",
            writes="/tmp/outside",
            kernel_scratch="/scratch/work",
            scratch_root="/scratch",
        )


def test_layer_paths_layout_has_no_materialized_base_repo() -> None:
    spec = _valid_layer_paths_layout()
    assert not hasattr(spec, "base_repo")
