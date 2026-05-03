"""Shared data types for the code intelligence service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class EditRequest:
    """A request to edit a file through the service edit helper."""

    file_path: str
    old_text: str
    new_text: str
    agent_id: str = ""
    description: str = ""


@dataclass(frozen=True)
class EditResult:
    """Result of an edit operation."""

    success: bool
    file_path: str
    message: str = ""
    conflict: bool = False
    conflict_reason: str = ""
    snapshot_id: str = ""
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationChange:
    """One file's slot in a service-level semantic operation.

    ``base_content`` is the content the semantic tool inspected at plan time;
    ``base_hash`` is its :func:`sandbox.occ.content.hashing.content_hash`.
    ``final_content`` is the
    tool's proposed post-transform content, or ``None`` to delete the file.
    ``base_existed`` is ``False`` when the plan expects to create a new file.
    ``strict_base`` requires ``current_hash == base_hash`` in the modify branch
    and skips the non-overlapping merge fallback; set for whole-file rewrites
    (e.g. ``move --overwrite``) where tolerating concurrent edits would
    silently drop them.
    """

    file_path: str
    base_content: str
    base_hash: str
    final_content: str | None
    base_existed: bool = True
    strict_base: bool = False


@dataclass(frozen=True)
class WriteSpec:
    """One file slot inside a :meth:`svc.write_file` batch.

    ``overwrite`` controls the create/modify contract: ``True`` (default)
    overwrites an existing file via a strict-base rewrite; ``False`` requires
    the path to be absent at commit time and aborts with ``aborted_version``
    if something already exists there.
    """

    file_path: str
    content: str
    overwrite: bool = True


@dataclass(frozen=True)
class EditSpec:
    """One file slot inside a :meth:`svc.edit_file` batch.

    Carries a list of :class:`SearchReplaceEdit` values applied in order
    against the file's plan-time base. The service assembles one
    :class:`OperationChange` per spec and submits the whole list as a single
    OCC batch.
    """

    file_path: str
    edits: Sequence[Any]  # Sequence[SearchReplaceEdit]


OperationStatus = Literal[
    "committed",
    "aborted_version",
    "aborted_overlap",
    "aborted_lock",
    "failed",
]


@dataclass(frozen=True)
class OperationResult:
    """Outcome of one service-level semantic operation against explicit bases."""

    success: bool
    status: OperationStatus
    files: tuple["EditResult", ...] = ()
    conflict_file: str | None = None
    conflict_reason: str = ""
    timings: dict[str, float] = field(default_factory=dict)
