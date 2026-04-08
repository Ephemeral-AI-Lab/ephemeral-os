"""Atlas freshness — git-independent, ledger + content-hash based.

Two signals drive Atlas staleness:

1. :class:`code_intelligence.editing.ledger.Ledger` — an in-memory,
   agent-attributed append-only log of every edit in the current process.
   Within a session the ledger is authoritative: if no entry under a
   chunk's scope has been recorded since the chunk was written, the
   chunk is fresh in O(log n).

2. :attr:`team.atlas.store.AtlasChunk.content_hashes` — a persisted
   map of ``path → sha256[:16]`` captured at write time. On cold start
   (fresh process, empty ledger) the chunk is proven fresh iff every
   tracked file still hashes to the stored value. Any missing file or
   mismatch marks the chunk stale.

Neither signal touches git. The earlier git-based helpers were removed:
they were unreliable in sandboxes (no ``.git``), invisible to untracked
files, and subprocess-heavy on the hot path.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from team.context.canonicalize import canonicalize_scope
from team.atlas.store import AtlasChunk

if TYPE_CHECKING:
    from code_intelligence.editing.ledger import Ledger

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    """16-char sha256 prefix — matches ``code_intelligence`` hash format."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def hash_file(path: str | Path) -> str | None:
    """Return the content hash of *path*, or ``None`` if unreadable."""
    try:
        return content_hash(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def hash_paths_under(scope_paths: list[str], repo_root: str | Path) -> dict[str, str]:
    """Hash every regular file under each scope path.

    Used by the atlas writer to snapshot a chunk's inputs so cold-start
    freshness checks have something to compare against. Returns an empty
    dict when no files exist (e.g. ``repo_root`` is synthetic in tests).
    """
    root = Path(repo_root)
    out: dict[str, str] = {}
    for raw in scope_paths:
        rel = raw.strip().rstrip("/")
        if not rel:
            continue
        target = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
        if target.is_file():
            h = hash_file(target)
            if h is not None:
                out[str(target)] = h
            continue
        if not target.is_dir():
            continue
        for p in target.rglob("*"):
            if p.is_file():
                h = hash_file(p)
                if h is not None:
                    out[str(p)] = h
    return out


def is_subsystem_stale(chunk: AtlasChunk, changed_files: set[str]) -> bool:
    """Return True if any file under the chunk's scope is in *changed_files*.

    A chunk's scope is derived from ``target_paths`` on the brief body
    (the same field scouts record). A scope with no ``target_paths`` is
    conservatively treated as stale when anything in the repo changed,
    because we cannot prove coverage.
    """
    if not changed_files:
        return False
    target_paths = _target_paths(chunk)
    if not target_paths:
        return True
    for path in changed_files:
        for scope in target_paths:
            if path == scope or path.startswith(scope.rstrip("/") + "/"):
                return True
    return False


def changes_since_chunk(chunk: AtlasChunk, ledger: "Ledger") -> set[str]:
    """Return file paths touched in *ledger* after ``chunk.updated_at``.

    Returns an empty set when ``chunk.updated_at`` is missing — the
    caller should treat that as "no ledger visibility" and fall back to
    content-hash comparison via :func:`is_chunk_fresh`.
    """
    if chunk.updated_at is None:
        return set()
    since = chunk.updated_at.timestamp()
    entries = ledger.changes_since(since)
    return {e.file_path for e in entries}


def is_chunk_fresh(
    chunk: AtlasChunk,
    *,
    ledger: "Ledger | None" = None,
) -> bool:
    """Return True iff *chunk* can be proven fresh.

    Resolution order:

    1. **Ledger fast path** — if a ledger is supplied and the chunk has
       an ``updated_at``, intersect the ledger's post-chunk entries with
       the chunk's scope. Empty intersection proves freshness in O(log n).
    2. **Content-hash cold path** — if no ledger visibility but the
       chunk carries ``content_hashes``, re-hash each tracked file from
       disk and compare. Any missing file or hash mismatch means stale.
    3. **Conservative fallback** — if neither signal is available,
       return False. "Cannot prove fresh" is always safer than
       "assume fresh".
    """
    if ledger is not None and chunk.updated_at is not None:
        touched = changes_since_chunk(chunk, ledger)
        return not is_subsystem_stale(chunk, touched)

    if chunk.content_hashes:
        for path, stored in chunk.content_hashes.items():
            current = hash_file(path)
            if current is None or current != stored:
                return False
        return True

    return False


def _target_paths(chunk: AtlasChunk) -> list[str]:
    """Normalise ``target_paths`` off an atlas chunk's brief."""
    raw = chunk.brief.get("target_paths") if isinstance(chunk.brief, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for p in raw:
        if isinstance(p, str) and p.strip():
            out.append(p.strip().rstrip("/"))
    return out


def canonical_subsystem_key(paths: list[str]) -> str:
    """Compute the subsystem key the atlas uses for a list of paths.

    Delegates to :func:`team.context.canonicalize.canonicalize_scope` so
    the Phase 1 briefing dedup key and the Phase 2 atlas chunk key stay
    in lock-step: one canonicalization rule, two consumers.
    """
    return canonicalize_scope(paths)
