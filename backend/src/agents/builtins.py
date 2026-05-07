"""Builtin agent definitions.

The legacy TaskCenter harness agents were removed with the old TaskCenter
runtime. Keep this module as a compatibility surface for callers that still
seed builtins during startup.
"""

from __future__ import annotations

from .types import AgentDefinition


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


BUILTIN_AGENTS: tuple[AgentDefinition, ...] = ()


def register_builtin_agents() -> None:
    """Register all built-in agent definitions used by the harness."""
    from .registry import register_definition

    for defn in BUILTIN_AGENTS:
        register_definition(defn)


__all__ = [
    "BUILTIN_AGENTS",
    "register_builtin_agents",
]
