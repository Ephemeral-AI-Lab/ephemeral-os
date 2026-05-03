"""Transport-backed runtime service backend."""

from __future__ import annotations

from sandbox.runtime.command_client import RuntimeCommandClient


class DaemonBackend(RuntimeCommandClient):
    """Backend for runtime-owned mutation and status commands."""


__all__ = ["DaemonBackend"]
