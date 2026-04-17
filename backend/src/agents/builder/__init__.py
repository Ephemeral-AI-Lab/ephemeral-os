"""Agent builder exports with lazy imports for optional heavy dependencies."""

from __future__ import annotations

__all__ = ["AgentBuilderService", "AgentDefinitionValidator"]


def __getattr__(name: str) -> object:
    if name == "AgentBuilderService":
        from agents.builder.service import AgentBuilderService

        return AgentBuilderService
    if name == "AgentDefinitionValidator":
        from agents.builder.validation import AgentDefinitionValidator

        return AgentDefinitionValidator
    raise AttributeError(name)
