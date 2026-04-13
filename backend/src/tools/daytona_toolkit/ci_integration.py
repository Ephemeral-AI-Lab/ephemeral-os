"""Daytona-specific CI integration helpers."""

from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Any

from team._path_utils import normalize_scope_paths
from tools.core.ci_runtime import sync_deleted_file, sync_write_to_ci
from tools.core.sandbox_runtime import (
    get_daytona_cwd,
    get_daytona_sandbox,
    require_declared_shell_outputs,
)
from tools.core.base import ToolExecutionContext

logger = logging.getLogger(__name__)

_SHELL_MUTATION_PATTERN = re.compile(
    r"(^|[;&|]\s*)("
    r"cat\s+>|tee\s|cp\s|mv\s|rm\s|touch\s|mkdir\s|install\s|ln\s|"
    r"git\s+(apply|checkout|restore|reset|clean|mv|rm)\b|"
    r"sed\s+-i\b|perl\s+-pi\b|patch\b|ed\b|ex\b|"
    r".*>>|.*[^<]>(?!&)[^>]"
    r")",
    flags=re.IGNORECASE,
)
_READ_ONLY_TEST_COMMAND_PATTERN = re.compile(
    r"^\s*(?:python(?:\d+(?:\.\d+)*)?\s+-m\s+)?(?:pytest|py\.test)\b",
    flags=re.IGNORECASE,
)


def shell_mutation_declaration_error(
    context: ToolExecutionContext,
    *,
    command: str,
    declared_output_paths: list[str] | None,
) -> str | None:
    """Return an error when a mutating shell command lacks declared outputs."""
    if not require_declared_shell_outputs(context):
        return None
    if not command_may_mutate_workspace(command):
        return None
    if normalize_scope_paths(declared_output_paths or []):
        return None
    return (
        "Mutating shell calls must declare `declared_output_paths` in team "
        "coordination mode. Prefer daytona_write_file/daytona_edit_file, or list every "
        "path the command may create, modify, move, or delete before running it."
    )


def command_may_mutate_workspace(command: str) -> bool:
    """Heuristic gate for when a shell command should trigger CI reconciliation."""
    stripped = (command or "").strip()
    if not stripped:
        return False
    # Treat test execution as read-only for coordination purposes even if the
    # tool runner writes ephemeral caches like .pytest_cache internally.
    if _READ_ONLY_TEST_COMMAND_PATTERN.match(stripped):
        return False
    return bool(_SHELL_MUTATION_PATTERN.search(stripped))


async def sync_shell_mutations(
    context: ToolExecutionContext,
    *,
    command: str,
    declared_output_paths: list[str] | None = None,
    limit: int = 64,
) -> dict[str, Any]:
    """Refresh CI state for files currently dirty after a mutating shell command.

    This is intentionally conservative: it only runs for commands that look
    mutating and only when the sandbox cwd is a git checkout. The goal is to
    keep CI caches and hotspots in sync when an
    agent edits files via shell commands instead of structured edit tools.
    """
    declared_output_paths = normalize_scope_paths(declared_output_paths or [])
    missing_decl = shell_mutation_declaration_error(
        context,
        command=command,
        declared_output_paths=declared_output_paths,
    )
    if missing_decl is not None:
        return {
            "enabled": False,
            "files": 0,
            "truncated": False,
            "missing_declarations": True,
            "error": missing_decl,
        }
    if not command_may_mutate_workspace(command) and not declared_output_paths:
        return {"enabled": False, "files": 0, "truncated": False}

    sandbox = get_daytona_sandbox(context)
    cwd = get_daytona_cwd(context)
    if sandbox is None or not cwd:
        return {"enabled": False, "files": 0, "truncated": False}

    try:
        root_resp = await sandbox.process.exec(
            f"git -C {shlex.quote(cwd)} rev-parse --show-toplevel",
            timeout=20,
        )
    except Exception:
        logger.debug("Shell sync skipped: could not resolve git root for %s", cwd, exc_info=True)
        return {"enabled": False, "files": 0, "truncated": False}

    git_root = (getattr(root_resp, "result", "") or "").strip()
    if getattr(root_resp, "exit_code", 1) != 0 or not git_root:
        return {"enabled": False, "files": 0, "truncated": False}

    if declared_output_paths:
        dirty_paths = [
            path if path.startswith("/") else os.path.normpath(f"{git_root}/{path}")
            for path in declared_output_paths
        ]
    else:
        try:
            status_resp = await sandbox.process.exec(
                f"git -C {shlex.quote(git_root)} status --porcelain --untracked-files=all",
                timeout=30,
            )
        except Exception:
            logger.debug("Shell sync skipped: git status failed for %s", git_root, exc_info=True)
            return {"enabled": True, "files": 0, "truncated": False}

        if getattr(status_resp, "exit_code", 1) != 0:
            return {"enabled": True, "files": 0, "truncated": False}

        dirty_paths = _parse_git_status_paths((getattr(status_resp, "result", "") or ""), git_root)
    truncated = len(dirty_paths) > limit
    changed_count = 0
    for file_path in dirty_paths[:limit]:
        try:
            raw = await sandbox.fs.download_file(file_path)
        except Exception:
            sync_deleted_file(
                context,
                file_path,
                edit_type="shell_mutation",
                description=f"Shell command: {command[:160]}",
            )
            changed_count += 1
            continue

        content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        sync_write_to_ci(
            context,
            file_path,
            content,
            edit_type="shell_mutation",
            description=f"Shell command: {command[:160]}",
        )
        changed_count += 1

    return {
        "enabled": True,
        "files": changed_count,
        "truncated": truncated,
        "declared_output_paths": declared_output_paths,
    }


def _parse_git_status_paths(output: str, git_root: str) -> list[str]:
    """Parse ``git status --porcelain`` output into absolute paths."""
    seen: set[str] = set()
    paths: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        status = line[:2]
        payload = line[3:].strip()
        if not payload:
            continue
        candidates = payload.split(" -> ") if " -> " in payload else [payload]
        for rel_path in candidates:
            abs_path = os.path.normpath(os.path.join(git_root, rel_path))
            if abs_path in seen:
                continue
            seen.add(abs_path)
            paths.append(abs_path)
            if "D" in status:
                break
    return paths


__all__ = [
    "command_may_mutate_workspace",
    "shell_mutation_declaration_error",
    "sync_shell_mutations",
]
