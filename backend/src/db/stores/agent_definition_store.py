"""Backward compatibility shim — store now lives in ephemeralos.agents.db.store."""

from ephemeralos.agents.db.store import AgentDefinitionStore  # noqa: F401

__all__ = ["AgentDefinitionStore"]
