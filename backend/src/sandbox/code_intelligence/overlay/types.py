"""Dataclasses and exceptions for the overlay shell sandbox.

See ``docs/architecture/overlay-sandbox-plan.md`` §4.1. These types form
the frozen interface between the sandbox-side ``overlay_run.py`` script
(which emits NDJSON) and the orchestrator-side ``overlay_auditor.py``
(which parses NDJSON, invokes OCC, and assembles the downstream
``SimpleNamespace`` response).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


UpperChangeKind = Literal["regular", "whiteout", "symlink", "opaque_dir"]


class OverlayError(RuntimeError):
    """Base error for overlay auditing failures."""


class OverlayRunError(OverlayError):
    """Raised when the sandbox-side ``overlay_run.py`` transport fails."""


class OverlayPolicyReject(OverlayError):
    """Raised when the sandbox-side script refused the run via policy.

    ``reason`` is one of the overlay structural reject reasons, e.g.
    ``overlay_upper_full``.
    ``paths`` is the (optional) offending path list.
    """

    def __init__(
        self,
        reason: str,
        paths: tuple[str, ...] = (),
        *,
        run_timings: dict[str, float] | None = None,
    ) -> None:
        super().__init__(reason if not paths else f"{reason}: {','.join(paths)}")
        self.reason = reason
        self.paths = paths
        self.run_timings = dict(run_timings or {})


@dataclass(frozen=True)
class OverlayLease:
    """One per-op overlay lease.

    The overlay model (see plan §0, "Mount model") has no pool — each
    ``svc.cmd`` builds a fresh unshare namespace with fresh mounts and
    tears it all down on exit. The lease is just the per-op run
    directory on the container filesystem (outside the overlay so it
    survives ns exit) that holds ``diff.ndjson``.
    """

    run_dir: str


@dataclass(frozen=True)
class UpperChange:
    """One raw upperdir change emitted by ``overlay_run.py`` for OCC."""

    rel: str
    kind: UpperChangeKind
    base_bytes: bytes | None
    upper_bytes: bytes | None
    base_existed: bool


@dataclass(frozen=True)
class OverlayCommandResult:
    """Result of running the user command under overlay."""

    stdout: str
    exit_code: int


@dataclass(frozen=True)
class OverlayCapture:
    """Parsed ``diff.ndjson`` payload after one overlay op."""

    exit_code: int
    upper_bytes: int
    upper_files: int
    upper_changes: tuple[UpperChange, ...]
    run_timings: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConflictInfo:
    """Structured failure surface for the overlay → caller boundary.

    ``reason`` is a domain term ('argv_too_large', 'patch_failed', or an
    overlay reject reason). Underlying raw OCC ``OperationStatus`` values
    flow separately on the SimpleNamespace ``git_commit_status`` field
    so callers can still read the precise OCC verdict; this struct is
    the *normalized* reason the slice contracts on.

    ``upper_layer_path`` captures the live workspace path the overlay
    upperdir intended to write so the caller can inspect it on conflict.
    """

    reason: str
    conflict_file: str | None = None
    message: str = ""
    upper_layer_path: str | None = None


@dataclass
class OverlayRunOutcome:
    """In-process handoff between OverlayAuditor and its caller.

    The auditor produces this; the caller (today's
    ``AuditedCommandExecutor``) drives OCC merge policy on
    :attr:`upper_changes` and assembles the downstream
    ``SimpleNamespace`` response. Slice 5a's correctness fix is exactly
    this seam: overlay never invokes OCC.

    Not ``frozen``: ``overlay_stage_timings`` is set after lease cleanup
    in the auditor's ``finally`` block, mirroring today's mutable
    ``SimpleNamespace`` lifecycle. The struct is otherwise treated as
    immutable by callers.
    """

    exit_code: int
    stdout: str
    upper_changes: tuple[UpperChange, ...]
    overlay_rejected: bool
    conflict: ConflictInfo | None
    warnings: tuple[str, ...] = ()
    overlay_run_timings: dict[str, float] = field(default_factory=dict)
    overlay_stage_timings: dict[str, float] = field(default_factory=dict)
    policy_reject: OverlayPolicyReject | None = None


__all__ = [
    "ConflictInfo",
    "OverlayCapture",
    "OverlayCommandResult",
    "OverlayError",
    "OverlayLease",
    "OverlayPolicyReject",
    "OverlayRunError",
    "OverlayRunOutcome",
    "UpperChange",
    "UpperChangeKind",
]
