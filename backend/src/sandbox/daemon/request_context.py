"""Shared daemon request validation and result projection helpers."""

from __future__ import annotations

from collections.abc import Mapping

from sandbox.occ.changeset import ChangesetResult
from sandbox.occ.gitignore import SnapshotGitignoreOracle
from sandbox.daemon.result_projection import (
    conflict_and_status,
    conflict_to_dict,
    gitignore_cache_timings,
    published_paths,
)
from sandbox._shared.clock import monotonic_now


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


def project_changeset(
    result: ChangesetResult,
    *,
    verb: str,
    total_start: float,
    gitignore: SnapshotGitignoreOracle,
    timings_extra: dict[str, float],
) -> dict[str, object]:
    conflict, status = conflict_and_status(result.files)
    changed_paths = list(published_paths(result.files))
    mutation_source = _mutation_source_for_verb(verb)
    return {
        "success": result.success,
        "changed_paths": changed_paths,
        "changed_path_kinds": _changed_path_kinds(changed_paths),
        "mutation_source": mutation_source,
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
        "changed_path_kinds": _changed_path_kinds(changed_paths or []),
        "mutation_source": _mutation_source_for_verb(verb),
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


def _mutation_source_for_verb(verb: str) -> str:
    return {
        "write": "api_write",
        "edit": "api_edit",
    }.get(verb, "")


def _changed_path_kinds(paths: list[str]) -> dict[str, str]:
    return {path: "write" for path in paths if str(path or "").strip()}


__all__ = [
    "project_changeset",
    "project_conflict",
    "require_arg",
    "require_layer_stack_root",
    "required_single_path",
]
