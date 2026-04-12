"""Shared fixtures for team tests.

Registers standard agent definitions so role-based lookups
(``has_role``, ``get_role``, ``find_by_role``) work in unit tests
that reference common agent names like ``developer`` or ``validator``.
"""

from __future__ import annotations

import pytest

from agents.registry import get_definition, register_definition, unregister_definition
from agents.types import AgentDefinition

# Agent names used across team test modules and the roles they carry.
_TEST_AGENTS: list[AgentDefinition] = [
    AgentDefinition(name="developer", description="test developer", role="developer"),
    AgentDefinition(name="validator", description="test validator", role="reviewer"),
    AgentDefinition(name="team_planner", description="test planner", role="planner"),
    AgentDefinition(name="planner", description="test planner (alias)", role="planner"),
    AgentDefinition(name="scout", description="test scout", role="explorer"),
    AgentDefinition(name="team_replanner", description="test replanner", role="replanner"),
    AgentDefinition(name="worker", description="generic test worker"),
]


@pytest.fixture(autouse=True)
def _register_test_agents():
    """Register standard test agents before each test, clean up after.

    Only registers stubs for names not already present so that modules
    using ``setup_module`` to seed real builtins (e.g.
    ``test_builtin_agent_registration``) are not overwritten.
    """
    registered: list[str] = []
    for defn in _TEST_AGENTS:
        if get_definition(defn.name) is None:
            register_definition(defn)
            registered.append(defn.name)
    yield
    for name in registered:
        unregister_definition(name)
