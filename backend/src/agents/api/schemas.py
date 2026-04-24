"""Pydantic schemas for config-backed agent definition endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agents.types import EFFORT_LEVELS


class AgentValidationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    system_prompt: str | None = None
    model: str = Field(
        min_length=1, description="Model key — each agent must be tied to a registered model key"
    )
    effort: str | None = None
    tool_call_limit: int | None = Field(default=None, gt=0)
    tools: list[str] | None = None
    skills: list[str] = Field(default_factory=list)
    hooks: dict[str, Any] | None = None
    background: bool = False
    initial_prompt: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    created_by: str | None = None

    @field_validator("effort")
    @classmethod
    def check_effort(cls, v: str | None) -> str | None:
        if v is not None and v not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {EFFORT_LEVELS}")
        return v
