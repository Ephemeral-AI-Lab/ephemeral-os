"""Shared capacity-action contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapacityActionResult:
    """Summary returned by capacity action drivers."""

    name: str
    summary: str
    artifact_path: str | None
    expected_errors: tuple[str, ...]
    counters: Mapping[str, int | float | str]


__all__ = ["CapacityActionResult"]
