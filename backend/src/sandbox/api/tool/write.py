"""Compatibility shim for the public sandbox file-write verb."""

from __future__ import annotations

from sandbox.api._impl.write import write_file

__all__ = ["write_file"]
