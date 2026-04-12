# Deprecated: canonical scope system replaced by TaskCenter scope_paths prefix matching.
# Stub functions kept only for backward compatibility with atlas/ (also a deletion target).
# Delete this file together with code_intelligence/atlas/ in the same migration phase.

from __future__ import annotations

from typing import Any


def scope_of_artifact(artifact: Any) -> str:
    """Stub — canonical scopes are no longer used."""
    if isinstance(artifact, dict):
        paths = artifact.get("target_paths") or []
        if paths:
            return "|".join(sorted(paths))
    return ""


def canonicalize_scope(scope: str) -> str:
    """Stub — returns input unchanged."""
    return scope
