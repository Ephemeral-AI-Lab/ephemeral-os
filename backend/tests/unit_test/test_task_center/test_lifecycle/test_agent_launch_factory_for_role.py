"""AgentLaunchFactory surface tests."""

from __future__ import annotations

import inspect

from workflow.attempt.launch import AgentLaunchFactory


def test_agent_launch_factory_public_surface_preserved() -> None:
    expected_methods = {"for_planner", "for_generator", "for_reducer"}
    actual_methods = {
        name for name in vars(AgentLaunchFactory) if not name.startswith("_")
    }
    missing = expected_methods - actual_methods
    assert not missing, f"AgentLaunchFactory missing factory methods: {missing}"


def test_for_planner_signature() -> None:
    sig = inspect.signature(AgentLaunchFactory.for_planner)
    assert set(sig.parameters) == {"self", "attempt", "task_id"}


def test_for_generator_signature() -> None:
    sig = inspect.signature(AgentLaunchFactory.for_generator)
    assert set(sig.parameters) == {"self", "attempt", "task", "base_agent_name"}


def test_for_reducer_signature() -> None:
    # A reducer is a plan task like a generator: the factory takes its row.
    sig = inspect.signature(AgentLaunchFactory.for_reducer)
    assert set(sig.parameters) == {"self", "attempt", "task"}
