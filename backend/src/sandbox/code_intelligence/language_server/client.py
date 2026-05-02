"""Semantic language-server-backed code intelligence queries.

Phase 3.6 rewire: queries route through a persistent :class:`LspBackendChild`
(basedpyright stdio child, single backend, no runtime selector, no fallback).
The Phase 1 jedi.Script per-call subprocess shim was deleted in the same
commit; ``ensure_ready`` checks for the chosen backend's launch binary
instead.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from sandbox.api.transport import SandboxTransport
from sandbox.code_intelligence.core.constants import (
    LSP_CACHE_MAX_ENTRIES,
    LSP_CACHE_TTL,
)
from sandbox.code_intelligence.core.types import (
    Diagnostic,
    DiagnosticSeverity,
    HoverResult,
    ReferenceInfo,
    SymbolInfo,
)
from sandbox.code_intelligence.language_server.cache import LspCacheMixin
from sandbox.code_intelligence.language_server.lsp_host import (
    LspAsyncHost,
    LspChildCrashed,
    LspChildUnavailable,
)
from sandbox.code_intelligence.language_server.models import (
    LspTelemetry,
    _CacheEntry,
    _InflightQuery,
)
from sandbox.code_intelligence.language_server.path_helpers import LspPathMixin
from sandbox.code_intelligence.language_server.telemetry import LspTelemetryMixin
from sandbox.code_intelligence.language_server.transport import LspTransportMixin
from sandbox.code_intelligence.language_server.utils import _readiness_targets

_T = TypeVar("_T")


class LspClient(LspPathMixin, LspTransportMixin, LspCacheMixin, LspTelemetryMixin):
    """Semantic backend with a persistent LSP child + per-position caching.

    Phase 3.6: queries route through :class:`LspBackendChild` (basedpyright)
    via :class:`LspAsyncHost`. There is no jedi fallback — a child crash
    bounded to one respawn; second crash escalates :class:`LspChildUnavailable`.
    """

    def __init__(
        self,
        workspace_root: str = "",
        cache_ttl: float = LSP_CACHE_TTL,
        cache_max: int = LSP_CACHE_MAX_ENTRIES,
        *,
        transport: SandboxTransport | None = None,
        sandbox_id: str = "",
    ) -> None:
        self._workspace_root = workspace_root
        self._transport = transport
        self._sandbox_id = sandbox_id
        self._cache_ttl = cache_ttl
        self._cache_max = cache_max

        self._cache_lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._line_cache_lock = threading.Lock()
        self._host_lock = threading.Lock()

        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._inflight: dict[str, _InflightQuery] = {}
        self._line_cache: OrderedDict[tuple[str, int], str | None] = OrderedDict()
        self._telemetry = LspTelemetry()
        self._py_available: bool | None = None
        self._host: LspAsyncHost | None = None

    # -- Public query methods -------------------------------------------------

    def goto_definition(
        self,
        file_path: str,
        line: int,
        character: int,
    ) -> list[SymbolInfo]:
        """Find symbol definitions at position."""
        return self._run_cached_query(
            f"def:{file_path}:{line}:{character}",
            lambda: self._query_python(
                file_path,
                lambda: self._lsp_definitions(file_path, line, character),
                [],
            ),
        )

    def find_references(
        self,
        file_path: str,
        line: int,
        character: int,
    ) -> list[ReferenceInfo]:
        """Find all references to symbol at position."""
        return self._run_cached_query(
            f"ref:{file_path}:{line}:{character}",
            lambda: self._query_python(
                file_path,
                lambda: self._lsp_references(file_path, line, character),
                [],
            ),
        )

    def hover(
        self,
        file_path: str,
        line: int,
        character: int,
    ) -> HoverResult | None:
        """Get hover information at position."""
        return self._run_cached_query(
            f"hover:{file_path}:{line}:{character}",
            lambda: self._query_python(
                file_path,
                lambda: self._lsp_hover(file_path, line, character),
                None,
            ),
            cache_when=lambda result: result is not None,
        )

    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        """Get diagnostics for a file."""
        return self._run_cached_query(
            f"diag:{file_path}",
            lambda: self._query_python(
                file_path,
                lambda: self._lsp_diagnostics(file_path),
                [],
            ),
        )

    def invalidate(self, file_path: str) -> None:
        """Invalidate all cached results for a file."""
        resolved_path = self._resolve_path(file_path)
        candidates = {str(file_path), resolved_path}
        if self._workspace_root:
            try:
                relative_path = Path(resolved_path).relative_to(self._workspace_root)
            except ValueError:
                pass
            else:
                candidates.add(str(relative_path))
                candidates.add(relative_path.as_posix())

        with self._cache_lock:
            to_remove = [
                k
                for k in self._cache
                if any(candidate and candidate in k for candidate in candidates)
            ]
            for k in to_remove:
                del self._cache[k]
        with self._line_cache_lock:
            stale = [key for key in self._line_cache if key[0] == resolved_path]
            for key in stale:
                del self._line_cache[key]

    invalidate_file = invalidate

    def close(self) -> None:
        """Release LSP child + thread resources."""
        host = None
        with self._host_lock:
            if self._host is not None:
                host = self._host
                self._host = None
        if host is not None:
            try:
                host.close()
            except Exception:
                pass

    def ensure_ready(
        self,
        *,
        install_missing: bool = False,
        languages: Sequence[str] | None = None,
    ) -> dict[str, bool]:
        """Check whether the chosen LSP backend is launchable.

        Phase 3.6: ``self._check_python_backend()`` looks for the
        ``basedpyright-langserver`` binary instead of probing for jedi.
        """
        targets = _readiness_targets(languages)
        if "python" in targets and self._py_available is None:
            self._py_available = self._check_python_backend()
        sandbox_available = self._transport is not None and self._sandbox_id
        if install_missing and sandbox_available:
            if "python" in targets and not self._py_available:
                self._py_available = self._install_python_backend()
        return {"python": self._py_available or False} if "python" in targets else {}

    def reset_backend_availability(self) -> None:
        """Forget cached backend readiness so the next probe can re-check."""
        self._py_available = None

    @property
    def telemetry(self) -> LspTelemetry:
        with self._counter_lock:
            return LspTelemetry(
                queries=self._telemetry.queries,
                errors=self._telemetry.errors,
                successes=self._telemetry.successes,
                cache_hits=self._telemetry.cache_hits,
            )

    @property
    def connected(self) -> bool:
        """Whether the chosen LSP backend is launchable."""
        status = self.ensure_ready(languages=("python",))
        return bool(status.get("python"))

    # -- Backend routing ------------------------------------------------------

    def _query_python(
        self,
        file_path: str,
        loader: Callable[[], _T],
        empty: _T,
    ) -> _T:
        self._record_query()
        if self._detect_language(file_path) != "python":
            return empty
        return loader()

    def _ensure_host(self) -> LspAsyncHost:
        with self._host_lock:
            if self._host is None:
                workspace = self._workspace_root or "/"
                self._host = LspAsyncHost(workspace_root=workspace)
            return self._host

    def _lsp_definitions(
        self, file_path: str, line: int, character: int
    ) -> list[SymbolInfo]:
        character = self._resolve_column(file_path, line, character)
        resolved = self._resolve_path(file_path)
        # LSP positions are 0-indexed; project conventions use 1-indexed lines
        # so convert here.
        lsp_line = max(line - 1, 0)
        host = self._ensure_host()
        try:
            return host.run(
                lambda c: c.find_definitions(resolved, lsp_line, character)
            )
        except LspChildUnavailable as exc:
            self._py_available = False
            raise RuntimeError(
                f"LSP backend unavailable: {exc}. "
                "Re-check the chosen backend's qualification on this sandbox image."
            ) from exc

    def _lsp_references(
        self, file_path: str, line: int, character: int
    ) -> list[ReferenceInfo]:
        character = self._resolve_column(file_path, line, character)
        resolved = self._resolve_path(file_path)
        lsp_line = max(line - 1, 0)
        host = self._ensure_host()
        try:
            return host.run(
                lambda c: c.find_references(resolved, lsp_line, character)
            )
        except LspChildUnavailable as exc:
            self._py_available = False
            raise RuntimeError(
                f"LSP backend unavailable: {exc}"
            ) from exc

    def _lsp_hover(
        self, file_path: str, line: int, character: int
    ) -> HoverResult | None:
        character = self._resolve_column(file_path, line, character)
        resolved = self._resolve_path(file_path)
        lsp_line = max(line - 1, 0)
        host = self._ensure_host()
        try:
            return host.run(
                lambda c: c.hover(resolved, lsp_line, character)
            )
        except LspChildUnavailable as exc:
            self._py_available = False
            raise RuntimeError(
                f"LSP backend unavailable: {exc}"
            ) from exc

    def _lsp_diagnostics(self, file_path: str) -> list[Diagnostic]:
        resolved = self._resolve_path(file_path)
        host = self._ensure_host()
        try:
            return host.run(lambda c: c.diagnostics(resolved))
        except LspChildUnavailable:
            # Preserve the SyntaxError contract when basedpyright isn't on PATH.
            self._py_available = False
            return _local_syntax_check_diagnostics(resolved, file_path)

    def did_change(self, file_path: str, content: str) -> None:
        resolved = self._resolve_path(file_path)
        host = self._ensure_host()
        try:
            host.run(lambda c: c.did_change(resolved, content))
        except (LspChildUnavailable, LspChildCrashed):
            # Best-effort — caller is the cache invalidation path.
            return

    def __del__(self) -> None:  # pragma: no cover - GC safety net
        try:
            self.close()
        except Exception:
            pass


def _local_syntax_check_diagnostics(
    resolved_path: str, requested_path: str
) -> list[Diagnostic]:
    """Local ``compile()`` SyntaxError fallback for callers without basedpyright."""
    try:
        content = Path(resolved_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return []
    try:
        compile(content, resolved_path, "exec")
    except SyntaxError as exc:
        line = exc.lineno or 0
        character = max((exc.offset or 1) - 1, 0)
        return [
            Diagnostic(
                file_path=requested_path,
                line=line,
                character=character,
                severity=DiagnosticSeverity.ERROR,
                message=str(exc.msg or "invalid syntax"),
                source="python",
            )
        ]
    except ValueError:
        return []
    return []
