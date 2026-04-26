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
    """One typestate of an agent — bounds the tools it may call.

    A mode encodes commitment: while a task sits in this mode, the dispatcher
    refuses any tool not on the mode's surface. The default mode is the
    "direct" entrypoint with an open toolset; secondary modes are entered
    by an explicit ``entry_tool`` and exited only via their ``terminals``.
    See ``docs/architecture/agent-mode-system-v1.md`` for the full spec.
    """

    name: str
    is_default: bool = False
    # ``None`` means "anything not in disallowed_tools" (open toolset for the
    # default mode). A list — even an empty one — is an explicit allowlist.
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] = Field(default_factory=list)
    terminals: list[str] = Field(default_factory=list)
    entry_tool: str | None = None
    briefing: str | None = None

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

    # --- mode-aware tool surface ---
    # The full tool surface is the union of every mode's surface; per-mode
    # gating happens at dispatch time via :class:`ModeDefinition`.
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
    def _legacy_tools_to_default_mode(cls, data: Any) -> Any:
        """Synthesize a single default mode when ``modes`` is not given.

        User-defined agents loaded from YAML may still declare a flat
        ``tools: [...]`` list and have no ``modes`` block; older test
        fixtures may pass neither. Both cases collapse to a single
        ``ModeDefinition(name="direct", is_default=True, ...,
        terminals=["submit_task_completion"])`` so callers don't have to
        re-spell the boilerplate. When ``modes`` is supplied, ``tools`` is
        ignored — agents that need richer mode-aware surfaces declare
        ``modes`` directly.
        """
        if not isinstance(data, dict):
            return data
        if "modes" in data:
            # Caller supplied modes (possibly empty — let the field validator
            # reject that). Drop any legacy ``tools`` to avoid mixing shapes.
            data.pop("tools", None)
            return data
        legacy_tools = data.pop("tools", None)
        if isinstance(legacy_tools, str):
            legacy_tools = [s.strip() for s in legacy_tools.split(",") if s.strip()]
        data["modes"] = [
            {
                "name": "direct",
                "is_default": True,
                "allowed_tools": list(legacy_tools) if legacy_tools else [],
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

        defaults = [m for m in modes if m.is_default]
        if len(defaults) != 1:
            raise ValueError(
                f"AgentDefinition.modes must have exactly one is_default=True "
                f"mode (got {len(defaults)})"
            )
        default = defaults[0]
        if default.entry_tool is not None or default.briefing is not None:
            raise ValueError(
                f"Default mode {default.name!r} must have entry_tool=None "
                "and briefing=None"
            )

        seen_names: set[str] = set()
        seen_entry_tools: set[str] = set()
        for mode in modes:
            if mode.name in seen_names:
                raise ValueError(f"Duplicate mode name: {mode.name!r}")
            seen_names.add(mode.name)
            if not mode.terminals:
                raise ValueError(
                    f"Mode {mode.name!r} must declare at least one terminal"
                )
            if not mode.is_default:
                if not mode.entry_tool:
                    raise ValueError(
                        f"Non-default mode {mode.name!r} must declare entry_tool"
                    )
                if not mode.briefing:
                    raise ValueError(
                        f"Non-default mode {mode.name!r} must declare a briefing"
                    )
                if mode.entry_tool in seen_entry_tools:
                    raise ValueError(
                        f"Duplicate entry_tool across modes: {mode.entry_tool!r}"
                    )
                seen_entry_tools.add(mode.entry_tool)
        return modes

    @computed_field  # type: ignore[prop-decorator]
    @property
    def default_mode(self) -> ModeDefinition:
        """The unique mode with ``is_default=True``."""
        for mode in self.modes:
            if mode.is_default:
                return mode
        # Validator guarantees one exists; this is unreachable.
        raise RuntimeError(f"AgentDefinition {self.name!r} has no default mode")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def modes_by_name(self) -> dict[str, ModeDefinition]:
        """Lookup table from mode name to definition."""
        return {mode.name: mode for mode in self.modes}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tool_universe(self) -> frozenset[str]:
        """Union of every mode's reachable tool surface.

        Used at agent-load time to build the runtime tool registry. Per-mode
        gating still happens at dispatch via :class:`ModeDefinition`. The
        universe excludes ``None`` allow-lists (default-mode "open toolset"
        can't be enumerated here — it relies on the global registry).
        """
        names: set[str] = set()
        for mode in self.modes:
            if mode.allowed_tools is not None:
                names.update(mode.allowed_tools)
            names.update(mode.terminals)
            if mode.entry_tool:
                names.add(mode.entry_tool)
        return frozenset(names)
