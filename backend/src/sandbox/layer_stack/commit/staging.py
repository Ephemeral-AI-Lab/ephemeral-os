"""Commit staging area data type for layer-stack mutations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommitStagingArea:
    staging_id: str
    path: Path


__all__ = ["CommitStagingArea"]
