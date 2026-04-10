"""CodeIntelligenceService — per-sandbox orchestrator.

Manages all code intelligence primitives (TreeCache, SymbolIndex,
Arbiter, Ledger, TimeMachine, Patcher, LspClient, QueryRouter) in a
single sandbox. Thread-safe with per-sandbox creation locks.
"""

from __future__ import annotations

import json
import inspect
import hashlib
import logging
import threading
import time
from typing import Any

from code_intelligence.atlas.service import AtlasService
from code_intelligence.editing.arbiter import Arbiter
from code_intelligence.routing.backend_protocol import (
    LspBackendAdapter,
    SymbolIndexBackendAdapter,
)
from code_intelligence.editing.ledger import Ledger
from code_intelligence.lsp.client import LspClient
from code_intelligence.editing.patcher import Patcher
from code_intelligence.routing.query_router import IntelligenceQueryRouter
from code_intelligence.analysis.symbol_index import SymbolIndex
from code_intelligence.editing.time_machine import TimeMachine
from code_intelligence.analysis.tree_cache import TreeCache
from code_intelligence.types import (
    CITelemetry,
    Diagnostic,
    EditRequest,
    EditResult,
    HoverResult,
    PreparedWrite,
    ReferenceInfo,
    SymbolInfo,
    WriteRequest,
)

logger = logging.getLogger(__name__)
_DEFAULT_SCOPE_RECENT_SECONDS = 300.0


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _normalize_scope_paths(paths: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths or ():
        if not isinstance(raw, str):
            continue
        for part in raw.split("|"):
            cleaned = part.strip().replace("\\", "/").removeprefix("./").rstrip("/")
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            out.append(cleaned)
    out.sort()
    return out


def _paths_overlap(path_a: str, path_b: str) -> bool:
    left = (path_a or "").strip().rstrip("/")
    right = (path_b or "").strip().rstrip("/")
    if not left or not right:
        return False
    if left == right:
        return True
    if left.startswith(right + "/") or right.startswith(left + "/"):
        return True
    return (
        left.endswith("/" + right)
        or right.endswith("/" + left)
        or ("/" + right + "/") in (left + "/")
        or ("/" + left + "/") in (right + "/")
    )


def _stable_briefing_versions(value: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "scope": str(item.get("scope") or ""),
                "snapshot_time": float(item.get("snapshot_time") or 0.0),
                "run_id": str(item.get("run_id") or ""),
            }
        )
    out.sort(key=lambda entry: entry["scope"])
    return out


def _scope_coherence_token(packet: dict[str, Any]) -> str:
    stable = {
        "scope_paths": packet.get("scope_paths") or [],
        "briefing_versions": packet.get("briefing_versions") or [],
        "ledger_generation": packet.get("ledger_generation") or 0,
        "arbiter_generation": packet.get("arbiter_generation") or 0,
        "symbol_index_generation": packet.get("symbol_index_generation") or 0,
        "recent_changes": packet.get("recent_changes") or [],
        "active_reservations": packet.get("active_reservations") or [],
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _same_scope(current: dict[str, Any], baseline_packet: dict[str, Any] | None) -> bool:
    if not isinstance(baseline_packet, dict):
        return False
    return _normalize_scope_paths(current.get("scope_paths") or []) == _normalize_scope_paths(
        baseline_packet.get("scope_paths") or []
    )


def _scope_freshness(current: dict[str, Any], baseline_packet: dict[str, Any] | None) -> str:
    if _same_scope(current, baseline_packet):
        if str(current.get("coherence_token") or "") == str(baseline_packet.get("coherence_token") or ""):
            return "fresh"
        if current.get("active_reservations") or current.get("recent_changes"):
            return "touched"
        return "stale"
    if current.get("active_reservations") or current.get("recent_changes"):
        return "touched"
    return "fresh"


def _scope_admission(packet: dict[str, Any]) -> dict[str, Any]:
    reservations = list(packet.get("active_reservations") or [])
    recent_changes = list(packet.get("recent_changes") or [])
    hotspots = list(packet.get("hotspots") or [])
    hotspot_max = max((int(item.get("edit_count") or 0) for item in hotspots), default=0)
    change_count = len(recent_changes)
    reasons: list[str] = []

    if reservations:
        mode = "serialize"
        contention = "high"
        recommended_parallel_scouts = 1
        reasons.append("active write reservations overlap this scope")
    elif hotspot_max >= 4 or change_count >= 6:
        mode = "serialize"
        contention = "high"
        recommended_parallel_scouts = 1
        reasons.append("scope is in a high-churn hotspot window")
    elif hotspot_max >= 2 or change_count >= 2:
        mode = "cautious"
        contention = "medium"
        recommended_parallel_scouts = 2
        reasons.append("scope changed recently; keep scout fanout narrow and disjoint")
    else:
        mode = "parallel"
        contention = "low"
        recommended_parallel_scouts = 3
        reasons.append("scope is stable enough for disjoint scout fanout")

    return {
        "mode": mode,
        "contention": contention,
        "recommended_parallel_scouts": recommended_parallel_scouts,
        "allow_parallel_fanout": recommended_parallel_scouts > 1,
        "active_reservation_count": len(reservations),
        "recent_change_count": change_count,
        "hotspot_max_edit_count": hotspot_max,
        "reasons": reasons,
    }


def _rebind_service_sandbox(service: CodeIntelligenceService, sandbox: Any) -> None:
    """Refresh the sandbox handle carried by a cached CI service."""
    if sandbox is None:
        return
    service._sandbox = sandbox
    lsp = getattr(service, "lsp_client", None)
    if lsp is not None:
        lsp._sandbox = sandbox


class CodeIntelligenceService:
    """Per-sandbox code intelligence runtime.

    Orchestrates all CI primitives and exposes a unified query/edit API.

    Parameters
    ----------
    sandbox_id:
        The sandbox this service is bound to.
    workspace_root:
        Root directory for indexing and path validation.
    sandbox:
        Optional Daytona sandbox object for remote operations.
    """

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        sandbox: Any = None,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._sandbox = sandbox
        self._initialized = False
        self._init_lock = threading.Lock()

        # Core components
        self.tree_cache = TreeCache(
            on_change=self._on_tree_change,
        )
        self.symbol_index = SymbolIndex(
            workspace_root=workspace_root,
        )
        self.arbiter = Arbiter(
            workspace_root=workspace_root,
        )
        self.ledger = Ledger()
        self.time_machine = TimeMachine()
        self.patcher = Patcher()
        self.lsp_client = LspClient(
            workspace_root=workspace_root,
            sandbox=sandbox,
        )
        self.atlas = AtlasService(
            workspace_root=workspace_root,
            ledger=self.ledger,
            symbol_index=self.symbol_index,
        )

        # Query router with backend adapters
        self.query_router = IntelligenceQueryRouter()
        self.query_router.register(LspBackendAdapter(self.lsp_client))
        self.query_router.register(SymbolIndexBackendAdapter(self.symbol_index))

    # -- Initialization -------------------------------------------------------

    def ensure_initialized(self, wait: bool = True) -> bool:
        """Initialize symbol indexing. Returns True if ready."""
        with self._init_lock:
            if self._initialized:
                return True

        ready = self.symbol_index.ensure_built(wait=wait)
        self.lsp_client.ensure_ready()

        with self._init_lock:
            self._initialized = ready
        return ready

    @property
    def is_initialized(self) -> bool:
        with self._init_lock:
            return self._initialized

    # -- Query API ------------------------------------------------------------

    def find_definitions(
        self, file_path: str, symbol: str, line: int = 0, character: int = 0,
    ) -> list[SymbolInfo]:
        """Find symbol definitions."""
        return self.query_router.find_definitions(file_path, symbol, line, character)

    def find_references(
        self, file_path: str, symbol: str, line: int = 0, character: int = 0,
    ) -> list[ReferenceInfo]:
        """Find all references to a symbol."""
        return self.query_router.find_references(file_path, symbol, line, character)

    def hover(self, file_path: str, line: int, character: int) -> HoverResult | None:
        """Get hover information."""
        return self.query_router.hover(file_path, line, character)

    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        """Get diagnostics for a file."""
        return self.query_router.diagnostics(file_path)

    def query_symbols(self, query: str) -> list[SymbolInfo]:
        """Search for symbols by name."""
        return self.symbol_index.find(query)

    # -- Edit API -------------------------------------------------------------

    def apply_edit(self, request: EditRequest) -> EditResult:
        """Apply an OCC-coordinated edit.

        1. Acquire per-file lock
        2. Validate edit token (if provided)
        3. Save snapshot for undo
        4. Apply edit via patcher
        5. Record in ledger
        6. Refresh symbol index
        7. Release lock
        """
        prepared = self.prepare_write(
            request.file_path,
            agent_id=request.agent_id,
            expected_hash=request.expected_hash,
        )
        if isinstance(prepared, EditResult):
            return prepared
        try:
            from code_intelligence.editing.patcher import SearchReplaceEdit

            patch_result = self.patcher.apply_edits(
                prepared.current_content,
                [SearchReplaceEdit(old_text=request.old_text, new_text=request.new_text)],
            )
            if not patch_result.success:
                self.time_machine.discard_snapshot(request.file_path)
                return EditResult(
                    success=False,
                    file_path=request.file_path,
                    message="; ".join(patch_result.errors),
                )
            return self.commit_prepared_write(
                prepared,
                patch_result.content,
                edit_type="edit",
                description=request.description,
                message=f"Applied {patch_result.edits_applied} edit(s)",
            )
        finally:
            self.abort_prepared_write(prepared)

    def apply_write(self, request: WriteRequest) -> EditResult:
        """Apply an OCC-coordinated full-file write."""
        prepared = self.prepare_write(
            request.file_path,
            agent_id=request.agent_id,
            expected_hash=request.expected_hash,
            allow_missing=True,
        )
        if isinstance(prepared, EditResult):
            return prepared
        try:
            return self.commit_prepared_write(
                prepared,
                request.content,
                edit_type=request.edit_type,
                description=request.description,
                message="Wrote file",
            )
        finally:
            self.abort_prepared_write(prepared)

    def prepare_write(
        self,
        file_path: str,
        *,
        agent_id: str = "",
        expected_hash: str = "",
        allow_missing: bool = False,
    ) -> PreparedWrite | EditResult:
        """Reserve *file_path* for writing and capture a stable read snapshot."""
        if not self.arbiter.acquire_file_lock(file_path):
            return EditResult(
                success=False,
                file_path=file_path,
                message="Could not acquire file lock (timeout)",
                conflict=True,
            )

        try:
            current, existed = self._read_content(file_path, allow_missing=allow_missing)
        except Exception as exc:
            self.arbiter.release_file_lock(file_path)
            return EditResult(
                success=False,
                file_path=file_path,
                message=f"Cannot read file: {exc}",
            )

        current_hash = _content_hash(current)
        if expected_hash and current_hash != expected_hash:
            self.arbiter.release_file_lock(file_path)
            return EditResult(
                success=False,
                file_path=file_path,
                message=(
                    "Write precheck failed: file content changed since it was read. "
                    "Re-read the file and retry."
                ),
                conflict=True,
            )

        token = self.arbiter.issue_token(file_path, current_hash, agent_id)
        return PreparedWrite(
            file_path=file_path,
            token_id=token.token_id,
            current_content=current,
            current_hash=current_hash,
            agent_id=agent_id,
            existed=existed,
        )

    def commit_prepared_write(
        self,
        prepared: PreparedWrite,
        new_content: str,
        *,
        edit_type: str,
        description: str = "",
        message: str = "Wrote file",
    ) -> EditResult:
        """Commit a prepared write after validating the reservation is still current."""
        ok, reason = self.arbiter.validate_token(
            prepared.token_id,
            file_path=prepared.file_path,
            content_hash=prepared.current_hash,
        )
        if not ok:
            return EditResult(
                success=False,
                file_path=prepared.file_path,
                message=f"Write precheck failed: {reason}",
                conflict=True,
            )

        try:
            current_now, _ = self._read_content(prepared.file_path, allow_missing=True)
        except Exception as exc:
            return EditResult(
                success=False,
                file_path=prepared.file_path,
                message=f"Cannot re-read file before commit: {exc}",
            )
        if _content_hash(current_now) != prepared.current_hash:
            return EditResult(
                success=False,
                file_path=prepared.file_path,
                message=(
                    "Write precheck failed: file content changed before commit. "
                    "Re-read the file and retry."
                ),
                conflict=True,
            )

        self.time_machine.save(prepared.file_path, current_now)
        try:
            self._write_content(prepared.file_path, new_content)
        except Exception as exc:
            return EditResult(
                success=False,
                file_path=prepared.file_path,
                message=f"Write failed: {exc}",
            )

        old_hash = prepared.current_hash
        new_hash = _content_hash(new_content)
        self.ledger.record(
            file_path=prepared.file_path,
            agent_id=prepared.agent_id,
            edit_type=edit_type,
            old_hash=old_hash,
            new_hash=new_hash,
            description=description,
        )
        gen = self.arbiter.record_edit(prepared.file_path, prepared.agent_id)
        self.tree_cache.put_content(prepared.file_path, new_content)
        self.symbol_index.refresh(prepared.file_path, new_content)
        self.lsp_client.invalidate(prepared.file_path)
        self.arbiter.release_token(prepared.token_id)
        self.arbiter.release_file_lock(prepared.file_path)
        return EditResult(
            success=True,
            file_path=prepared.file_path,
            message=message,
            snapshot_id=str(gen),
        )

    def abort_prepared_write(self, prepared: PreparedWrite) -> None:
        """Release any reservation still held for *prepared*."""
        ok, _ = self.arbiter.validate_token(
            prepared.token_id,
            file_path=prepared.file_path,
        )
        if ok:
            self.arbiter.release_token(prepared.token_id)
            self.arbiter.release_file_lock(prepared.file_path)

    def undo_last_edit(self, file_path: str) -> EditResult:
        """Undo the last edit to a file via TimeMachine."""
        snapshot = self.time_machine.rollback(file_path)
        if snapshot is None:
            return EditResult(
                success=False,
                file_path=file_path,
                message="No snapshot available for undo",
            )

        try:
            self._write_content(file_path, snapshot.content)
        except Exception as exc:
            return EditResult(
                success=False,
                file_path=file_path,
                message=f"Undo write failed: {exc}",
            )

        # Refresh caches
        self.tree_cache.put_content(file_path, snapshot.content)
        self.symbol_index.refresh(file_path, snapshot.content)
        self.lsp_client.invalidate(file_path)

        return EditResult(
            success=True,
            file_path=file_path,
            message="Reverted to previous snapshot",
        )

    def scope_status(
        self,
        scope_paths: list[str] | tuple[str, ...] | None,
        *,
        briefing_versions: list[dict[str, Any]] | None = None,
        baseline_packet: dict[str, Any] | None = None,
        recent_seconds: float = _DEFAULT_SCOPE_RECENT_SECONDS,
    ) -> dict[str, Any]:
        """Return the authoritative live coordination snapshot for *scope_paths*."""
        normalized = _normalize_scope_paths(scope_paths)
        recent_changes: list[dict[str, Any]] = []
        for entry in self.ledger.recent_entries(recent_seconds):
            file_path = str(getattr(entry, "file_path", "") or "")
            if normalized and not any(_paths_overlap(file_path, scope) for scope in normalized):
                continue
            recent_changes.append(
                {
                    "file_path": file_path,
                    "agent_id": str(getattr(entry, "agent_id", "") or ""),
                    "timestamp": float(getattr(entry, "timestamp", 0.0) or 0.0),
                    "edit_type": str(getattr(entry, "edit_type", "") or ""),
                }
            )
        recent_changes.sort(key=lambda item: (item["file_path"], item["timestamp"]))

        active_reservations = self.arbiter.active_reservations(normalized)
        hotspots = [
            {"file_path": str(file_path), "edit_count": int(count)}
            for file_path, count in self.arbiter.hotspots(limit=25)
            if not normalized or any(_paths_overlap(str(file_path), scope) for scope in normalized)
        ][:10]

        packet = {
            "scope_paths": normalized,
            "briefing_versions": _stable_briefing_versions(briefing_versions),
            "ledger_generation": self.ledger.generation,
            "arbiter_generation": self.arbiter.generation,
            "symbol_index_generation": self.symbol_index.generation,
            "recent_changes": recent_changes[:25],
            "active_reservations": [dict(item) for item in active_reservations][:25],
            "hotspots": hotspots,
            "generated_at": time.time(),
        }
        packet["coherence_token"] = _scope_coherence_token(packet)
        packet["freshness"] = _scope_freshness(packet, baseline_packet)
        if isinstance(baseline_packet, dict):
            packet["baseline_coherence_token"] = str(baseline_packet.get("coherence_token") or "")
        packet["admission"] = _scope_admission(packet)
        return packet

    # -- Telemetry ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return service status summary."""
        lsp_tel = self.lsp_client.telemetry
        return {
            "sandbox_id": self.sandbox_id,
            "initialized": self.is_initialized,
            "workspace_root": self.workspace_root,
            "tree_cache": self.tree_cache.stats,
            "symbol_index": {
                "built": self.symbol_index.is_built,
                "files": self.symbol_index.indexed_files,
                "symbols": self.symbol_index.size,
                "generation": self.symbol_index.generation,
            },
            "arbiter": self.arbiter.status(),
            "ledger": {
                "entries": self.ledger.entry_count,
                "generation": self.ledger.generation,
            },
            "atlas": self.atlas.status(),
            "lsp": {
                "connected": self.lsp_client.connected,
                "queries": lsp_tel.queries,
                "cache_hits": lsp_tel.cache_hits,
            },
        }

    def get_telemetry(self) -> CITelemetry:
        """Return structured telemetry."""
        cache_stats = self.tree_cache.stats
        lsp_tel = self.lsp_client.telemetry
        return CITelemetry(
            tree_cache_size=cache_stats["size"],
            tree_cache_hits=cache_stats["hits"],
            tree_cache_misses=cache_stats["misses"],
            symbol_index_size=self.symbol_index.size,
            symbol_index_generation=self.symbol_index.generation,
            indexed_files=self.symbol_index.indexed_files,
            lsp_connected=self.lsp_client.connected,
            lsp_query_count=lsp_tel.queries,
            lsp_cache_hits=lsp_tel.cache_hits,
            arbiter_active_edits=self.arbiter.active_edit_count,
            ledger_entry_count=self.ledger.entry_count,
        )

    # -- Cleanup --------------------------------------------------------------

    def dispose(self) -> None:
        """Cleanup all resources."""
        self.tree_cache.invalidate_all()
        self.arbiter.cleanup_locks()
        self.time_machine.clear()
        logger.info("CodeIntelligenceService disposed for sandbox %s", self.sandbox_id)

    # -- Callbacks ------------------------------------------------------------

    def _on_tree_change(self, file_path: str, old_hash: str, new_hash: str) -> None:
        """Called when tree cache detects a file change."""
        self.query_router.register_file_change(file_path)

    def _write_content(self, file_path: str, content: str) -> None:
        """Write content locally or to the attached sandbox."""
        from pathlib import Path

        if self._sandbox:
            result = self._sandbox.fs.upload_file(
                content.encode("utf-8"),
                file_path,
            )
            self._resolve(result)
            return
        Path(file_path).write_text(content, encoding="utf-8")

    def _read_content(
        self,
        file_path: str,
        *,
        allow_missing: bool = False,
    ) -> tuple[str, bool]:
        """Read content locally or from the attached sandbox."""
        from pathlib import Path

        if self._sandbox:
            try:
                raw = self._resolve(self._sandbox.fs.download_file(file_path))
            except Exception as exc:
                if allow_missing and self._is_missing_error(exc):
                    return "", False
                raise
            if isinstance(raw, bytes):
                return raw.decode("utf-8"), True
            return str(raw), True

        path = Path(file_path)
        if not path.exists():
            if allow_missing:
                return "", False
            raise FileNotFoundError(file_path)
        return path.read_text(encoding="utf-8"), True

    @staticmethod
    def _is_missing_error(exc: Exception) -> bool:
        text = str(exc).lower()
        if isinstance(exc, FileNotFoundError):
            return True
        return "not found" in text or "no such file" in text or "does not exist" in text

    @staticmethod
    def _resolve(result: Any) -> Any:
        """If *result* is awaitable, run it synchronously."""
        import asyncio
        import concurrent.futures

        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, result).result()
            return asyncio.run(result)
        return result


# ---------------------------------------------------------------------------
# Global service registry — per-sandbox singleton management
# ---------------------------------------------------------------------------

_SERVICES: dict[str, CodeIntelligenceService] = {}
_SERVICES_LOCK = threading.Lock()
_CREATION_LOCKS: dict[str, threading.Lock] = {}


def get_code_intelligence(
    sandbox_id: str,
    workspace_root: str = "/workspace",
    sandbox: Any = None,
) -> CodeIntelligenceService:
    """Get or create a CI service for a sandbox."""
    existing: CodeIntelligenceService | None = None
    with _SERVICES_LOCK:
        existing = _SERVICES.get(sandbox_id)
        if existing is not None and existing.workspace_root == workspace_root:
            _rebind_service_sandbox(existing, sandbox)
            return existing
        if sandbox_id not in _CREATION_LOCKS:
            _CREATION_LOCKS[sandbox_id] = threading.Lock()
        creation_lock = _CREATION_LOCKS[sandbox_id]

    with creation_lock:
        # Double-check after acquiring creation lock
        with _SERVICES_LOCK:
            existing = _SERVICES.get(sandbox_id)
            if existing is not None and existing.workspace_root == workspace_root:
                _rebind_service_sandbox(existing, sandbox)
                return existing
            if existing is not None:
                _SERVICES.pop(sandbox_id, None)

        if existing is not None:
            existing.dispose()

        service = CodeIntelligenceService(
            sandbox_id=sandbox_id,
            workspace_root=workspace_root,
            sandbox=sandbox,
        )
        with _SERVICES_LOCK:
            _SERVICES[sandbox_id] = service

        return service


def get_code_intelligence_if_exists(sandbox_id: str) -> CodeIntelligenceService | None:
    """Fetch an existing CI service without creating one."""
    with _SERVICES_LOCK:
        return _SERVICES.get(sandbox_id)


def dispose_code_intelligence(sandbox_id: str) -> None:
    """Dispose and remove a CI service."""
    with _SERVICES_LOCK:
        service = _SERVICES.pop(sandbox_id, None)
    if service:
        service.dispose()


def dispose_all_code_intelligence() -> None:
    """Dispose all CI services."""
    with _SERVICES_LOCK:
        services = list(_SERVICES.values())
        _SERVICES.clear()
    for service in services:
        service.dispose()


def get_all_services_status() -> dict[str, dict]:
    """Return status for all active services."""
    with _SERVICES_LOCK:
        services = dict(_SERVICES)
    return {sid: svc.status() for sid, svc in services.items()}
