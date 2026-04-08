"""Stable ``project_key`` derivation for the atlas.

Single entry point — ``project_key_for(repo_root)`` — so every caller
uses the same rule. Keys are the SHA-256 prefix of the resolved absolute
path: short enough to index cheaply, stable across CWD changes, and
insensitive to symlinks. The raw path is kept in a separate column on
:class:`team.atlas.model.ProjectAtlasRecord` for human inspection.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


_KEY_BYTES = 16  # 128 bits — collision-free for any realistic project set


def project_key_for(repo_root: str | Path | None) -> str:
    """Return a stable key for *repo_root*, or empty string if unusable.

    An empty string is the sentinel for "no atlas" — atlas tools should
    degrade gracefully when they receive it instead of raising.
    """
    if not repo_root:
        return ""
    try:
        resolved = Path(repo_root).resolve()
    except (OSError, RuntimeError):
        return ""
    canonical = str(resolved).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[: _KEY_BYTES * 2]
