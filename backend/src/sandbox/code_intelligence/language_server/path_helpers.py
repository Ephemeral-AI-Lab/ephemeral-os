"""Path / line / language helpers shared between LSP backends.

Phase 3.6 split these helpers out of ``python_backend.py`` so that the
:class:`LspClient` can drop the jedi-specific mixin while keeping the
load-bearing column/line resolution semantics intact.

Provides :class:`LspPathMixin` which exposes:

* ``_resolve_path`` — workspace-relative → absolute resolution.
* ``_resolve_column`` — when the caller passed ``character=0``, advance to
  the actual symbol name (otherwise jedi/basedpyright resolve the
  ``def``/``class`` keyword instead of the function/class).
* ``_read_line`` / ``_read_line_uncached`` — local-or-sandbox single-line
  read with a small LRU cache.
* ``_detect_language`` — file-extension → language id.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from pathlib import Path

from sandbox.code_intelligence.core.path_utils import resolve_workspace_path

logger = logging.getLogger(__name__)


_DEF_CLASS_RE = re.compile(r"^(\s*(?:async\s+)?(?:def|class)\s+)")


class LspPathMixin:
    """Mixin contributing path / line / language helpers to :class:`LspClient`."""

    _workspace_root: str
    _cache_max: int
    _line_cache_lock: threading.Lock
    _line_cache: OrderedDict[tuple[str, int], str | None]

    def _resolve_path(self, file_path: str) -> str:
        """Resolve a potentially relative file path against workspace root."""
        return resolve_workspace_path(file_path, self._workspace_root)

    def _resolve_column(self, file_path: str, line: int, character: int) -> int:
        """When *character* is 0, advance to the actual symbol-name column.

        Both jedi (legacy) and basedpyright resolve position-based queries
        against the cursor. ``character=0`` lands on leading indentation
        (or on the ``def``/``class`` keyword), producing empty results. For
        ``def``/``class`` lines the cursor is placed past the keyword onto
        the symbol name.
        """
        if character != 0:
            return character
        try:
            text = self._read_line(file_path, line)
            if text is None:
                return 0
            stripped = text.lstrip()
            if not stripped:
                return 0
            indent = len(text) - len(stripped)
            m = _DEF_CLASS_RE.match(text)
            if m:
                return len(m.group(1))
            return indent
        except Exception:
            logger.debug("_resolve_column failed for %s:%d", file_path, line)
            return 0

    def _read_line(self, file_path: str, line: int) -> str | None:
        """Read a single line from a resolved local file (1-indexed)."""
        abs_path = self._resolve_path(file_path)
        key = (abs_path, int(line))
        with self._line_cache_lock:
            if key in self._line_cache:
                self._line_cache.move_to_end(key)
                return self._line_cache[key]
            value = self._read_line_uncached(abs_path, int(line))
            self._line_cache[key] = value
            self._line_cache.move_to_end(key)
            while len(self._line_cache) > self._cache_max:
                self._line_cache.popitem(last=False)
            return value

    def _read_line_uncached(self, abs_path: str, line: int) -> str | None:
        """Read a single resolved line without consulting the local cache."""
        try:
            p = Path(abs_path)
            if not p.exists():
                return None
            lines = p.read_text(encoding="utf-8").splitlines()
            if line < 1 or line > len(lines):
                return None
            return lines[line - 1]
        except Exception:
            return None

    def _detect_language(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        return "python" if ext == ".py" else "unknown"
