"""Shared types for the sandbox-side overlay runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

UpperChangeKind = Literal["regular", "whiteout", "symlink", "opaque_dir"]


@dataclass(frozen=True)
class UpperEntry:
    """One raw upperdir entry."""

    rel: str
    st: os.stat_result
    xattrs: dict[bytes, bytes]
    upper_path: str


@dataclass(frozen=True)
class UpperChange:
    """One captured upperdir change emitted to OCC."""

    rel: str
    kind: UpperChangeKind
    base_bytes: bytes | None
    upper_bytes: bytes | None
    base_existed: bool


@dataclass(frozen=True)
class PolicyRejectOutcome:
    reason: str
    paths: tuple[str, ...]


__all__ = [
    "PolicyRejectOutcome",
    "UpperChange",
    "UpperChangeKind",
    "UpperEntry",
]
