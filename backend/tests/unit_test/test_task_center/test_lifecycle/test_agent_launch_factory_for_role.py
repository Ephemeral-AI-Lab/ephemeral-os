"""AgentLaunchFactory surface tests."""

from __future__ import annotations

import inspect

from task_center.attempt.launch import AgentLaunchFactory


def test_agent_launch_factory_public_surface_preserved() -> None:
    expected_methods = {"for_planner", "for_generator", "for_evaluator"}
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


def test_for_evaluator_signature() -> None:
    sig = inspect.signature(AgentLaunchFactory.for_evaluator)
    assert set(sig.parameters) == {"self", "attempt", "task_id"}
