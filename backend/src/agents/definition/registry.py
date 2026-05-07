"""Runtime registry for config-backed agent definitions."""

from __future__ import annotations

from .model import AgentDefinition

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_DEFINITIONS: dict[str, AgentDefinition] = {}


def register_definition(defn: AgentDefinition) -> None:
    """Register or replace an agent definition at runtime."""
    _DEFINITIONS[defn.name] = defn


def unregister_definition(name: str) -> bool:
    """Remove an agent definition. Returns True if it existed."""
    return _DEFINITIONS.pop(name, None) is not None


def get_definition(name: str) -> AgentDefinition | None:
    """Look up an agent definition by name."""
    return _DEFINITIONS.get(name)


def list_definitions() -> list[AgentDefinition]:
    """List all registered definitions."""
    return list(_DEFINITIONS.values())


def list_dispatchable_subagent_names() -> list[str]:
    """Return registered subagent names that may be targeted by run_subagent."""
    return sorted(
        defn.name
        for defn in _DEFINITIONS.values()
        if defn.agent_type == "subagent"
    )
