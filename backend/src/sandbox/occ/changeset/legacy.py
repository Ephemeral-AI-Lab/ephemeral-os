"""Legacy result type kept for the OCC simplification migration window.

Step 1 of the OCC simplification (see
``.omc/plans/occ-changeset-gate-simplification.md``) rewrites
``changeset/types.py`` with the new search/replace gate result shape. Until
the legacy gate is removed in Step 4, the existing
``apply_changeset``/``LocalOCCEngine``/``WriteCoordinator`` chain and its
tests still need the old result dataclass. They import it from here so the
new types can land without breaking ``make test``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LegacyChangesetResult:
    success: bool
    status: str
    ledgered: tuple[str, ...] = ()
    direct_merged: tuple[str, ...] = ()
    conflict_reason: str | None = None
    conflict_file: str | None = None
    timings: dict[str, float] = field(default_factory=dict)


__all__ = ["LegacyChangesetResult"]
