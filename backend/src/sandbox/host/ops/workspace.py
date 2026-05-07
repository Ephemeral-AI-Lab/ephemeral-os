"""Sandbox workspace discovery and runtime context metadata.

Lifted from ``sandbox.lifecycle.workspace``. The ``sandbox`` argument to
``discover_workspace`` / ``_sandbox_project_root`` still has provider-specific
shape (``project_dir``, ``labels``, ``process.exec``); collapsing that gap is
a separate plan (see ``.omc/plans/sandbox-provider-agnostic-lifecycle.md``
§Out of scope).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

def _sandbox_project_root(sandbox: Any) -> str | None:
    project_dir = getattr(sandbox, "project_dir", None)
    if isinstance(project_dir, str) and project_dir.strip():
        return project_dir.strip()
    labels = getattr(sandbox, "labels", None)
    if isinstance(labels, dict):
        label_dir = labels.get("project_dir")
        if isinstance(label_dir, str) and label_dir.strip():
            return label_dir.strip()
    return None


def discover_workspace(sandbox: Any) -> str | None:
    project_dir = _sandbox_project_root(sandbox)
    if project_dir:
        return project_dir
    try:
        resp = sandbox.process.exec("pwd")
        if resp.exit_code == 0 and resp.result:
            result = str(resp.result).strip()
            return result or None
    except Exception:
        pass
    return None


async def discover_workspace_async(sandbox: Any) -> str | None:
    project_dir = _sandbox_project_root(sandbox)
    if project_dir:
        return project_dir
    try:
        resp = await sandbox.process.exec("pwd")
        if resp.exit_code == 0 and resp.result:
            result = str(resp.result).strip()
            return result or None
    except Exception:
        pass
    return None


def prepare_sandbox_runtime_context(
    context: Any,
    *,
    sandbox: Any,
    workspace_root: str | None,
) -> None:
    """Inject provider-neutral sandbox runtime metadata.

    Provider implementations own provider-specific context keys and adapter
    registration. This helper only normalizes workspace metadata shared by
    sandbox tools.
    """
    repo_root = str(context.get("repo_root") or "").strip()
    if not repo_root:
        candidate = str(workspace_root or "").strip()
        if not candidate and sandbox is not None:
            candidate = _sandbox_project_root(sandbox) or ""
        if candidate:
            repo_root = candidate
            context["repo_root"] = repo_root

    if not context.get("exec_cwd") and repo_root:
        context["exec_cwd"] = repo_root


__all__ = [
    "_sandbox_project_root",
    "discover_workspace",
    "discover_workspace_async",
    "prepare_sandbox_runtime_context",
]
