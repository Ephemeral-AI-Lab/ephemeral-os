"""occ-server logical module — OCC mutation gate composition.

Phase 05 establishes occ-server as the internal mutation gate consumed
through :class:`OCCClient.apply_changeset`. It is not a host-callable runtime
dispatch surface; public data operations reach it through the handler-owned
``OCCClient`` instance.

Phase 05.5 adds a single OCC backend factory consumed by every runtime
peer that needs the layer-stack/OCC/gitignore tuple — handlers/_common.py
(api.write/edit/read), command_exec_server (api.shell), and
handlers/metrics_handler.py (api.layer_metrics).
The factory uses a canonical ``workspace_ref=layer_stack_root`` only; this module
owns no path classification (single source of truth lives on command-exec
via :mod:`sandbox.runtime.handlers._common`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sandbox.layer_stack.stack_manager import LayerStackManager
from sandbox.occ.client import OCCClient
from sandbox.occ.content.gitignore_oracle import SnapshotGitignoreOracle
from sandbox.occ.service import OccService
from sandbox.runtime.clients.layer_stack import LayerStackClient
from sandbox.runtime.clients.occ import RuntimeWorkspaceBindingReader
from sandbox.runtime.layer_stack_server import get_layer_stack_manager


@dataclass(frozen=True)
class OccBackend:
    """The OCC backend tuple shared by every runtime peer.

    Field names are the structural contract: ``handlers._common``,
    ``command_exec_server``, and ``metrics_handler`` all read these
    attributes. A typo here silently breaks every consumer.
    """

    layer_stack: LayerStackClient
    occ_client: OCCClient
    gitignore: SnapshotGitignoreOracle
    single_path_gitignore: SnapshotGitignoreOracle
    manager: LayerStackManager


_BACKEND_CACHE: dict[str, OccBackend] = {}


def build_occ_backend(layer_stack_root: str) -> OccBackend:
    """Return the cached OCC backend for ``layer_stack_root`` (constructing on miss)."""
    cache_key = _backend_cache_key(layer_stack_root)
    cached = _BACKEND_CACHE.get(cache_key)
    if cached is not None:
        return cached
    manager = get_layer_stack_manager(cache_key)
    layer_stack = LayerStackClient(manager)
    gitignore = SnapshotGitignoreOracle(layer_stack)
    single_path_gitignore = SnapshotGitignoreOracle(layer_stack, backend="pathspec")
    occ_service = OccService(gitignore=gitignore, layer_stack=layer_stack)
    occ_client = OCCClient(
        occ_service,
        binding_reader=RuntimeWorkspaceBindingReader(),
        workspace_ref=cache_key,
    )
    backend = OccBackend(
        layer_stack=layer_stack,
        occ_client=occ_client,
        gitignore=gitignore,
        single_path_gitignore=single_path_gitignore,
        manager=manager,
    )
    _BACKEND_CACHE[cache_key] = backend
    return backend


def drop_backend_cache(layer_stack_root: str) -> None:
    """Drop cached OCC backend for one layer-stack root."""
    root = str(layer_stack_root or "").strip()
    if not root:
        return
    _BACKEND_CACHE.pop(root, None)
    _BACKEND_CACHE.pop(str(Path(root).resolve(strict=False)), None)


def _backend_cache_clear() -> None:
    """Drop every cached OCC backend. Test helper."""
    _BACKEND_CACHE.clear()


def _backend_cache_key(layer_stack_root: str | Path) -> str:
    raw = str(layer_stack_root or "").strip()
    if not raw:
        raise ValueError("layer_stack_root is required")
    return str(Path(raw).resolve(strict=False))


__all__ = [
    "OccBackend",
    "build_occ_backend",
    "drop_backend_cache",
]
