"""Atlas freshness — git-independent, ledger + content-hash based.

Three signals drive Atlas staleness:

1. **Ledger fast path.** :class:`code_intelligence.editing.ledger.Ledger`
   is an in-memory, agent-attributed append-only log of every edit in
   the current process. Within a session it is authoritative: if no
   entry under a chunk's scope has been recorded since the chunk's
   ``snapshot_time``, the chunk is fresh in O(log n). The cutoff is the
   **pre-read snapshot time** the writer captured *before* scouting the
   files — not the DB ``updated_at`` — so edits that landed between
   "files read" and "row committed" are detected as stale instead of
   being silently skipped.

2. **Content-hash cold path.** :attr:`AtlasChunk.content_hashes` stores
   a ``path → sha256[:16]`` map captured at write time. On cold start
   (fresh process, empty ledger) the chunk is proven fresh iff:
     a) every tracked file still hashes to its stored value, and
     b) the current file set under ``target_paths`` has **no new files**
        that weren't tracked at write time.
   The second check catches the class of staleness where a concurrent
   writer *added* a file to the scope after the chunk was written —
   hash-only comparison would miss it entirely.

3. **TTL.** An optional max-age bound so briefs cannot linger forever
   under scopes that happen never to be touched.

Neither signal touches git.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from code_intelligence.atlas.store import AtlasChunk
from team.context.canonicalize import canonicalize_scope

if TYPE_CHECKING:
    from code_intelligence.editing.ledger import Ledger

logger = logging.getLogger(__name__)

DEFAULT_ATLAS_MAX_AGE_SECONDS = 6 * 3600
MIN_COMPLETE_SCOPE_COVERAGE = 0.9


# ---------------------------------------------------------------------------
# Stat-cached content hashing
# ---------------------------------------------------------------------------
#
# Hashing a whole subsystem scope costs O(files × filesize). Under cold
# starts the same files are re-hashed repeatedly, and the content almost
# never changes between calls. The cache keys on ``(path, mtime_ns, size)``
# — any real mutation invalidates the key for free via the mtime/size
# change, so we never serve a stale hash. Bounded by ``_CACHE_MAX`` to
# keep memory trivial under long-lived processes.

_CACHE_MAX = 4096
_hash_cache: dict[tuple[str, int, int], str] = {}


def content_hash(text: str) -> str:
    """16-char sha256 prefix of UTF-8-encoded *text*.

    Matches the hash format used by ``code_intelligence``. For file
    hashing prefer :func:`hash_file`, which hashes raw bytes and works
    correctly for binary files.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _raw_hash_file(path: Path) -> str | None:
    """Hash *path* as raw bytes. Returns ``None`` if the file cannot be read.

    Hashing bytes (not decoded text) is what lets us detect mutations to
    binary files — the previous ``read_text`` path silently dropped any
    non-UTF-8 file from ``content_hashes`` entirely, so replacing a
    binary under scope was invisible to freshness checks.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def hash_file(path: str | Path) -> str | None:
    """Return the stat-cached content hash of *path*, or ``None`` if gone.

    The cache keys on ``(str(path), mtime_ns, size)``, so any real
    mutation invalidates the entry for free. Missing files and
    unreadable files both return ``None`` — both correctly register as
    stale when compared against a stored hash.
    """
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    cached = _hash_cache.get(key)
    if cached is not None:
        return cached
    h = _raw_hash_file(p)
    if h is None:
        return None
    if len(_hash_cache) >= _CACHE_MAX:
        # FIFO-ish eviction via CPython dict insertion order. Good
        # enough for a hot-path cache that is write-once, read-many.
        _hash_cache.pop(next(iter(_hash_cache)))
    _hash_cache[key] = h
    return h


def _clear_hash_cache() -> None:
    """Test helper — drop the stat cache so a fresh run sees a clean slate."""
    _hash_cache.clear()


def hash_paths_under(scope_paths: list[str], repo_root: str | Path) -> dict[str, str]:
    """Hash every regular file under each scope path.

    Used by the atlas writer to snapshot a chunk's inputs so cold-start
    freshness checks have something to compare against.
    """
    out: dict[str, str] = {}
    for target in _iter_scope_files(scope_paths, repo_root):
        h = hash_file(target)
        if h is not None:
            out[str(target)] = h
    return out


def _iter_scope_files(
    scope_paths: list[str], repo_root: str | Path
) -> list[Path]:
    """Resolve the list of files currently under *scope_paths*.

    All paths are ``resolve()``-d so write-time and read-time produce
    identical keys on symlinked filesystems (e.g. macOS ``/tmp`` →
    ``/private/tmp``). Without this, the cold-path added-file check
    would false-positive every chunk on such systems.
    """
    root = Path(repo_root)
    files: list[Path] = []
    for raw in scope_paths:
        rel = raw.strip().rstrip("/")
        if not rel:
            continue
        base = Path(rel) if Path(rel).is_absolute() else root / rel
        try:
            target = base.resolve()
        except OSError:
            target = base
        if target.is_file():
            files.append(target)
            continue
        if not target.is_dir():
            continue
        for p in target.rglob("*"):
            if p.is_file():
                files.append(p)
    return files


# ---------------------------------------------------------------------------
# Scope matching (ledger fast path)
# ---------------------------------------------------------------------------


def is_subsystem_stale(chunk: AtlasChunk, changed_files: set[str]) -> bool:
    """Return True if any file under the chunk's scope is in *changed_files*."""
    if not changed_files:
        return False
    target_paths = _target_paths(chunk)
    if not target_paths:
        return True
    for path in changed_files:
        path = _normalise_changed_path(path, chunk.repo_root)
        for scope in target_paths:
            if path == scope or path.startswith(scope.rstrip("/") + "/"):
                return True
    return False


def changes_since_chunk(chunk: AtlasChunk, ledger: "Ledger") -> set[str]:
    """Return file paths touched in *ledger* after the chunk's cutoff.

    The cutoff is ``chunk.snapshot_time`` when present (the wall-clock
    captured *before* the scout read files), falling back to
    ``chunk.updated_at`` for rows written before snapshot_time existed.
    Using snapshot_time closes the race where an edit lands between
    "files read" and "row committed".
    """
    cutoff = _ledger_cutoff(chunk)
    if cutoff is None:
        return set()
    entries = ledger.changes_since(cutoff)
    raw_root = (chunk.repo_root or "").rstrip("/")
    resolved_root = _resolve_once(raw_root) if raw_root else ""
    out: set[str] = set()
    for entry in entries:
        out.add(_normalise_ledger_path(entry.file_path, resolved_root, raw_root))
    return out


def _ledger_cutoff(chunk: AtlasChunk) -> float | None:
    if chunk.snapshot_time and chunk.snapshot_time > 0:
        return float(chunk.snapshot_time)
    if chunk.updated_at is not None:
        return _as_utc(chunk.updated_at).timestamp()
    return None


# ---------------------------------------------------------------------------
# Primary entry point
# ---------------------------------------------------------------------------


def is_chunk_fresh(
    chunk: AtlasChunk,
    *,
    ledger: "Ledger | None" = None,
    max_age_seconds: float | None = None,
) -> bool:
    """Return True iff *chunk* can be proven fresh."""
    fresh, _ = freshness_status(
        chunk,
        ledger=ledger,
        max_age_seconds=max_age_seconds,
    )
    return fresh


def freshness_status(
    chunk: AtlasChunk,
    *,
    ledger: "Ledger | None" = None,
    max_age_seconds: float | None = None,
) -> tuple[bool, str | None]:
    """Return True iff *chunk* can be proven fresh.

    Resolution order:

    1. **TTL gate** — if ``max_age_seconds`` is set and ``updated_at``
       is older than that bound, the chunk is stale regardless of other
       signals. This bounds drift from sources the ledger cannot see
       (e.g. dependency upgrades that don't touch scope files).
    2. **Ledger fast path** — O(log n) scope intersection against edits
       since ``snapshot_time``.
    3. **Content-hash cold path** — re-hash tracked files *and* verify
       no new files were added under the scope since write time.
    4. **Conservative fallback** — if neither signal is available,
       return False.
    """
    if max_age_seconds is not None and chunk.updated_at is not None:
        now = datetime.now(timezone.utc)
        age = (now - _as_utc(chunk.updated_at)).total_seconds()
        if age > max_age_seconds:
            return False, (
                "atlas brief exceeded the max reuse age and must be refreshed"
            )

    cutoff = _ledger_cutoff(chunk)
    if ledger is not None and cutoff is not None:
        touched = changes_since_chunk(chunk, ledger)
        if not is_subsystem_stale(chunk, touched):
            return True, None
        return False, (
            "ledger recorded edits under this scope since the chunk snapshot"
        )

    if chunk.content_hashes:
        # (a) every tracked file must still hash identically
        for path, stored in chunk.content_hashes.items():
            current = hash_file(path)
            if current is None or current != stored:
                return False, (
                    "content hashes diverged from the working tree under this scope"
                )
        # (b) no new files may have appeared in scope
        target_paths = _target_paths(chunk)
        if target_paths and chunk.repo_root:
            current_files = {
                str(p) for p in _iter_scope_files(target_paths, chunk.repo_root)
            }
            tracked = set(chunk.content_hashes.keys())
            if current_files - tracked:
                return False, (
                    "new files appeared under this scope since the chunk was written"
                )
        return True, None

    return False, (
        "cannot prove freshness: no ledger visibility and no stored content hashes"
    )


def chunk_reuse_status(
    chunk: AtlasChunk,
    *,
    ledger: "Ledger | None" = None,
    max_age_seconds: float | None = DEFAULT_ATLAS_MAX_AGE_SECONDS,
    min_scope_coverage: float = MIN_COMPLETE_SCOPE_COVERAGE,
) -> tuple[bool, str | None]:
    """Return whether the planner should reuse a cached atlas chunk.

    Planner reuse is stricter than freshness alone: an atlas entry can
    be fresh on disk and still be too incomplete to trust as structural
    context. In that case callers should refresh it, not propagate a
    partial brief.
    """
    fresh, reason = freshness_status(
        chunk,
        ledger=ledger,
        max_age_seconds=max_age_seconds,
    )
    if not fresh:
        return False, reason
    return brief_reuse_status(
        chunk.brief,
        min_scope_coverage=min_scope_coverage,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _target_paths(chunk: AtlasChunk) -> list[str]:
    raw = chunk.brief.get("target_paths") if isinstance(chunk.brief, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for p in raw:
        if isinstance(p, str) and p.strip():
            out.append(p.strip().replace("\\", "/").removeprefix("./").rstrip("/"))
    return out


_resolved_root_cache: dict[str, str] = {}


def _resolve_once(root: str) -> str:
    """Resolve ``root`` exactly once per process — stat syscalls hurt in hot loops."""
    if not root:
        return ""
    cached = _resolved_root_cache.get(root)
    if cached is not None:
        return cached
    try:
        resolved = str(Path(root).resolve())
    except OSError:
        resolved = root
    _resolved_root_cache[root] = resolved
    return resolved


def _normalise_changed_path(path: str, repo_root: str) -> str:
    """Best-effort repo-relative normalization for a path set by the caller.

    This is the slow path used when ``is_subsystem_stale`` is called
    directly by tests or external callers with arbitrary strings. The
    hot ledger path goes through :func:`_normalise_ledger_path` which
    avoids per-entry ``.resolve()`` calls entirely.
    """
    cleaned = path.strip().replace("\\", "/").removeprefix("./").rstrip("/")
    if not cleaned:
        return cleaned
    candidate = Path(cleaned)
    if repo_root and candidate.is_absolute():
        raw_root = repo_root.rstrip("/")
        resolved_root = _resolve_once(raw_root)
        as_posix = candidate.as_posix()
        for root in (resolved_root, raw_root):
            if root and as_posix.startswith(root + "/"):
                return as_posix[len(root) + 1 :]
        return as_posix
    return candidate.as_posix()


def _normalise_ledger_path(
    path: str, resolved_root: str, raw_root: str
) -> str:
    """Strip a ``raw_root`` or ``resolved_root`` prefix from a ledger entry.

    The ledger stores whatever absolute path the edit tool was given,
    which on macOS may start with ``/tmp`` while ``resolved_root`` is
    ``/private/tmp``. Checking both forms avoids a per-entry
    ``os.path.realpath`` stat syscall on the hot freshness path.
    """
    for root in (resolved_root, raw_root):
        if root and path.startswith(root + "/"):
            return path[len(root) + 1 :]
    return path


def brief_reuse_status(
    brief: dict[str, object] | None,
    *,
    min_scope_coverage: float,
) -> tuple[bool, str | None]:
    brief = brief if isinstance(brief, dict) else {}
    if _is_explicit_empty_area_brief(brief):
        return True, None

    coverage = brief.get("scope_coverage")
    if not isinstance(coverage, (int, float)):
        return False, "atlas brief is missing scope_coverage and cannot be trusted"
    if float(coverage) < min_scope_coverage:
        return False, (
            f"atlas brief coverage {float(coverage):.2f} is below the reuse threshold"
        )

    if _normalised_subdivisions(brief.get("suggested_subdivisions")):
        return False, (
            "atlas brief requested further subdivision and should be refreshed"
        )

    gaps = brief.get("gaps")
    if isinstance(gaps, str) and gaps.strip():
        return False, "atlas brief contains unresolved gaps and should be refreshed"
    return True, None


def _is_explicit_empty_area_brief(brief: dict[str, object]) -> bool:
    coverage = brief.get("scope_coverage")
    if not isinstance(coverage, (int, float)) or float(coverage) != 0.0:
        return False
    files = brief.get("files")
    if not isinstance(files, list) or files:
        return False
    return not _normalised_subdivisions(brief.get("suggested_subdivisions"))


def _normalised_subdivisions(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [p.strip() for p in raw if isinstance(p, str) and p.strip()]


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical_subsystem_key(paths: list[str]) -> str:
    """Compute the subsystem key the atlas uses for a list of paths."""
    return canonicalize_scope(paths)
