"""Per-profile terminal-routing modules (``terminal_routing:`` frontmatter).

Loads the real profiles and exercises each attached ``select_terminals`` rule
across the (is_nested × has_workflow) input space. Profiles without a routing
module must carry no router.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agents
from agents.definition.loader import load_agents_tree

_PROFILE_ROOT = Path(agents.__file__).parent / "profile"


def _definitions() -> dict[str, object]:
    return {d.name: d for d in load_agents_tree(_PROFILE_ROOT)}


def test_only_planner_and_executor_have_routers():
    defs = _definitions()
    with_router = {name for name, d in defs.items() if d.terminal_router is not None}
    assert with_router == {"planner", "executor"}


def test_planner_routing_rule():
    planner = _definitions()["planner"]
    rule = planner.terminal_router
    # Nested → close only.
    assert rule(is_nested=True, has_workflow=True) == frozenset({"submit_plan_closes_goal"})
    # Top-level → close or defer (workflow flag irrelevant for planner).
    full = frozenset({"submit_plan_closes_goal", "submit_plan_defers_goal"})
    assert rule(is_nested=False, has_workflow=True) == full
    assert rule(is_nested=False, has_workflow=False) == full


def test_executor_routing_rule():
    executor = _definitions()["executor"]
    rule = executor.terminal_router
    # Outside a workflow → no filtering.
    assert rule(is_nested=True, has_workflow=False) is None
    assert rule(is_nested=False, has_workflow=False) is None
    # Nested in a workflow → no handoff.
    assert rule(is_nested=True, has_workflow=True) == frozenset(
        {"submit_generator_success", "submit_generator_failure"}
    )
    # Top-level in a workflow → handoff allowed.
    assert rule(is_nested=False, has_workflow=True) == frozenset(
        {"submit_workflow_handoff", "submit_generator_success", "submit_generator_failure"}
    )


@pytest.mark.parametrize("name", ["reducer", "advisor", "explorer"])
def test_non_routed_profiles_have_no_router(name):
    assert _definitions()[name].terminal_router is None
