"""Agent definition model and constants."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

#: Valid effort level strings.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high")


class AgentDefinition(BaseModel):
    """Full agent definition with all configuration fields."""

    # --- required ---
    name: str
    description: str

    # --- prompt ---
    system_prompt: str | None = None

    # --- model & effort ---
    model: str | None = Field(default=None, alias="model_key")
    effort: str | int | None = None

    # --- agent loop control ---
    max_turns: int | None = Field(
        default=None, validation_alias=AliasChoices("max_turns", "maxTurns")
    )

    # --- skills & toolkits ---
    skills: list[str] = Field(default_factory=list)
    toolkits: list[str] = Field(default_factory=list)

    # --- hooks ---
    hooks: dict[str, Any] | None = None

    # --- lifecycle ---
    background: bool = False
    initial_prompt: str | None = Field(
        default=None, validation_alias=AliasChoices("initial_prompt", "initialPrompt")
    )

    # --- metadata ---
    critical_system_reminder: str | None = Field(
        default=None,
        validation_alias=AliasChoices("critical_system_reminder", "criticalSystemReminder"),
    )

    # --- Python-specific ---
    permissions: list[str] = Field(default_factory=list)
    source: Literal["builtin", "user", "plugin"] = "builtin"

    # --- agent type: regular agent or subagent (worker) ---
    agent_type: Literal["agent", "subagent"] = "agent"

    model_config = {"populate_by_name": True}

    @field_validator("skills", "toolkits", "permissions", mode="before")
    @classmethod
    def _split_csv(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator("max_turns", mode="before")
    @classmethod
    def _coerce_positive_int(cls, v: Any) -> Any:
        if v is None or isinstance(v, int):
            return v if (v is None or v > 0) else None
        try:
            n = int(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    @field_validator("effort", mode="before")
    @classmethod
    def _validate_effort(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, int):
            return v if v > 0 else None
        if isinstance(v, str) and v in EFFORT_LEVELS:
            return v
        return None

    @field_validator("background", mode="before")
    @classmethod
    def _coerce_bool(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.lower() == "true"
        return bool(v) if v is not None else False
