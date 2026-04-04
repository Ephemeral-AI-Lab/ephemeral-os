"""Backward compatibility shim — builder now lives in ephemeralos.agents.builder."""

from ephemeralos.agents.builder import AgentBuilderService, AgentDefinitionValidator  # noqa: F401

__all__ = ["AgentBuilderService", "AgentDefinitionValidator"]
