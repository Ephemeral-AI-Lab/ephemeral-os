"""AgentDefinitions used by the live e2e mock runner.

The squad uses the repository's main-profile markdown definitions so live e2e
coverage exercises the same frontmatter, variants, terminals, and system
prompts as production launches. The mock runner still executes deterministic
tool calls, but agent selection comes from real ``agent.md``-style metadata.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from agents import (
    AgentDefinition,
    list_definitions,
    load_agents_dir,
    register_definition,
    unregister_definition,
)


_PROFILE_ROOT = Path(__file__).resolve().parents[3] / "agents" / "profile"
_MAIN_PROFILE_DIR = _PROFILE_ROOT / "main"
# The mock now drives the REAL loop, which spawns the advisor (via ``ask_advisor``,
# required by the gated-terminal advisor gate) and the explorer (via
# ``run_subagent``). Both must be registered or those spawns fail to resolve.
_HELPER_PROFILE_DIR = _PROFILE_ROOT / "helper"
_SUBAGENT_PROFILE_DIR = _PROFILE_ROOT / "subagent"


@contextlib.contextmanager
def registered_mock_agents() -> Iterator[None]:
    """Temporarily install the main TaskCenter squad definitions."""
    previous = list_definitions()
    for definition in previous:
        unregister_definition(definition.name)

    for definition in mock_agent_definitions():
        register_definition(definition)

    try:
        yield
    finally:
        for definition in list_definitions():
            unregister_definition(definition.name)
        for definition in previous:
            register_definition(definition)


def mock_agent_definitions() -> tuple[AgentDefinition, ...]:
    """Load the production squad (main) + the helper/subagent profiles the real
    loop spawns (advisor, explorer) for deterministic runs."""
    return (
        *load_agents_dir(_MAIN_PROFILE_DIR),
        *load_agents_dir(_HELPER_PROFILE_DIR),
        *load_agents_dir(_SUBAGENT_PROFILE_DIR),
    )


__all__ = [
    "mock_agent_definitions",
    "registered_mock_agents",
]
