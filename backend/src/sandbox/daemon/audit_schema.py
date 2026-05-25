"""Typed dataclass helpers for daemon audit emitters (Phase 1).

The full event-family catalog and lane assignments are documented inline at
the top of :mod:`sandbox.daemon.audit_buffer`. This module owns *typed*
construction helpers for the smoke emitters defined in Phase 1. Additional
section dataclasses (overlay_workspace, layer_stack, occ, isolated_workspace,
plugin, background_tool, tool_call) land additively in Phase 2 emitters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class DaemonSection:
    """Payload shape for ``daemon.*`` events."""

    boot_epoch_id: int | None = None
    pid: int | None = None
    pressure: float | None = None
    retained_events: int | None = None
    retained_bytes: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class OsResourceSection:
    """Payload shape for ``os_resource.sampled`` events."""

    sampled_at_monotonic_s: float
    rss_bytes: int | None = None
    cpu_user_s: float | None = None
    cpu_system_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def build_daemon_event(event_type: str, daemon: DaemonSection) -> dict[str, Any]:
    return {
        "type": event_type,
        "payload": {"daemon": daemon.as_dict()},
    }


def build_os_resource_event(os_resource: OsResourceSection) -> dict[str, Any]:
    return {
        "type": "os_resource.sampled",
        "payload": {"os_resource": os_resource.as_dict()},
    }


__all__ = [
    "DaemonSection",
    "OsResourceSection",
    "build_daemon_event",
    "build_os_resource_event",
]
