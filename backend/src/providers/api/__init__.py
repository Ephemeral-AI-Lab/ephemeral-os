"""Model API exports with lazy router import."""

from __future__ import annotations

__all__ = ["create_models_router"]


def __getattr__(name: str):
    if name == "create_models_router":
        from providers.api.router import create_models_router

        return create_models_router
    raise AttributeError(name)
