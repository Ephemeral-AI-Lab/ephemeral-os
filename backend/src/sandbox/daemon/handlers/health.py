"""Runtime readiness probe for the resident sandbox daemon."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from sandbox.command_exec import workspace_mount
from sandbox.layer_stack.manifest import (
    manifest_path,
    read_manifest,
)
from sandbox.layer_stack.workspace import require_workspace_binding
from sandbox.daemon.handlers import _common
from sandbox.daemon.services import occ_backend, shell_runner
from sandbox.daemon.services.workspace_server import get_layer_stack_manager

_STARTED_AT_MONO = time.monotonic()


def runtime_ready(args: dict[str, object]) -> dict[str, object]:
    """Return binary daemon readiness plus per-plane probe details."""
    total_start = time.perf_counter()
    layer_stack_root = _layer_stack_root(args)
    timings: dict[str, float] = {}
    probes = [
        _run_probe(
            "control_plane",
            lambda: _probe_control_plane(layer_stack_root),
            timings=timings,
        ),
        _run_probe(
            "data_plane",
            lambda: _probe_data_plane(layer_stack_root),
            timings=timings,
        ),
        _run_probe(
            "mutation_gate",
            lambda: _probe_mutation_gate(layer_stack_root),
            timings=timings,
        ),
    ]
    return {
        "success": True,
        "ready": all(probe["status"] == "ok" for probe in probes),
        "probes": probes,
        "daemon_pid": os.getpid(),
        "uptime_s": max(0.0, time.monotonic() - _STARTED_AT_MONO),
        "timings": {
            **timings,
            "runtime.ready.total_s": time.perf_counter() - total_start,
        },
    }


def _probe_control_plane(layer_stack_root: str) -> dict[str, object]:
    binding = require_workspace_binding(layer_stack_root)
    manager = get_layer_stack_manager(layer_stack_root)
    manifest = read_manifest(manifest_path(layer_stack_root))
    # Also exercise the manager API; this catches a broken manager cache even
    # when the manifest file itself can be read directly.
    manager_manifest = manager.read_active_manifest()
    if manager_manifest.version != manifest.version:
        raise RuntimeError(
            "manager manifest version does not match active manifest file"
        )
    return {
        "workspace_root": binding.workspace_root,
        "manifest_version": manifest.version,
        "manifest_depth": manifest.depth,
        "base_root_hash": binding.base_root_hash,
    }


def _probe_data_plane(layer_stack_root: str) -> dict[str, object]:
    handlers_backend = _common._services(layer_stack_root)
    shell_services = shell_runner._services(
        {"layer_stack_root": layer_stack_root}
    )
    expected_fields = (
        "layer_stack",
        "occ_client",
        "gitignore",
        "manager",
    )
    missing_fields = [
        field for field in expected_fields if not hasattr(handlers_backend, field)
    ]
    if missing_fields:
        raise RuntimeError(
            "handler services missing fields: " + ", ".join(missing_fields)
        )
    if len(shell_services) != 4:
        raise RuntimeError(
            f"shell services returned {len(shell_services)} entries; expected 4"
        )
    mount_mode = (
        "private_namespace"
        if workspace_mount._private_mount_namespace_available()
        else "copy_backed"
    )
    return {
        "handlers_services_ready": True,
        "shell_services_ready": True,
        "workspace_mount_mode": mount_mode,
    }


def _probe_mutation_gate(layer_stack_root: str) -> dict[str, object]:
    backend = occ_backend.build_occ_backend(layer_stack_root)
    expected_fields = (
        "layer_stack",
        "occ_client",
        "gitignore",
        "manager",
    )
    present_fields = [field for field in expected_fields if hasattr(backend, field)]
    missing_fields = sorted(set(expected_fields) - set(present_fields))
    if missing_fields:
        raise RuntimeError("OCC backend missing fields: " + ", ".join(missing_fields))
    return {
        "backend_ready": True,
        "backend_fields": present_fields,
        "occ_client_class": type(getattr(backend, "occ_client", None)).__name__,
    }


def _run_probe(
    name: str,
    probe: Callable[[], dict[str, object]],
    *,
    timings: dict[str, float],
) -> dict[str, object]:
    start = time.perf_counter()
    try:
        details = probe()
        status = "ok"
    except Exception as exc:
        status = "down"
        details = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    timings[f"runtime.ready.{name}_s"] = time.perf_counter() - start
    return {
        "name": name,
        "status": status,
        "details": details,
    }


def _layer_stack_root(args: dict[str, object]) -> str:
    layer_stack_root = str(args.get("layer_stack_root") or "").strip()
    if not layer_stack_root:
        raise ValueError("layer_stack_root is required")
    return layer_stack_root


__all__ = ["runtime_ready"]
