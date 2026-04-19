"""Dataclasses and exceptions for the overlay CodeAct sandbox.

See ``docs/architecture/overlay-sandbox-plan.md`` §4.1. These types form
the frozen interface between the sandbox-side ``overlay_run.py`` script
(which emits NDJSON) and the orchestrator-side ``overlay_auditor.py``
(which parses NDJSON, invokes OCC, and assembles the downstream
``SimpleNamespace`` response).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


OverlayChangeKind = Literal["create", "modify", "delete"]


class OverlayError(RuntimeError):
    """Base error for overlay auditing failures."""


class OverlayRunError(OverlayError):
    """Raised when the sandbox-side ``overlay_run.py`` transport fails."""


class OverlayPolicyReject(OverlayError):
    """Raised when the sandbox-side script refused the run via policy.

    ``reason`` is one of the plan-defined reasons, e.g.
    ``overlay_rejected_dotgit_writes``,
    ``overlay_refused_gitignored_whiteout``,
    ``overlay_unsupported_symlink``,
    ``overlay_unsupported_opaque_dir``,
    ``overlay_non_utf8_tracked``,
    ``overlay_upper_full``.
    ``paths`` is the (optional) offending path list.
    """

    def __init__(self, reason: str, paths: tuple[str, ...] = ()) -> None:
        super().__init__(reason if not paths else f"{reason}: {','.join(paths)}")
        self.reason = reason
        self.paths = paths


@dataclass(frozen=True)
class OverlayLease:
    """One per-op overlay lease.

    The overlay model (see plan §0, "Mount model") has no pool — each
    ``svc.cmd`` builds a fresh unshare namespace with fresh mounts and
    tears it all down on exit. The lease is just the bookkeeping handle
    (semaphore slot, unique run-id, per-op run directory on the
    container filesystem for NDJSON transport).
    """

    run_id: str
    run_dir: str  # container-fs directory (not under overlay) for diff.ndjson


@dataclass(frozen=True)
class OverlayChange:
    """One tracked-route change emitted by ``overlay_run.py``.

    Gitignored changes are direct-merged inside the namespace and do not
    appear here — they are summarized in :class:`OverlayDiff.gitignored_paths`.
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

    snap: str
    exit_code: int
    upper_bytes: int
    upper_files: int
    tracked_changes: tuple[OverlayChange, ...]
    gitignored_paths: tuple[str, ...]
    gitignored_truncated: bool
    direct_merged_bytes: int
    whiteouts_tracked: int
    whiteouts_gitignored_refused: int
    dotgit_rejects: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverlayAuditResult:
    """Full orchestrator-side result, before the ``SimpleNamespace`` adapter.

    Downstream code (``codeact_tool`` etc.) reads through the
    ``SimpleNamespace`` the auditor returns, so this record is internal.
    It carries the additive fields called out in plan §4.5 that the
    auditor surfaces on the response.
    """

    command: OverlayCommandResult
    tracked_committed: tuple[str, ...]
    gitignored_merged: tuple[str, ...]
    gitignored_merged_count: int
    mixed_tracked_gitignored: bool
    mixed_partial_apply: bool
    git_commit_status: str | None
    git_conflict_file: str | None
    git_conflict_reason: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "OverlayAuditResult",
    "OverlayChange",
    "OverlayChangeKind",
    "OverlayCommandResult",
    "OverlayDiff",
    "OverlayError",
    "OverlayLease",
    "OverlayPolicyReject",
    "OverlayRunError",
]
