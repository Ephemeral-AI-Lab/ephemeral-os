"""CI integration helpers for the Daytona toolkit.

Provides service acquisition, tree cache priming after writes,
lightweight shell-mutation reconciliation, and atlas dirty-marking.
All CI features are optional — tools degrade gracefully if no CI service
is configured.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Any

from team.runtime.registry import get as get_team_run
from tools.core.base import ToolExecutionContext

logger = logging.getLogger(__name__)

_SHELL_MUTATION_PATTERN = re.compile(
    r"(^|[;&|]\s*)("
    r"cat\s+>|tee\s|cp\s|mv\s|rm\s|touch\s|mkdir\s|install\s|ln\s|"
    r"git\s+(apply|checkout|restore|reset|clean|mv|rm)\b|"
    r"sed\s+-i\b|perl\s+-pi\b|patch\b|ed\b|ex\b|"
    r".*>>|.*[^<]>[^>]"
    r")",
    flags=re.IGNORECASE,
)


def get_ci_service(context: ToolExecutionContext) -> Any | None:
    """Get the CodeIntelligenceService from context, or None if unavailable."""
    return context.metadata.get("ci_service")


def get_daytona_sandbox(context: ToolExecutionContext) -> Any | None:
    """Get the injected Daytona sandbox object, if available."""
    return context.metadata.get("daytona_sandbox")


def get_daytona_cwd(context: ToolExecutionContext) -> str:
    """Get the injected Daytona working directory, if available."""
    return context.metadata.get("daytona_cwd") or ""


def resolve_daytona_path(path: str, context: ToolExecutionContext) -> str:
    """Resolve *path* against the injected Daytona cwd."""
    if not path:
        return get_daytona_cwd(context) or "."
    if path.startswith("/"):
        return path
    cwd = get_daytona_cwd(context)
    if not cwd:
        return path
    return os.path.normpath(f"{cwd}/{path}")


def prime_cache_after_write(context: ToolExecutionContext, file_path: str, content: str) -> None:
    """Prime the tree cache and refresh the symbol index after a write."""
    svc = get_ci_service(context)
    if svc is None:
        _note_atlas_edit(context, file_path, reason="write")
        return
    try:
        svc.tree_cache.put_content(file_path, content)
        svc.symbol_index.refresh(file_path, content)
        svc.lsp_client.invalidate(file_path)
    except Exception:
        logger.debug("CI prime_cache_after_write failed for %s", file_path)
    finally:
        _note_atlas_edit(context, file_path, reason="write")


def sync_write_to_ci(
    context: ToolExecutionContext,
    file_path: str,
    content: str,
    *,
    agent_id: str = "",
    edit_type: str = "write",
    description: str = "",
    old_hash: str = "",
    new_hash: str = "",
) -> None:
    """Record a write in the ledger/arbiter and refresh CI caches."""
    svc = get_ci_service(context)
    if svc is not None:
        try:
            arbiter = getattr(svc, "arbiter", None)
            if arbiter is not None:
                arbiter.record_edit(file_path, agent_id)
        except Exception:
            logger.debug("CI arbiter sync failed for %s", file_path, exc_info=True)
    record_edit_in_ledger(
        context,
        file_path,
        agent_id=agent_id,
        edit_type=edit_type,
        old_hash=old_hash,
        new_hash=new_hash,
        description=description,
    )
    prime_cache_after_write(context, file_path, content)


def sync_deleted_file(
    context: ToolExecutionContext,
    file_path: str,
    *,
    agent_id: str = "",
    edit_type: str = "delete",
    description: str = "",
) -> None:
    """Best-effort CI invalidation for a deleted file."""
    svc = get_ci_service(context)
    if svc is not None:
        try:
            arbiter = getattr(svc, "arbiter", None)
            if arbiter is not None:
                arbiter.record_edit(file_path, agent_id)
        except Exception:
            logger.debug("CI arbiter delete sync failed for %s", file_path, exc_info=True)
        try:
            svc.tree_cache.invalidate(file_path)
            svc.symbol_index.refresh(file_path, "")
            svc.lsp_client.invalidate(file_path)
        except Exception:
            logger.debug("CI delete invalidation failed for %s", file_path, exc_info=True)
    record_edit_in_ledger(
        context,
        file_path,
        agent_id=agent_id,
        edit_type=edit_type,
        description=description,
    )
    _note_atlas_edit(context, file_path, reason=edit_type)


def command_may_mutate_workspace(command: str) -> bool:
    """Heuristic gate for when a shell command should trigger CI reconciliation."""
    stripped = (command or "").strip()
    if not stripped:
        return False
    return bool(_SHELL_MUTATION_PATTERN.search(stripped))


async def sync_shell_mutations(
    context: ToolExecutionContext,
    *,
    command: str,
    limit: int = 64,
) -> dict[str, Any]:
    """Refresh CI state for files currently dirty after a mutating shell command.

    This is intentionally conservative: it only runs for commands that look
    mutating and only when the sandbox cwd is a git checkout. The goal is to
    keep CI caches, ledger, hotspots, and atlas invalidation in sync when an
    agent edits files via ``daytona_bash`` instead of structured edit tools.
    """
    if not command_may_mutate_workspace(command):
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

    return {"enabled": True, "files": changed_count, "truncated": truncated}


def record_edit_in_ledger(
    context: ToolExecutionContext,
    file_path: str,
    agent_id: str = "",
    edit_type: str = "edit",
    old_hash: str = "",
    new_hash: str = "",
    description: str = "",
) -> None:
    """Record an edit in the CI ledger if available."""
    svc = get_ci_service(context)
    if svc is None:
        return
    try:
        svc.ledger.record(
            file_path=file_path,
            agent_id=agent_id,
            edit_type=edit_type,
            old_hash=old_hash,
            new_hash=new_hash,
            description=description,
        )
    except Exception:
        logger.debug("CI record_edit_in_ledger failed for %s", file_path)


def _note_atlas_edit(
    context: ToolExecutionContext,
    file_path: str,
    *,
    reason: str,
) -> None:
    """Tell the live TeamRun that a file changed so atlas can refresh lazily."""
    team_run_id = context.metadata.get("team_run_id")
    if not team_run_id:
        return
    team_run = get_team_run(team_run_id)
    if team_run is None:
        return
    try:
        team_run.note_atlas_edit(file_path, reason=reason)
    except Exception:
        logger.debug("atlas dirty-mark failed for %s", file_path, exc_info=True)


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
