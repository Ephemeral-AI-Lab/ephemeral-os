"""Legacy result type kept for the OCC simplification migration window.

The new search/replace gate's result shape lives in ``changeset/types.py``.
A small legacy surface remains to support shell-pipeline test injection that
constructs the old shape directly: see ``runtime/pipelines.shell_pipeline``'s
``occ_engine`` / ``occ_apply_changeset`` injection branches. Step 5 of the
simplification will revisit whether these injections still need this type.
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
