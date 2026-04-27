"""Pydantic schemas for config-backed agent definition endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    system_prompt: str | None = None
    model: str = Field(
        min_length=1, description="Model key — each agent must be tied to a registered model key"
    )
    tool_call_limit: int | None = Field(default=None, gt=0)
    allowed_tools: list[str] = Field(default_factory=list)
    terminals: list[str] = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    background: bool = False
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    created_by: str | None = None
