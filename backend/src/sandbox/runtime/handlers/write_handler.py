"""``api.write_file`` dispatch entry."""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

from sandbox.layer_stack.workspace import require_workspace_binding
from sandbox.occ.changeset.builders import build_api_write_change
from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.runtime.handlers._common import (
    _layer_stack_root,
    _project_changeset,
    _required_single_path,
    _services,
    classify_path,
)


async def write_file(args: dict[str, object]) -> dict[str, object]:
    """Single-path write_file dispatch with in/out-of-workspace classification."""
    total_start = time.perf_counter()
    layer_stack_root = _layer_stack_root(args)
    binding = require_workspace_binding(layer_stack_root)
    raw_path = _required_single_path(args)
    classified = classify_path(raw_path, binding.workspace_root)

    content = str(args.get("content") or "")
    overwrite = bool(args.get("overwrite", True))

    if classified.classification == "out_of_workspace":
        return _write_out_of_workspace(
            classified.abs_path,
            content,
            overwrite=overwrite,
            total_start=total_start,
        )

    return await _write_in_workspace(
        layer_stack_root=layer_stack_root,
        layer_path=classified.layer_path,
        content=content,
        overwrite=overwrite,
        actor_id=str(args.get("actor_id") or ""),
        description=str(args.get("description") or f"write {raw_path}"),
        total_start=total_start,
    )


async def _write_in_workspace(
    *,
    layer_stack_root: str,
    layer_path: str,
    content: str,
    overwrite: bool,
    actor_id: str,
    description: str,
    total_start: float,
) -> dict[str, object]:
    services = _services(layer_stack_root)
    request_id = uuid4().hex
    lease_start = time.perf_counter()
    lease = services.manager.acquire_snapshot_lease(request_id)
    lease_acquired_s = time.perf_counter() - lease_start
    try:
        if not overwrite:
            # create-only: reject if the path already exists in the leased
            # validation snapshot. OCC's gated merge does not enforce
            # WriteChange.create_only on its own — host-side existence check
            # against snapshot N is the §6 source of truth for this rule.
            _, exists_in_n = services.layer_stack.read_bytes(
                layer_path, lease.manifest
            )
            if exists_in_n:
                return {
                    "success": False,
                    "changed_paths": [],
                    "status": "rejected",
                    "conflict": {
                        "reason": "create_only_existing",
                        "conflict_file": layer_path,
                        "message": (
                            "create-only write rejected: path exists in "
                            f"validation snapshot at {layer_path}"
                        ),
                    },
                    "conflict_reason": "create_only_existing",
                    "timings": {
                        "api.write.lease_acquire_s": lease_acquired_s,
                        "api.write.total_s": time.perf_counter() - total_start,
                    },
                }

        change = build_api_write_change(
            path=layer_path,
            final_content=content,
            create_only=not overwrite,
        )
        apply_start = time.perf_counter()
        result = await services.occ_client.apply_changeset(
            [change],
            snapshot=lease.manifest,
            options=CommitOptions(
                atomic=False,
                caller_id=actor_id,
                description=description,
            ),
            workspace_ref=layer_stack_root,
        )
        apply_elapsed = time.perf_counter() - apply_start
    finally:
        services.manager.release_lease(lease.lease_id)

    if isinstance(result, PreparedChangeset):
        raise TypeError("write_file OCC client returned an uncommitted changeset")
    return _project_changeset(
        result,
        fallback_path=layer_path,
        verb="write",
        total_start=total_start,
        gitignore=services.gitignore,
        timings_extra={
            "api.write.lease_acquire_s": lease_acquired_s,
            "api.write.occ_apply_s": apply_elapsed,
        },
    )


def _write_out_of_workspace(
    abs_path: str,
    content: str,
    *,
    overwrite: bool,
    total_start: float,
) -> dict[str, object]:
    target = Path(abs_path)
    if not overwrite and target.exists():
        return {
            "success": False,
            "changed_paths": [],
            "status": "rejected",
            "conflict": {
                "reason": "create_only_existing",
                "conflict_file": abs_path,
                "message": (
                    "create-only write rejected: path exists at "
                    f"{abs_path}"
                ),
            },
            "conflict_reason": "create_only_existing",
            "timings": {
                "api.write.total_s": time.perf_counter() - total_start,
            },
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    write_start = time.perf_counter()
    target.write_text(content, encoding="utf-8")
    write_elapsed = time.perf_counter() - write_start
    return {
        "success": True,
        "changed_paths": [abs_path],
        "status": "ok",
        "conflict": None,
        "conflict_reason": None,
        "timings": {
            "api.write.host_fs_write_s": write_elapsed,
            "api.write.total_s": time.perf_counter() - total_start,
        },
    }


__all__ = ["write_file"]
