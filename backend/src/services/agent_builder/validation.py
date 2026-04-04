"""Backward compatibility shim — validator now lives in ephemeralos.agents.builder.validation."""

from ephemeralos.agents.builder.validation import AgentDefinitionValidator  # noqa: F401

__all__ = ["AgentDefinitionValidator"]
