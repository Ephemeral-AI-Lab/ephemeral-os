"""Agent definition model and constants."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

AgentType = Literal["agent", "subagent"]


class ModeDefinition(BaseModel):
    """The single tool surface for an agent run."""

    name: str
    is_default: bool = False
    allowed_tools: list[str] = Field(default_factory=list)
    terminals: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


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

    # --- skills ---
    skills: list[str] = Field(default_factory=list)

    # --- lifecycle ---
    background: bool = False

    # --- role metadata ---
    # Optional freeform label for UI display and tool-factory context.
    role: str | None = None

    # --- Python-specific ---
    permissions: list[str] = Field(default_factory=list)

    # --- agent type: regular agent or subagent (worker) ---
    agent_type: AgentType = "agent"

    # --- run tool surface ---
    # Tool access is declared via the single default ModeDefinition.
    modes: list[ModeDefinition]

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    @field_validator(
        "skills",
        "permissions",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="before")
    @classmethod
    def _synthesize_default_mode(cls, data: Any) -> Any:
        """Synthesize a single empty default mode when ``modes`` is omitted."""
        if not isinstance(data, dict):
            return data
        if "modes" in data:
            return data
        data["modes"] = [
            {
                "name": "direct",
                "is_default": True,
                "allowed_tools": [],
                "terminals": ["submit_task_completion"],
            }
        ]
        return data

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

    @field_validator("modes")
    @classmethod
    def _check_modes(cls, modes: list[ModeDefinition]) -> list[ModeDefinition]:
        if not modes:
            raise ValueError("AgentDefinition.modes must be non-empty")
        if len(modes) != 1:
            raise ValueError(
                "AgentDefinition.modes must contain exactly one default tool surface"
            )

        defaults = [m for m in modes if m.is_default]
        if len(defaults) != 1:
            raise ValueError(
                f"AgentDefinition.modes must have exactly one is_default=True "
                f"mode (got {len(defaults)})"
            )

        seen_names: set[str] = set()
        for mode in modes:
            if mode.name in seen_names:
                raise ValueError(f"Duplicate mode name: {mode.name!r}")
            seen_names.add(mode.name)
            if not mode.terminals:
                raise ValueError(
                    f"Mode {mode.name!r} must declare at least one terminal"
                )
        return modes

    @computed_field  # type: ignore[prop-decorator]
    @property
    def default_mode(self) -> ModeDefinition:
        """The unique default tool surface."""
        for mode in self.modes:
            if mode.is_default:
                return mode
        # Validator guarantees one exists; this is unreachable.
        raise RuntimeError(f"AgentDefinition {self.name!r} has no default mode")
