"""OCC backend factory for daemon handlers and services.

This module owns the single OCC backend tuple consumed by every daemon peer
that needs layer-stack/OCC/gitignore state: ``handler/{edit,read,write}.py``
(api.write/edit/read), ``ephemeral_workspace/pipeline.py`` (api.shell), and
``handler/metrics.py`` (api.layer_metrics).
The factory uses a canonical ``workspace_ref=layer_stack_root`` only; this
module owns no path classification (single source of truth lives in
:mod:`sandbox.daemon.request_context`).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from sandbox.layer_stack.stack import LayerStack
from sandbox.occ.client import OccClient
from sandbox.occ.gitignore import SnapshotGitignoreOracle
from sandbox.occ.maintenance import AutoSquashMaintenancePolicy
from sandbox.occ.service import AUTO_SQUASH_MAX_DEPTH, OccService
from sandbox.occ.layer_stack_client import LayerStackClient
from sandbox.main_workspace.workspace_binding import RuntimeWorkspaceBindingReader
from sandbox.daemon.workspace_server import get_layer_stack_manager


@dataclass(frozen=True)
class OccBackend:
    """The OCC backend tuple shared by every runtime peer.

    Field names are the structural contract: ``handler.request_context``,
    ``ephemeral_workspace.pipeline``, and ``handler.metrics`` all read these
    attributes. A typo here silently breaks every consumer.
    """

    layer_stack: LayerStackClient
    occ_service: OccService
    occ_client: OccClient
    gitignore: SnapshotGitignoreOracle
    manager: LayerStack


_MAX_BACKEND_CACHE_ENTRIES = 256
_BACKEND_CACHE: OrderedDict[str, OccBackend] = OrderedDict()
_BACKEND_CACHE_LOCK = threading.RLock()


def build_occ_backend(layer_stack_root: str) -> OccBackend:
    """Return the cached OCC backend for ``layer_stack_root`` (constructing on miss)."""
    cache_key = _backend_cache_key(layer_stack_root)
    with _BACKEND_CACHE_LOCK:
        cached = _BACKEND_CACHE.get(cache_key)
        if cached is not None:
            _BACKEND_CACHE.move_to_end(cache_key)
            return cached
    manager = get_layer_stack_manager(cache_key)
    layer_stack = LayerStackClient(manager)
    gitignore = SnapshotGitignoreOracle(layer_stack)
    occ_service = OccService(
        gitignore=gitignore,
        layer_stack=layer_stack,
        maintenance=AutoSquashMaintenancePolicy(
            snapshot_reader=layer_stack,
            squasher=layer_stack,
            max_depth=AUTO_SQUASH_MAX_DEPTH,
        ),
    )
    occ_client = OccClient(
        occ_service,
        binding_reader=RuntimeWorkspaceBindingReader(),
        workspace_ref=cache_key,
    )
    backend = OccBackend(
        layer_stack=layer_stack,
        occ_service=occ_service,
        occ_client=occ_client,
        gitignore=gitignore,
        manager=manager,
    )
    close_backend: OccBackend | None = None
    evicted: tuple[OccBackend, ...] = ()
    with _BACKEND_CACHE_LOCK:
        existing = _BACKEND_CACHE.get(cache_key)
        if existing is not None:
            _BACKEND_CACHE.move_to_end(cache_key)
            close_backend = backend
            backend = existing
        else:
            _BACKEND_CACHE[cache_key] = backend
            evicted = _pop_oldest_backends_locked()
    if close_backend is not None:
        _close_backend(close_backend)
    for evicted_backend in evicted:
        _close_backend(evicted_backend)
    return backend


def drop_backend_cache(layer_stack_root: str) -> None:
    """Drop cached OCC backend for one layer-stack root."""
    root = str(layer_stack_root or "").strip()
    if not root:
        return
    with _BACKEND_CACHE_LOCK:
        backend = _BACKEND_CACHE.pop(str(Path(root).resolve(strict=False)), None)
    if backend is not None:
        _close_backend(backend)


def clear_backend_cache() -> None:
    """Drop every cached OCC backend. Test helper."""
    with _BACKEND_CACHE_LOCK:
        backends = tuple(_BACKEND_CACHE.values())
        _BACKEND_CACHE.clear()
    for backend in backends:
        _close_backend(backend)


def _backend_cache_key(layer_stack_root: str | Path) -> str:
    raw = str(layer_stack_root or "").strip()
    if not raw:
        raise ValueError("layer_stack_root is required")
    return str(Path(raw).resolve(strict=False))


def _pop_oldest_backends_locked() -> tuple[OccBackend, ...]:
    """Caller must hold ``_BACKEND_CACHE_LOCK``."""
    evicted: list[OccBackend] = []
    while len(_BACKEND_CACHE) > _MAX_BACKEND_CACHE_ENTRIES:
        _, backend = _BACKEND_CACHE.popitem(last=False)
        evicted.append(backend)
    return tuple(evicted)


def _close_backend(backend: OccBackend) -> None:
    close = getattr(backend.occ_service, "close", None)
    if callable(close):
        close()


__all__ = [
    "OccBackend",
    "build_occ_backend",
    "clear_backend_cache",
    "drop_backend_cache",
]
