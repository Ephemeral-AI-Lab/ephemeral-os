"""Daemon-local OCC service cache.

This module owns the single OCC service bundle consumed by every daemon peer
that needs layer-stack/OCC/gitignore state: built-in operations, workspace
tool dispatch, and the ephemeral workspace pipeline.
The factory uses a canonical ``workspace_ref=layer_stack_root`` only; this
module owns no path classification (single source of truth lives in
:mod:`sandbox.daemon.workspace_tool.payloads`).
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
from sandbox.occ.layer_stack_adapter import LayerStackPortAdapter
from sandbox.daemon.layer_stack_runtime import emit_squash_event, get_layer_stack_manager
from sandbox.daemon.workspace_binding_reader import LayerStackBindingReader


@dataclass(frozen=True)
class OccRuntimeServices:
    """The OCC service bundle shared by every daemon runtime peer.

    Field names are the structural contract: built-ins and workspace publish
    code all read these attributes. A typo here silently breaks every consumer.
    """

    layer_stack: LayerStackPortAdapter
    occ_service: OccService
    occ_client: OccClient
    gitignore: SnapshotGitignoreOracle
    layer_stack_manager: LayerStack


_OCC_RUNTIME_SERVICES_CACHE_MAX = 256
_RUNTIME_SERVICE_CACHE: OrderedDict[str, OccRuntimeServices] = OrderedDict()
_RUNTIME_SERVICE_CACHE_LOCK = threading.RLock()


def get_occ_runtime_services(layer_stack_root: str) -> OccRuntimeServices:
    """Return daemon-local OCC services for ``layer_stack_root``."""
    layer_stack_root_key = _runtime_service_cache_key(layer_stack_root)
    with _RUNTIME_SERVICE_CACHE_LOCK:
        cached = _RUNTIME_SERVICE_CACHE.get(layer_stack_root_key)
        if cached is not None:
            _RUNTIME_SERVICE_CACHE.move_to_end(layer_stack_root_key)
            return cached
    manager = get_layer_stack_manager(layer_stack_root_key)
    layer_stack = LayerStackPortAdapter(manager)
    gitignore = SnapshotGitignoreOracle(layer_stack)
    occ_service = OccService(
        gitignore=gitignore,
        layer_stack=layer_stack,
        maintenance=AutoSquashMaintenancePolicy(
            snapshot_reader=layer_stack,
            squasher=layer_stack,
            max_depth=AUTO_SQUASH_MAX_DEPTH,
            audit=emit_squash_event,
        ),
    )
    occ_client = OccClient(
        occ_service,
        binding_reader=LayerStackBindingReader(),
        workspace_ref=layer_stack_root_key,
    )
    services = OccRuntimeServices(
        layer_stack=layer_stack,
        occ_service=occ_service,
        occ_client=occ_client,
        gitignore=gitignore,
        layer_stack_manager=manager,
    )
    close_services: OccRuntimeServices | None = None
    evicted: tuple[OccRuntimeServices, ...] = ()
    with _RUNTIME_SERVICE_CACHE_LOCK:
        existing = _RUNTIME_SERVICE_CACHE.get(layer_stack_root_key)
        if existing is not None:
            _RUNTIME_SERVICE_CACHE.move_to_end(layer_stack_root_key)
            close_services = services
            services = existing
        else:
            _RUNTIME_SERVICE_CACHE[layer_stack_root_key] = services
            evicted = _evict_oldest_occ_services_locked()
    if close_services is not None:
        _close_runtime_services(close_services)
    for evicted_services in evicted:
        _close_runtime_services(evicted_services)
    return services


def drop_occ_runtime_services(layer_stack_root: str) -> None:
    """Drop cached OCC services for one layer-stack root."""
    root = str(layer_stack_root or "").strip()
    if not root:
        return
    with _RUNTIME_SERVICE_CACHE_LOCK:
        services = _RUNTIME_SERVICE_CACHE.pop(
            str(Path(root).resolve(strict=False)),
            None,
        )
    if services is not None:
        _close_runtime_services(services)


def clear_occ_runtime_services() -> None:
    """Drop every cached OCC service bundle. Test helper."""
    with _RUNTIME_SERVICE_CACHE_LOCK:
        service_bundles = tuple(_RUNTIME_SERVICE_CACHE.values())
        _RUNTIME_SERVICE_CACHE.clear()
    for services in service_bundles:
        _close_runtime_services(services)


def _runtime_service_cache_key(layer_stack_root: str | Path) -> str:
    raw = str(layer_stack_root or "").strip()
    if not raw:
        raise ValueError("layer_stack_root is required")
    return str(Path(raw).resolve(strict=False))


def _evict_oldest_occ_services_locked() -> tuple[OccRuntimeServices, ...]:
    """Evict oldest entries until cache is within bounds.

    Caller must hold ``_RUNTIME_SERVICE_CACHE_LOCK``.
    """
    evicted: list[OccRuntimeServices] = []
    while len(_RUNTIME_SERVICE_CACHE) > _OCC_RUNTIME_SERVICES_CACHE_MAX:
        _, services = _RUNTIME_SERVICE_CACHE.popitem(last=False)
        evicted.append(services)
    return tuple(evicted)


def _close_runtime_services(services: OccRuntimeServices) -> None:
    close = getattr(services.occ_service, "close", None)
    if callable(close):
        close()


__all__ = [
    "OccRuntimeServices",
    "clear_occ_runtime_services",
    "drop_occ_runtime_services",
    "get_occ_runtime_services",
]
