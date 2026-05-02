"""Provider-neutral exceptions for the sandbox API surface.

Tools and CI internals raise and catch these symbols so error handling
stays decoupled from any concrete sandbox provider.
"""

from __future__ import annotations


class SandboxApiError(Exception):
    """Base class for all sandbox API errors."""


class SandboxTransportError(SandboxApiError):
    """A transport-layer failure raised by ``SandboxTransport`` implementations."""


class SandboxTimeoutError(SandboxApiError):
    """Operation exceeded its timeout budget."""


class SandboxNotFoundError(SandboxApiError, FileNotFoundError):
    """Target path does not exist on the sandbox."""


class SandboxConflictError(SandboxApiError):
    """OCC base-mismatch or concurrent-write conflict.

    Carries the conflicting path and a short reason so callers can render
    user-facing diagnostics without re-parsing transport messages.
    """

    def __init__(
        self,
        *,
        path: str = "",
        reason: str = "",
        message: str = "",
    ) -> None:
        super().__init__(message or reason or path or "sandbox conflict")
        self.path = path
        self.reason = reason


__all__ = [
    "SandboxApiError",
    "SandboxConflictError",
    "SandboxNotFoundError",
    "SandboxTimeoutError",
    "SandboxTransportError",
]
