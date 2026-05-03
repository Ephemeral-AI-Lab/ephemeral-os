"""Compatibility re-export for OCC types during the sandbox API refactor."""

from __future__ import annotations

from sandbox.occ.types import (
    EditRequest,
    EditResult,
    EditSpec,
    OperationChange,
    OperationResult,
    OperationStatus,
    WriteSpec,
)

__all__ = [
    "EditRequest",
    "EditResult",
    "EditSpec",
    "OperationChange",
    "OperationResult",
    "OperationStatus",
    "WriteSpec",
]
