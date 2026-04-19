"""Configuration helpers for Git workspace CodeAct auditing."""

from __future__ import annotations

import os

DEFAULT_GIT_WORKSPACE_POOL_SIZE_PER_SANDBOX = 20
_ENV_POOL_SIZE = "CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX"


def git_workspace_pool_size_per_sandbox() -> int:
    """Return the configured per-sandbox Git workspace pool size."""

    raw = os.environ.get(_ENV_POOL_SIZE, "").strip()
    if not raw:
        return DEFAULT_GIT_WORKSPACE_POOL_SIZE_PER_SANDBOX
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_GIT_WORKSPACE_POOL_SIZE_PER_SANDBOX
    return max(0, value)


__all__ = [
    "DEFAULT_GIT_WORKSPACE_POOL_SIZE_PER_SANDBOX",
    "git_workspace_pool_size_per_sandbox",
]
