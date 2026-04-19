"""Structural path guards for destructive Daytona file operations."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit._daytona_utils import _get_repo_root
from tools.daytona_toolkit.hooks._common import resolved_arg


def _normalized_path(path: str) -> str:
    if path == "/":
        return path
    return path.rstrip("/") or path


def _repo_guard_error(
    context: ToolExecutionContext,
    file_path: str,
    *,
    tool_name: str,
) -> str | None:
    """Reject operations outside a concrete repo root."""
    repo_root = _normalized_path(str(_get_repo_root(context) or ""))
    if not repo_root or repo_root == "/":
        return (
            f"{tool_name}: operation requires a non-root "
            "repo_root/daytona_cwd in context."
        )

    path = _normalized_path(file_path)
    if path == repo_root:
        return f"{tool_name}: refusing to operate on repo root: {repo_root}"
    if not path.startswith(repo_root + "/"):
        return (
            f"{tool_name}: refusing operation outside repo root "
            f"{repo_root}: {file_path}"
        )
    return None


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    if tool_name == "daytona_delete_file":
        path = resolved_arg(args, "path", context)
        candidates = [path] if path is not None else []
    elif tool_name == "daytona_move_file":
        candidates = [
            resolved_arg(args, "src_path", context),
            resolved_arg(args, "target_path", context),
        ]
    else:
        candidates = []

    for candidate in candidates:
        if candidate is None:
            continue
        error = _repo_guard_error(context, candidate, tool_name=tool_name)
        if error is not None:
            return PreHookOutcome(has_error=True, error_message=error)

    if tool_name == "daytona_move_file":
        src = resolved_arg(args, "src_path", context)
        dst = resolved_arg(args, "target_path", context)
        if src is not None and dst is not None:
            src = _normalized_path(src)
            dst = _normalized_path(dst)
            if dst == src:
                return PreHookOutcome(
                    has_error=True,
                    error_message="daytona_move_file: src_path and target_path are identical",
                )
            if dst.startswith(src + "/"):
                return PreHookOutcome(
                    has_error=True,
                    error_message=(
                        "daytona_move_file: refusing to move a path to a destination "
                        f"inside source: {dst}"
                    ),
                )
            if src.startswith(dst + "/"):
                return PreHookOutcome(
                    has_error=True,
                    error_message=(
                        "daytona_move_file: refusing to replace a destination that "
                        f"contains source: {dst}"
                    ),
                )

    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    for tool_name in ("daytona_delete_file", "daytona_move_file"):
        reg.register(
            tool_name,
            "pre",
            5,
            hook,
            name=f"{tool_name}:repo_operation_guard",
        )
