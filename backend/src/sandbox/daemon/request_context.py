"""Per-request context shared by every daemon handler module.

Single source of truth for:

* the in-workspace classifier predicate (:func:`classify_path`),
* the host-side request-argument validation contract
  (:func:`require_arg`, :func:`require_layer_stack_root`,
  :func:`required_single_path`),
* no-follow filesystem helpers used by out-of-workspace verbs
  (:func:`read_bytes_no_follow`, :func:`write_text_no_follow`),
* result-payload projection used by ``write``/``edit`` to turn a
  :class:`ChangesetResult` into the host-visible response
  (:func:`project_changeset`, :func:`project_conflict`).

The OCC backend tuple is owned by :mod:`sandbox.daemon.occ_backend`; handlers
call :func:`sandbox.daemon.occ_backend.build_occ_backend` directly.

``shell`` does NOT use this module — the dispatcher routes it directly to
:mod:`sandbox.ephemeral_workspace.pipeline`, which owns its own argv/env
validation and timing helpers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal, NamedTuple

from sandbox._shared.tool_primitives.file_ops import (
    read_bytes_no_follow as _read_bytes_no_follow,
)
from sandbox._shared.tool_primitives.file_ops import (
    write_text_no_follow as _write_text_no_follow,
)
from sandbox.occ.changeset import ChangesetResult
from sandbox.occ.gitignore import SnapshotGitignoreOracle
from sandbox.daemon.result_projection import (
    committed_paths,
    conflict_and_status,
    conflict_to_dict,
    gitignore_cache_timings,
)
from sandbox._shared.clock import monotonic_now

# -- classifier predicate ---------------------------------------------------


class ClassifiedPath(NamedTuple):
    classification: Literal["in_workspace", "out_of_workspace"]
    abs_path: str
    """Absolute filesystem path post-symlink-resolution."""
    layer_path: str
    """Workspace-relative layer path. Empty string for out-of-workspace."""


def classify_path(raw_path: str, workspace_root: str) -> ClassifiedPath:
    """Classify ``raw_path`` as in-workspace or out-of-workspace.

    Single source of truth for the §1 classifier predicate. Symlinks resolve
    before classification; ``..`` segments that escape a workspace-anchored
    input are a hard ``ValueError`` (not a silent direct-FS fallthrough).
    """
    raw = str(raw_path or "").strip()
    if not raw:
        raise ValueError("path is required")

    workspace_literal = workspace_root.rstrip("/") or "/"
    workspace_real = os.path.realpath(workspace_literal)

    if not raw.startswith("/"):
        candidate = os.path.join(workspace_real, raw)
        anchored_to_workspace = True
    else:
        candidate = raw
        anchored_to_workspace = (
            raw in (workspace_literal, workspace_real)
            or raw.startswith(workspace_literal + "/")
            or raw.startswith(workspace_real + "/")
        )

    normalized = os.path.normpath(candidate)

    if anchored_to_workspace:
        inside_literal = (
            normalized == workspace_literal
            or normalized.startswith(workspace_literal + "/")
        )
        inside_real = (
            normalized == workspace_real
            or normalized.startswith(workspace_real + "/")
        )
        if not (inside_literal or inside_real):
            raise ValueError(f"path escapes workspace via '..': {raw}")

    resolved = os.path.realpath(normalized)

    if resolved == workspace_real or resolved.startswith(workspace_real + "/"):
        rel = os.path.relpath(resolved, workspace_real)
        if rel == ".":
            rel = ""
        return ClassifiedPath("in_workspace", resolved, rel)

    return ClassifiedPath("out_of_workspace", resolved, "")


# -- argument validation ----------------------------------------------------


def require_arg(args: Mapping[str, object], key: str) -> str:
    """Return a stripped non-empty string ``args[key]`` or raise."""
    value = str(args.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def require_layer_stack_root(args: Mapping[str, object]) -> str:
    return require_arg(args, "layer_stack_root")


def required_single_path(args: Mapping[str, object]) -> str:
    """Enforce single-path contract: ``args['path']`` must be one string."""
    raw = args.get("path")
    if not isinstance(raw, str):
        raise ValueError(
            "single-path contract: api.write_file/edit_file/read_file accept "
            "exactly one string path per request"
        )
    path = raw.strip()
    if not path:
        raise ValueError("path is required")
    return path


# -- no-follow host filesystem helpers --------------------------------------


def read_bytes_no_follow(abs_path: str) -> bytes:
    return _read_bytes_no_follow(abs_path)


def write_text_no_follow(
    abs_path: str,
    content: str,
    *,
    create_only: bool = False,
) -> None:
    _write_text_no_follow(abs_path, content, create_only=create_only)


# -- result projection ------------------------------------------------------


def project_changeset(
    result: ChangesetResult,
    *,
    fallback_path: str,
    verb: str,
    total_start: float,
    gitignore: SnapshotGitignoreOracle,
    timings_extra: dict[str, float],
) -> dict[str, object]:
    conflict, status = conflict_and_status(result.files)
    return {
        "success": result.success,
        "changed_paths": list(committed_paths(result.files, fallback_path=fallback_path)),
        "status": status,
        "conflict": conflict_to_dict(conflict),
        "conflict_reason": conflict.message if conflict is not None else None,
        "timings": {
            **result.timings,
            **gitignore_cache_timings(gitignore),
            **timings_extra,
            f"api.{verb}.total_s": monotonic_now() - total_start,
        },
    }


def project_conflict(
    *,
    verb: str,
    status: str,
    reason: str,
    path: str,
    message: str,
    total_start: float,
    timings_extra: dict[str, float] | None = None,
    changed_paths: list[str] | None = None,
    conflict_reason: str | None = None,
    **extras: object,
) -> dict[str, object]:
    """Project a single-path conflict into the guarded-result shape.

    ``status`` is the outer wire status (e.g. ``rejected``); ``reason`` is
    the inner ``conflict.reason`` (e.g. ``create_only_existing``). They
    coincide for the edit anchor-miss case. ``conflict_reason`` defaults
    to ``reason`` but the in-workspace edit path passes the raw exception
    text instead. ``extras`` carries verb-specific fields like
    ``applied_edits``.
    """
    payload: dict[str, object] = {
        "success": False,
        "changed_paths": list(changed_paths or []),
        "status": status,
        "conflict": {
            "reason": reason,
            "conflict_file": path,
            "message": message,
        },
        "conflict_reason": conflict_reason if conflict_reason is not None else reason,
        "timings": {
            **(timings_extra or {}),
            f"api.{verb}.total_s": monotonic_now() - total_start,
        },
    }
    payload.update(extras)
    return payload


__all__ = [
    "ClassifiedPath",
    "classify_path",
    "project_changeset",
    "project_conflict",
    "read_bytes_no_follow",
    "require_arg",
    "require_layer_stack_root",
    "required_single_path",
    "write_text_no_follow",
]
