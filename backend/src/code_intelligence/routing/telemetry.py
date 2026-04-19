"""Status and telemetry shaping for code intelligence services."""

from __future__ import annotations

from typing import Any

from code_intelligence.types import CITelemetry


def build_status(
    *,
    sandbox_id: str,
    workspace_root: str,
    initialized: bool,
    symbol_index: Any,
    arbiter: Any,
    tree_cache: Any,
    lsp_client: Any,
    rename_cache_stats: dict[str, int],
    rename_preview_fast_fallbacks: int,
) -> dict[str, Any]:
    """Return service status summary."""
    lsp = lsp_telemetry_fields(lsp_client)
    return {
        "sandbox_id": sandbox_id,
        "initialized": initialized,
        "workspace_root": workspace_root,
        "symbol_index": {
            "built": symbol_index.is_built,
            "files": symbol_index.indexed_files,
            "symbols": symbol_index.size,
            "generation": symbol_index.generation,
        },
        "arbiter": arbiter.status(),
        "edit_buffer": {
            "entries": arbiter.metrics.total_edits,
            "generation": arbiter.generation,
        },
        "tree_cache": tree_cache.stats,
        "rename_preview_cache": rename_cache_stats,
        "rename_preview_fast_fallbacks": rename_preview_fast_fallbacks,
        "lsp": lsp,
    }


def build_telemetry(*, symbol_index: Any, arbiter: Any, lsp_client: Any) -> CITelemetry:
    lsp = lsp_telemetry_fields(lsp_client)
    return CITelemetry(
        symbol_index_size=symbol_index.size,
        symbol_index_generation=symbol_index.generation,
        indexed_files=symbol_index.indexed_files,
        lsp_connected=lsp["connected"],
        lsp_query_count=lsp["queries"],
        lsp_cache_hits=lsp["cache_hits"],
        arbiter_active_locks=arbiter.active_lock_count,
        total_edits=arbiter.metrics.total_edits,
    )


def lsp_telemetry_fields(lsp_client: Any) -> dict[str, Any]:
    tel = lsp_client.telemetry
    worker_status = lsp_client.worker_status()
    return {
        "connected": lsp_client.connected,
        "queries": tel.queries,
        "successes": tel.successes,
        "errors": tel.errors,
        "cache_hits": tel.cache_hits,
        "script_runs": tel.script_runs,
        "script_successes": tel.script_successes,
        "script_errors": tel.script_errors,
        "worker_successes": tel.worker_successes,
        "worker_fallbacks": tel.worker_fallbacks,
        "worker_errors": tel.worker_errors,
        "worker_active": worker_status.get("active", False),
        "worker_enabled": worker_status.get("enabled", False),
        "worker_transport": worker_status.get("transport"),
        "worker_pid": worker_status.get("pid"),
        "worker_pid_path": worker_status.get("pid_path"),
        "worker_socket_path": worker_status.get("socket_path"),
        "worker_log_path": worker_status.get("log_path"),
        "worker_stdio_fallback": worker_status.get("stdio_fallback", False),
    }
