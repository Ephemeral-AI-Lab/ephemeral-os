"""Compatibility shim for the public sandbox file-read verb."""

from __future__ import annotations

from sandbox.api._impl.read import read_file

__all__ = ["read_file"]
