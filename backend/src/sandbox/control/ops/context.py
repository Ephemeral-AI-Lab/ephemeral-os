"""Provider-neutral context-preparer factory.

The Protocol moved here from ``sandbox.providers.protocol`` so that only
neutral code consumes it. Provider-owned implementations are looked up via
the registered adapter, never imported by name.
"""

from __future__ import annotations

from typing import Any, Protocol, cast


class SandboxContextPreparer(Protocol):
    """Provider-owned context hook used by agent runtime setup."""

    def prepare_context(self, context: Any) -> None: ...
    async def prepare_context_async(self, context: Any) -> None: ...


def context_preparer_for(sandbox_id: str) -> SandboxContextPreparer:
    """Return the context preparer attached to *sandbox_id*'s adapter.

    The provider adapter exposes ``context_preparer(sandbox_id) ->
    SandboxContextPreparer`` so this factory is provider-agnostic.
    """
    from sandbox.providers.registry import get_adapter

    adapter = get_adapter(sandbox_id)
    factory = getattr(adapter, "context_preparer", None)
    if not callable(factory):
        raise RuntimeError(
            f"Provider adapter for sandbox {sandbox_id!r} does not expose "
            "context_preparer()."
        )
    return cast(SandboxContextPreparer, factory(sandbox_id))


__all__ = [
    "SandboxContextPreparer",
    "context_preparer_for",
]
