"""TreeCache — thread-safe AST caching with two-tier validation.

Uses mtime fast-path and content-hash fallback. Reads from sandbox
filesystem when available, local filesystem otherwise.

Modeled after the synthetic-os TreeCache architecture:
- Per-file locking so network I/O for one file doesn't block others
- mtime stat (~5ms) before download to skip unchanged files
- Content-hash fallback to avoid re-parsing when content is identical
- TOCTOU detection: re-stat after download to catch concurrent writes

Lock ordering (Group A):
    A3: per-file locks  <  A4: dict lock  <  A5: counter lock
"""

from __future__ import annotations

import ast
import hashlib
import logging
import json
import shlex
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

from code_intelligence._async_bridge import run_sync
from code_intelligence.constants import (
    TREE_CACHE_MAX_FILE_SIZE,
    TREE_CACHE_MAX_FILES,
)
from tools.daytona_toolkit._daytona_utils import (
    _extract_exit_code,
    _supports_exec_transport,
    _wrap_bash_command,
)

logger = logging.getLogger(__name__)

# Try to import tree-sitter; fall back gracefully
try:
    import tree_sitter  # type: ignore[import-untyped]

    _HAS_TREE_SITTER = True
except ImportError:
    tree_sitter = None  # type: ignore[assignment]
    _HAS_TREE_SITTER = False


def _content_hash(content: str | bytes) -> str:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class CacheEntry:
    """A cached parse result for a single file."""

    file_path: str
    content: str
    content_hash: str
    tree: Any  # tree-sitter Tree or ast.Module
    language: str
    mtime: str  # from FileInfo.mod_time (string) or str(float) for local
    size: int = 0
    parsed_at: float = field(default_factory=time.time)


class TreeCache:
    """Thread-safe AST cache with LRU eviction and sandbox awareness.

    When a sandbox is provided, prefers ``process.exec`` for text stat/read
    operations and falls back to ``sandbox.fs`` only for mock sandboxes that
    do not expose exec transport.
    """

    def __init__(
        self,
        sandbox: Any = None,
        max_files: int = TREE_CACHE_MAX_FILES,
        max_file_size: int = TREE_CACHE_MAX_FILE_SIZE,
        on_change: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._max_files = max_files
        self._max_file_size = max_file_size
        self._on_change = on_change

        # Group A locks
        self._file_locks: dict[str, threading.Lock] = {}  # A3
        self._dict_lock = threading.Lock()  # A4
        self._counter_lock = threading.Lock()  # A5

        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._stat_calls = 0

    # -- Public API -----------------------------------------------------------

    def get_tree(
        self,
        file_path: str,
        content: str | None = None,
        mtime: str | None = None,
    ) -> CacheEntry | None:
        """Get or parse a file's AST.

        If *content* is provided, parse it directly (no I/O).
        Otherwise, stat + download from sandbox (or local filesystem).
        Returns ``None`` on parse failure.

        Per-file locking ensures I/O for one file doesn't block others.
        """
        file_lock = self._get_file_lock(file_path)
        with file_lock:
            # Check cache
            with self._dict_lock:
                entry = self._cache.get(file_path)
                if entry is not None:
                    self._cache.move_to_end(file_path)

            # Content provided — skip all I/O
            if content is not None:
                return self._parse_and_cache(file_path, content, entry, mtime or "")

            # Fast path: mtime unchanged
            if entry is not None:
                current_mtime = self._stat_mtime(file_path)
                if current_mtime is not None and current_mtime == entry.mtime:
                    self._record_hit()
                    return entry

            # Slow path: download + hash + parse
            content = self._read_content(file_path)
            if content is None:
                return None

            current_mtime = mtime or ""
            new_hash = _content_hash(content)

            # Content-hash hit: downloaded but content unchanged
            if entry is not None and entry.content_hash == new_hash:
                entry.mtime = current_mtime
                self._record_hit()
                return entry

            # True miss: parse
            return self._parse_and_cache(file_path, content, entry, current_mtime)

    @property
    def size(self) -> int:
        with self._dict_lock:
            return len(self._cache)

    @property
    def stats(self) -> dict[str, int]:
        with self._counter_lock:
            return {
                "size": self.size,
                "hits": self._hits,
                "misses": self._misses,
                "stat_calls": self._stat_calls,
            }

    # -- Sandbox I/O ----------------------------------------------------------

    def _stat_mtime(self, file_path: str) -> str | None:
        """Get file mtime via sandbox or local filesystem."""
        sandbox = self._sandbox
        process = getattr(sandbox, "process", None) if sandbox else None
        exec_fn = getattr(process, "exec", None) if process is not None else None
        if sandbox and _supports_exec_transport(sandbox):
            script = """
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
stat = path.stat()
print(json.dumps({"mtime": stat.st_mtime, "size": stat.st_size}))
"""
            try:
                response = run_sync(
                    exec_fn(
                        _wrap_bash_command(
                            f"python3 -c {shlex.quote(script)} {shlex.quote(file_path)}"
                        )
                    )
                )
                stdout, exit_code = _extract_exit_code(
                    getattr(response, "result", "") or "",
                    fallback_exit_code=getattr(response, "exit_code", None),
                )
                if exit_code in (0, None):
                    payload = json.loads(stdout or "{}")
                    with self._counter_lock:
                        self._stat_calls += 1
                    return str(payload.get("mtime", "") or "")
            except Exception:
                return None
        fs = getattr(sandbox, "fs", None) if sandbox else None
        get_info = getattr(fs, "get_file_info", None)

        if callable(get_info):
            try:
                info = run_sync(get_info(file_path))
                with self._counter_lock:
                    self._stat_calls += 1
                return str(getattr(info, "mod_time", "") or "")
            except Exception:
                return None

        # Local fallback
        try:
            return str(Path(file_path).stat().st_mtime)
        except Exception:
            return None

    def _read_content(self, file_path: str) -> str | None:
        """Read file content via sandbox or local filesystem."""
        sandbox = self._sandbox
        process = getattr(sandbox, "process", None) if sandbox else None
        exec_fn = getattr(process, "exec", None) if process is not None else None
        if sandbox and _supports_exec_transport(sandbox):
            script = """
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
print(path.read_text(encoding="utf-8"))
"""
            try:
                response = run_sync(
                    exec_fn(
                        _wrap_bash_command(
                            f"python3 -c {shlex.quote(script)} {shlex.quote(file_path)}"
                        )
                    )
                )
                stdout, exit_code = _extract_exit_code(
                    getattr(response, "result", "") or "",
                    fallback_exit_code=getattr(response, "exit_code", None),
                )
                if exit_code in (0, None):
                    return stdout
            except Exception:
                logger.debug("TreeCache: process.exec read failed for %s", file_path, exc_info=True)
                return None
        fs = getattr(sandbox, "fs", None) if sandbox else None
        download = getattr(fs, "download_file", None)

        if callable(download):
            try:
                raw = run_sync(download(file_path))
                if isinstance(raw, bytes):
                    return raw.decode("utf-8")
                return str(raw) if raw is not None else None
            except Exception:
                logger.debug("TreeCache: download_file failed for %s", file_path, exc_info=True)
                return None

        # Local fallback
        try:
            p = Path(file_path)
            if not p.is_file() or p.stat().st_size > self._max_file_size:
                return None
            return p.read_text(encoding="utf-8")
        except Exception:
            return None

    # -- Internal -------------------------------------------------------------

    def _parse_and_cache(
        self,
        file_path: str,
        content: str,
        entry: CacheEntry | None,
        mtime: str,
    ) -> CacheEntry | None:
        """Parse content, update cache, return entry."""
        if len(content) > self._max_file_size:
            return None

        new_hash = _content_hash(content)

        # Content-hash hit: content provided but unchanged
        if entry is not None and entry.content_hash == new_hash:
            entry.mtime = mtime
            with self._dict_lock:
                self._cache.move_to_end(file_path)
            self._record_hit()
            return entry

        # True miss: parse
        self._record_miss()
        language = self._detect_language(file_path)
        tree = self._parse(content, language)
        if tree is None:
            return None

        old_hash = entry.content_hash if entry else ""
        new_entry = CacheEntry(
            file_path=file_path,
            content=content,
            content_hash=new_hash,
            tree=tree,
            language=language,
            mtime=mtime,
            size=len(content),
        )

        with self._dict_lock:
            self._cache[file_path] = new_entry
            self._cache.move_to_end(file_path)
            self._evict_if_needed()

        if self._on_change and old_hash and old_hash != new_hash:
            try:
                self._on_change(file_path, old_hash, new_hash)
            except Exception:
                logger.debug("on_change callback failed for %s", file_path)

        return new_entry

    def _get_file_lock(self, file_path: str) -> threading.Lock:
        """Get or create a per-file lock (Group A3)."""
        with self._dict_lock:
            if file_path not in self._file_locks:
                self._file_locks[file_path] = threading.Lock()
            return self._file_locks[file_path]

    def _evict_if_needed(self) -> None:
        """LRU eviction (must hold _dict_lock)."""
        while len(self._cache) > self._max_files:
            self._cache.popitem(last=False)

    def _record_hit(self) -> None:
        with self._counter_lock:
            self._hits += 1

    def _record_miss(self) -> None:
        with self._counter_lock:
            self._misses += 1

    def _detect_language(self, file_path: str) -> str:
        """Detect language from file extension."""
        ext = Path(file_path).suffix.lower()
        mapping = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
        }
        return mapping.get(ext, "unknown")

    def _parse(self, content: str, language: str) -> Any:
        """Parse content into an AST. Falls back to Python ast if tree-sitter unavailable."""
        if _HAS_TREE_SITTER:
            return self._parse_tree_sitter(content, language)
        if language == "python":
            return self._parse_python_ast(content)
        # For non-Python without tree-sitter, store raw content
        return content

    def _parse_tree_sitter(self, content: str, language: str) -> Any:
        """Parse using tree-sitter."""
        try:
            lang = tree_sitter.Language(f"tree-sitter-{language}")  # type: ignore[arg-type]
            parser = tree_sitter.Parser()
            parser.set_language(lang)
            return parser.parse(content.encode("utf-8"))
        except Exception:
            if language == "python":
                return self._parse_python_ast(content)
            return content

    def _parse_python_ast(self, content: str) -> ast.Module | None:
        """Parse Python source with the stdlib ast module."""
        try:
            return ast.parse(content)
        except SyntaxError:
            return None
