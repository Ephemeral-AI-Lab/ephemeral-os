"""Shared scaffolding for the per-verb handler modules.

Owns the single source of truth for:

* the in-workspace classifier predicate (``classify_path``),
* the host-side single-path / ``layer_stack_root`` validation contract,
* the (LayerStackClient, OCCClient, GitignoreOracle, LayerStackManager)
  service tuple cache,
* the result projection helpers used by ``write_handler`` and
  ``edit_handler`` to turn a :class:`ChangesetResult` into the
  host-visible payload.

``shell_handler`` does NOT use this module — it routes through
``command_exec_server`` whose worker scaffolding still owns its own
service cache and timing helpers.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, NamedTuple

from sandbox.api.tool.result_projection import (
    committed_paths,
    conflict_and_status,
)
from sandbox.layer_stack.stack_manager import LayerStackManager
from sandbox.occ.changeset.types import ChangesetResult
from sandbox.occ.client import OCCClient
from sandbox.occ.content.gitignore_oracle import SnapshotGitignoreOracle
from sandbox.occ.service import OccService
from sandbox.runtime.clients.layer_stack import LayerStackClient
from sandbox.runtime.clients.occ import RuntimeWorkspaceBindingReader
from sandbox.runtime.layer_stack_server import get_layer_stack_manager


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
            raw == workspace_literal
            or raw.startswith(workspace_literal + "/")
            or raw == workspace_real
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


def _layer_stack_root(args: Mapping[str, object]) -> str:
    layer_stack_root = str(args.get("layer_stack_root") or "").strip()
    if not layer_stack_root:
        raise ValueError("layer_stack_root is required")
    return layer_stack_root


def _required_single_path(args: Mapping[str, object]) -> str:
    """Enforce single-path contract: ``args['path']`` must be one string."""
    raw = args.get("path")
    if isinstance(raw, list):
        raise ValueError(
            "single-path contract: api.write_file/edit_file/read_file accept "
            "exactly one path per request"
        )
    path = str(raw or "").strip()
    if not path:
        raise ValueError("path is required")
    return path


# -- service cache ----------------------------------------------------------


class _Services(NamedTuple):
    layer_stack: LayerStackClient
    occ_client: OCCClient
    gitignore: SnapshotGitignoreOracle
    manager: LayerStackManager


_SERVICE_CACHE: dict[str, _Services] = {}


def _services_cache_clear() -> None:
    """Drop write/edit/read service cache. Test helper."""
    _SERVICE_CACHE.clear()


def drop_services_cache(layer_stack_root: str) -> None:
    """Drop cached services for one layer-stack root."""
    root = str(layer_stack_root or "").strip()
    if not root:
        return
    _SERVICE_CACHE.pop(root, None)
    _SERVICE_CACHE.pop(str(Path(root).resolve(strict=False)), None)


def _services(layer_stack_root: str) -> _Services:
    cached = _SERVICE_CACHE.get(layer_stack_root)
    if cached is not None:
        return cached
    manager = get_layer_stack_manager(layer_stack_root)
    layer_stack = LayerStackClient(manager)
    gitignore = SnapshotGitignoreOracle(layer_stack)
    occ_service = OccService(gitignore=gitignore, layer_stack=layer_stack)
    occ_client = OCCClient(
        occ_service,
        binding_reader=RuntimeWorkspaceBindingReader(),
        workspace_ref=layer_stack_root,
    )
    services = _Services(
        layer_stack=layer_stack,
        occ_client=occ_client,
        gitignore=gitignore,
        manager=manager,
    )
    _SERVICE_CACHE[layer_stack_root] = services
    return services


# -- result projection ------------------------------------------------------


def _project_changeset(
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
        "conflict": _conflict_to_dict(conflict),
        "conflict_reason": conflict.message if conflict is not None else None,
        "timings": {
            **result.timings,
            **_gitignore_timings(gitignore),
            **timings_extra,
            f"api.{verb}.total_s": time.perf_counter() - total_start,
        },
    }


def _gitignore_timings(
    gitignore: SnapshotGitignoreOracle,
) -> dict[str, float]:
    return {
        "gitignore.cache_hits_total": float(gitignore.cache_hits),
        "gitignore.cache_misses_total": float(gitignore.cache_misses),
        "gitignore.materialize_snapshot_s": float(gitignore.last_materialize_s),
        "gitignore.git_init_s": float(gitignore.last_git_init_s),
    }


def _conflict_to_dict(conflict: object | None) -> dict[str, object] | None:
    if conflict is None:
        return None
    return {
        "reason": getattr(conflict, "reason", ""),
        "conflict_file": getattr(conflict, "conflict_file", None),
        "message": getattr(conflict, "message", ""),
    }


__all__ = [
    "ClassifiedPath",
    "_Services",
    "_SERVICE_CACHE",
    "_conflict_to_dict",
    "_gitignore_timings",
    "_layer_stack_root",
    "_project_changeset",
    "_required_single_path",
    "_services",
    "_services_cache_clear",
    "classify_path",
    "drop_services_cache",
]
