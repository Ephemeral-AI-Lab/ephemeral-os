"""Agent definition model and constants."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AgentType = Literal["agent", "subagent"]


class AgentDefinition(BaseModel):
    """Full agent definition with all configuration fields."""

    # --- required ---
    name: str
    description: str

    # --- prompt ---
    system_prompt: str | None = None

    # --- model ---
    model: str | None = None

    # --- agent loop control ---
    # Per-ephemeral-run cap on tool dispatches. ``None`` = unlimited.
    # Each ``EphemeralAgent`` spawn starts with a fresh counter, so
    # nested ``run_subagent`` calls have independent budgets and the
    # caller's counter is untouched.
    tool_call_limit: int | None = None

    # --- skills & tools ---
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(
        default_factory=list,
        description="Tool names available to this agent.",
    )
    # --- lifecycle ---
    background: bool = False

    # --- role metadata ---
    # Optional freeform label for UI display and tool-factory context.
    role: str | None = None

    # --- Python-specific ---
    permissions: list[str] = Field(default_factory=list)

    # --- agent type: regular agent or subagent (worker) ---
    agent_type: AgentType = "agent"

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    @field_validator(
        "skills",
        "permissions",
        "tools",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator("tool_call_limit", mode="before")
    @classmethod
    def _coerce_positive_int(cls, v: Any) -> Any:
        if v is None or isinstance(v, int):
            return v if (v is None or v > 0) else None
        try:
            n = int(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    @field_validator("background", mode="before")
    @classmethod
    def _coerce_bool(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.lower() == "true"
        return bool(v) if v is not None else False
