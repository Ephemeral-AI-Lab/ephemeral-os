"""Agent API exports with lazy router import."""

from __future__ import annotations

__all__ = ["create_agents_router"]


def __getattr__(name: str):
    if name == "create_agents_router":
        from agents.api.router import create_agents_router

        return create_agents_router
    raise AttributeError(name)
