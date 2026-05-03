"""Dataclasses and exceptions for the overlay shell sandbox.

See ``docs/architecture/overlay-sandbox-plan.md`` §4.1. These types form
the frozen interface between the sandbox-side ``overlay_run.py`` script
(which emits NDJSON) and the orchestrator-side ``overlay_auditor.py``
(which parses NDJSON, invokes OCC, and assembles the downstream
``SimpleNamespace`` response).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sandbox.code_intelligence.core.types import OperationChange


OverlayChangeKind = Literal["create", "modify", "delete"]


class OverlayError(RuntimeError):
    """Base error for overlay auditing failures."""


class OverlayRunError(OverlayError):
    """Raised when the sandbox-side ``overlay_run.py`` transport fails."""


class OverlayPolicyReject(OverlayError):
    """Raised when the sandbox-side script refused the run via policy.

    ``reason`` is one of the plan-defined reasons, e.g.
    ``overlay_rejected_dotgit_writes``,
    ``overlay_refused_gitignore_whiteout``,
    ``overlay_unsupported_symlink``,
    ``overlay_unsupported_opaque_dir``,
    ``overlay_non_utf8_gitinclude``,
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
class OverlayChange:
    """One gitinclude-route change emitted by ``overlay_run.py`` for OCC.

    Routing is keyed by ``git check-ignore`` against the live workspace,
    not by git index membership: brand-new files that are not matched by
    any ``.gitignore`` rule appear here too. Concurrent writers to the
    same path are resolved by strict-base OCC → first-writer-wins.

    Gitignore-route changes are direct-merged inside the namespace and
    do not appear here — they are summarized in
    :class:`OverlayDiff.gitignore_paths` (per-file last-writer-wins, not
    per-tree atomic).
    """

    path: str
    kind: OverlayChangeKind
    base_content: str
    base_existed: bool
    final_content: str | None


@dataclass(frozen=True)
class OverlayCommandResult:
    """Result of running the user command under overlay."""

    stdout: str
    exit_code: int


@dataclass(frozen=True)
class OverlayDiff:
    """Full payload parsed from ``diff.ndjson`` after one overlay op."""

    exit_code: int
    upper_bytes: int
    upper_files: int
    gitinclude_changes: tuple[OverlayChange, ...]
    gitignore_paths: tuple[str, ...]
    gitignore_truncated: bool
    direct_merged_bytes: int
    whiteouts_gitinclude: int
    whiteouts_gitignore_refused: int
    dotgit_rejects: int
    run_timings: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverlayAuditResult:
    """Full orchestrator-side result, before the ``SimpleNamespace`` adapter.

    Downstream code (``shell`` etc.) reads through the
    ``SimpleNamespace`` the auditor returns, so this record is internal.
    It carries the additive fields called out in plan §4.5 that the
    auditor surfaces on the response.
    """

    command: OverlayCommandResult
    gitinclude_committed: tuple[str, ...]
    gitignore_merged: tuple[str, ...]
    gitignore_merged_count: int
    mixed_gitinclude_gitignore: bool
    mixed_partial_apply: bool
    git_commit_status: str | None
    git_conflict_file: str | None
    git_conflict_reason: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


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
    ``AuditedCommandExecutor``) drives OCC commit on
    :attr:`dirty_changes` and assembles the downstream
    ``SimpleNamespace`` response. Slice 5a's correctness fix is exactly
    this seam: overlay never invokes OCC.

    Not ``frozen``: ``overlay_stage_timings`` is set after lease cleanup
    in the auditor's ``finally`` block, mirroring today's mutable
    ``SimpleNamespace`` lifecycle. The struct is otherwise treated as
    immutable by callers.
    """

    exit_code: int
    stdout: str
    dirty_changes: tuple["OperationChange", ...]
    overlay_rejected: bool
    conflict: ConflictInfo | None
    gitignore_paths: tuple[str, ...]
    gitinclude_live_paths: tuple[str, ...]
    mixed_gitinclude_gitignore: bool
    warnings: tuple[str, ...] = ()
    overlay_run_timings: dict[str, float] = field(default_factory=dict)
    overlay_stage_timings: dict[str, float] = field(default_factory=dict)
    policy_reject: OverlayPolicyReject | None = None


__all__ = [
    "ConflictInfo",
    "OverlayAuditResult",
    "OverlayChange",
    "OverlayChangeKind",
    "OverlayCommandResult",
    "OverlayDiff",
    "OverlayError",
    "OverlayLease",
    "OverlayPolicyReject",
    "OverlayRunError",
    "OverlayRunOutcome",
]
