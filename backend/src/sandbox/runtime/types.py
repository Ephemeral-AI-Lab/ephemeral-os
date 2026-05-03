"""Runtime result types shared by in-sandbox pipelines and clients."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConflictInfo:
    """Structured guarded-operation conflict details."""

    reason: str
    conflict_file: str | None = None
    message: str = ""


@dataclass(frozen=True)
class ShellResult:
    """Runtime shell result after overlay capture and OCC projection."""

    result: str
    exit_code: int
    changed_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    overlay_run_timings: dict[str, float] = field(default_factory=dict)
    overlay_stage_timings: dict[str, float] = field(default_factory=dict)
    conflict: ConflictInfo | None = None


__all__ = ["ConflictInfo", "ShellResult"]
