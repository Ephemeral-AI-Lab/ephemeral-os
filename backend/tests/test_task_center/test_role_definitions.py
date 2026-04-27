"""Tests for role-local harness agent definitions."""

from __future__ import annotations

from importlib.resources import files

from task_center.harness_agents.evaluator.definition import (
    EVALUATOR,
    load_system_prompt as load_evaluator_prompt,
)
from task_center.harness_agents.executor.definition import (
    EXECUTOR,
    load_system_prompt as load_executor_prompt,
)
from task_center.harness_agents.planner.definition import (
    PLANNER,
    load_system_prompt as load_planner_prompt,
)


def test_executor_definition_loads_role_local_markdown() -> None:
    expected = files("task_center.harness_agents.executor").joinpath(
        "agent.md"
    ).read_text(encoding="utf-8")
    assert load_executor_prompt() == expected
    assert EXECUTOR.system_prompt == expected


def test_planner_definition_loads_role_local_markdown() -> None:
    expected = files("task_center.harness_agents.planner").joinpath(
        "agent.md"
    ).read_text(encoding="utf-8")
    assert load_planner_prompt() == expected
    assert PLANNER.system_prompt == expected


def test_evaluator_definition_loads_role_local_markdown() -> None:
    expected = files("task_center.harness_agents.evaluator").joinpath(
        "agent.md"
    ).read_text(encoding="utf-8")
    assert load_evaluator_prompt() == expected
    assert EVALUATOR.system_prompt == expected
