"""Pure helpers for canonicalizing scout ``target_paths`` into a stable key.

``canonical_scope`` is the dedup key used by shared briefings (§13) and
render-time deduplication (§2b). Two scouts that independently cover the
same paths must produce the same canonical_scope regardless of input
order, trailing slashes, or ``./`` prefixes.
"""

from __future__ import annotations

from typing import Any


def canonicalize_scope(target_paths: list[str]) -> str:
    """Normalize and join a list of paths into a deterministic scope key.

    Rules (applied in order):
        1. Strip surrounding whitespace.
        2. Strip a single leading ``./``.
        3. Strip trailing ``/``.
        4. Drop empty strings.
        5. Deduplicate.
        6. Sort lexicographically.
        7. Join with ``|``.
    """
    cleaned: set[str] = set()
    for raw in target_paths or ():
        if not isinstance(raw, str):
            continue
        p = raw.strip()
        if p.startswith("./"):
            p = p[2:]
        p = p.rstrip("/")
        if p:
            cleaned.add(p)
    return "|".join(sorted(cleaned))


def scope_of_artifact(artifact: Any) -> str | None:
    """Extract a canonical scope key from a brief-shaped artifact, if any.

    Resolution order, mirroring §13:
        1. Explicit ``canonical_scope`` field on the dict.
        2. Derived from ``target_paths`` via :func:`canonicalize_scope`.
        3. ``None`` — caller decides what to fall back on.

    Used by every site that needs to dedup briefs by scope (the
    ``submit_summary`` injection, ``share_briefing`` key resolution, and
    the render-time reader fallback) so the three layers stay in lock-step.
    """
    if not isinstance(artifact, dict):
        return None
    explicit = artifact.get("canonical_scope")
    if isinstance(explicit, str) and explicit:
        return explicit
    target_paths = artifact.get("target_paths")
    if isinstance(target_paths, list):
        derived = canonicalize_scope(
            [p for p in target_paths if isinstance(p, str)]
        )
        return derived or None
    return None
