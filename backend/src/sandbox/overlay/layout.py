"""Shared layout for namespace overlay execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class LayerPathsLayout:
    """Layout used by namespace overlay callers."""

    workspace_root: str
    layer_paths: tuple[str, ...]
    layer_storage_root: str
    writes: str
    kernel_scratch: str
    scratch_root: str

    def __post_init__(self) -> None:
        if not str(self.workspace_root).startswith("/"):
            raise ValueError("workspace_root must be absolute")
        if not str(self.scratch_root).strip():
            raise ValueError("scratch_root must not be empty")
        if not self.layer_paths:
            raise ValueError("layer_paths must not be empty")
        if not str(self.layer_storage_root).strip():
            raise ValueError("layer_storage_root must not be empty")
        layer_storage_root = Path(self.layer_storage_root).resolve(strict=False)
        for path_str in self.layer_paths:
            path = Path(path_str).resolve(strict=False)
            if path == layer_storage_root or not path.is_relative_to(layer_storage_root):
                raise ValueError(
                    f"layer path {path_str!r} must be under "
                    f"layer_storage_root {self.layer_storage_root!r}"
                )
        scratch_root = Path(self.scratch_root).resolve(strict=False)
        for field_name in ("writes", "kernel_scratch"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
            path = Path(str(getattr(self, field_name))).resolve(strict=False)
            if path == scratch_root or not path.is_relative_to(scratch_root):
                raise ValueError(
                    f"{field_name} must be strictly under scratch_root: {path}"
                )


__all__ = ["LayerPathsLayout"]
