"""Test helpers for constructing OCC write changes through public builders."""

from __future__ import annotations

from sandbox.occ.changeset.builders import (
    build_api_write_change,
    build_overlay_write_change,
)
from sandbox.occ.changeset.types import ChangeSource, WriteChange


def write_change(
    *,
    path: str,
    final_content: bytes | str,
    source: ChangeSource = "api_write",
    base_hash: str | None = None,
) -> WriteChange:
    if source == "overlay_capture":
        return build_overlay_write_change(
            path=path,
            final_content=final_content,
        ).with_base_hash(base_hash)
    return build_api_write_change(
        path=path,
        final_content=final_content,
        base_hash=base_hash,
    )


__all__ = ["write_change"]
