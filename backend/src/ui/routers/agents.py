"""Backward compatibility shim — router now lives in ephemeralos.agents.api.router."""

from ephemeralos.agents.api.router import create_agents_router  # noqa: F401

__all__ = ["create_agents_router"]
