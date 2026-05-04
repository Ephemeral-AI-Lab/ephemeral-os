"""Legacy live-root overlay capture dataclasses and exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

UpperChangeKind = Literal["regular", "whiteout", "symlink", "opaque_dir"]


class OverlayError(RuntimeError):
    """Base error for overlay auditing failures."""


class OverlayRunError(OverlayError):
    """Raised when the sandbox-side overlay runtime transport fails."""


@dataclass(frozen=True)
class OverlayLease:
    """One per-op overlay lease.

    The live-root overlay path has no pool. Each command builds a fresh
    namespace with fresh mounts and tears it down on exit. The lease is the
    per-op run directory on the container filesystem that holds runtime
    artifacts such as ``diff.ndjson``.
    """

    run_dir: str


@dataclass(frozen=True)
class UpperChange:
    """One raw upperdir change emitted by the legacy overlay runtime for OCC."""

    rel: str
    kind: UpperChangeKind
    base_bytes: bytes | None
    upper_bytes: bytes | None
    base_existed: bool


@dataclass(frozen=True)
class OverlayCapture:
    """Parsed ``diff.ndjson`` payload after one legacy overlay op."""

    exit_code: int
    upper_bytes: int
    upper_files: int
    upper_changes: tuple[UpperChange, ...]
    run_timings: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass
class OverlayRunOutcome:
    """Capture-run handoff between the legacy overlay path and its caller."""

    exit_code: int
    stdout: str
    upper_changes: tuple[UpperChange, ...]
    warnings: tuple[str, ...] = ()
    overlay_run_timings: dict[str, float] = field(default_factory=dict)
    overlay_stage_timings: dict[str, float] = field(default_factory=dict)


__all__ = [
    "OverlayCapture",
    "OverlayError",
    "OverlayLease",
    "OverlayRunError",
    "OverlayRunOutcome",
    "UpperChange",
    "UpperChangeKind",
]
