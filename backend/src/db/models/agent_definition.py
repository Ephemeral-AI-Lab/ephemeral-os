"""Backward compatibility shim — model now lives in ephemeralos.agents.db.model."""

from ephemeralos.agents.db.model import AgentDefinitionRecord  # noqa: F401

__all__ = ["AgentDefinitionRecord"]
