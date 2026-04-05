"""Pipeline configuration schema — user-facing, serializable models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InputDepConfig(BaseModel):
    """Declares which prior step outputs this step needs."""

    step: str
    keys: list[str] | None = None


class PipelineStepConfig(BaseModel):
    """Configuration for a single pipeline step.

    Every field except *name* and *agent* has a sensible default.
    Agent definitions own model/toolkits/skills/prompt — steps just
    reference agents by name.
    """

    name: str
    agent: str
    description: str = ""
    enabled: bool = True
    timeout: float | None = None
    tool_call_limit: int | None = None
    posthook_agent: str | None = None
    output_schema: dict[str, Any] | None = None
    input_deps: list[InputDepConfig] = Field(default_factory=list)
    checkpoint: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    """Full pipeline definition."""

    pipeline_id: str
    name: str
    description: str = ""
    version: int = 1
    steps: list[PipelineStepConfig]
    default_timeout: float = 300.0
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
