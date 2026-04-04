"""Backward compatibility shim — schemas now live in ephemeralos.agents.api.schemas."""

from ephemeralos.agents.api.schemas import (  # noqa: F401
    AgentDefinitionCreate,
    AgentDefinitionResponse,
    AgentDefinitionUpdate,
    AgentValidationResult,
    CloneRequest,
)

__all__ = [
    "AgentDefinitionCreate",
    "AgentDefinitionResponse",
    "AgentDefinitionUpdate",
    "AgentValidationResult",
    "CloneRequest",
]
