"""Compatibility import path for the temporary legacy runtime command client."""

from __future__ import annotations

from sandbox.runtime.legacy_command_client import (
    DaemonCommandClient,
    DaemonCommandError,
)

__all__ = ["DaemonCommandClient", "DaemonCommandError"]
