"""Backward compatibility shim — builder now lives in ephemeralos.agents.builder.service."""

from ephemeralos.agents.builder.service import AgentBuilderService  # noqa: F401

__all__ = ["AgentBuilderService"]
