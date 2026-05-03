"""Types for OCC changeset routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class UpperChangeLike(Protocol):
    rel: str
    kind: str
    base_bytes: bytes | None
    upper_bytes: bytes | None
    base_existed: bool


@dataclass(frozen=True)
class ChangesetResult:
    success: bool
    status: str
    ledgered: tuple[str, ...] = ()
    direct_merged: tuple[str, ...] = ()
    conflict_reason: str | None = None
    conflict_file: str | None = None
    timings: dict[str, float] = field(default_factory=dict)


__all__ = ["ChangesetResult", "UpperChangeLike"]

