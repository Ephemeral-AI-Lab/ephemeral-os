"""Compatibility shim for unguarded sandbox command execution."""

from __future__ import annotations

from sandbox.api._impl.raw_exec import raw_exec

__all__ = ["raw_exec"]
