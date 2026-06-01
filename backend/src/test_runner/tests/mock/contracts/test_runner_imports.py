"""Offline integration test for the test_runner wiring.

Verifies that the framework's public surface is importable, the scenario-loop
runner constructs without an SWE-EVO instance, the agent registry can be
installed + restored cleanly, and a scenario can produce a planner response
from a fresh ScenarioContext — all without invoking Daytona or Postgres.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from agents import AgentDefinition, AgentRole
from agents import list_definitions
from test_runner import RunReport, run_scenario
from test_runner.audit.bus import AuditEventBus
from test_runner.scenarios.base import ScenarioContext
from test_runner.scenarios.correctness_testing import CorrectnessTesting
from test_runner.scenarios.full_case_user_input import FullCaseUserInput
from test_runner.scenarios.full_stack_adversarial import FullStackAdversarial
from test_runner.agent.mock.definitions import (
    mock_agent_definitions,
    registered_mock_agents,
)
from test_runner.agent.mock import scenario_loop_runner as loop_runner_module
from test_runner.agent.mock.scenario_loop_runner import ScenarioLoopRunner
from tools._framework.core.runtime import ExecutionMetadata
from tools.submission.planner import submit_planner_outcome


def test_runner_top_level_exports_are_callable() -> None:
    assert callable(run_scenario)
    assert RunReport.__module__ == "test_runner.core.runner"
    sig = inspect.signature(run_scenario)
    # de-sweevo-fied signature: no ``instance``, ``repo_dir`` is required, and
    # ``entry_prompt`` is required.
    params = sig.parameters
    assert "instance" not in params
    assert params["repo_dir"].default is inspect.Parameter.empty
    assert params["entry_prompt"].default is inspect.Parameter.empty
    assert params["sandbox_id"].default is inspect.Parameter.empty


def _runner() -> ScenarioLoopRunner:
    return ScenarioLoopRunner(
        repo_dir="/tmp/live_e2e_test_repo",
        bus=AuditEventBus(),
        scenario=CorrectnessTesting(),
    )


class _TaskStore:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self._rows = rows

    def get_task(self, task_id: str) -> dict[str, object] | None:
        return self._rows.get(task_id)


def _runtime_with_tasks(rows: dict[str, dict[str, object]]) -> SimpleNamespace:
    return SimpleNamespace(task_store=_TaskStore(rows))


def _metadata_for_task(task_id: str, *, needs: list[str]) -> ExecutionMetadata:
    return ExecutionMetadata(
        task_id=task_id,
        attempt_runtime=_runtime_with_tasks({task_id: {"needs": needs}}),
    )


def _agent(name: str, role: AgentRole) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        description=f"test {name}",
        role=role,
        terminals=["terminal"],
        tool_call_limit=10,
    )


def _role_context(
    *,
    role: str,
    task_id: str,
    dependency_sections: list[str],
) -> str:
    sections = [
        f'<context role="{role}">',
        "<dependencies>",
        *dependency_sections,
        "</dependencies>",
        f'<assigned_task task_id="{task_id}">Run {task_id}.</assigned_task>',
        "</context>",
    ]
    return "\n".join(sections)


def _dependency_section(
    task_id: str,
    *,
    task_outcome: str | None = None,
    outcome_task_id: str | None = None,
) -> str:
    if task_outcome is None:
        return f'<dependency task_id="{task_id}"></dependency>'
    rendered_task_id = outcome_task_id or task_id
    return "\n".join(
        [
            f'<dependency task_id="{task_id}">',
            f'<task task_id="{rendered_task_id}" role="generator" status="success">',
            task_outcome,
            "</task>",
            "</dependency>",
        ]
    )


def test_scenario_loop_runner_constructs_without_instance() -> None:
    bus = AuditEventBus()
    runner = ScenarioLoopRunner(
        repo_dir="/tmp/live_e2e_test_repo",
        bus=bus,
        scenario=CorrectnessTesting(),
    )
    assert runner._repo_dir == "/tmp/live_e2e_test_repo"  # noqa: SLF001
    assert not hasattr(runner, "_instance"), (
        "ScenarioLoopRunner must not retain an SWE-EVO ``instance`` attribute "
        "after the de-sweevo migration"
    )


def test_prompt_inspector_accepts_current_failed_attempt_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        loop_runner_module,
        "_attempt_and_iteration",
        lambda _metadata: (
            SimpleNamespace(attempt_sequence_no=2),
            SimpleNamespace(sequence_no=1),
        ),
    )

    inspection = _runner()._inspect_prompt(  # noqa: SLF001
        prompt="\n".join(
            [
                "<goal>Do the retry work.</goal>",
                '<iteration iteration_no="1" position="current">',
                '<attempt attempt_no="1">Attempt 1 failed.</attempt>',
                "</iteration>",
            ]
        ),
        agent_def=AgentDefinition(
            name="planner",
            description="test planner",
            role=AgentRole.PLANNER,
            terminals=["submit_planner_outcome", "submit_planner_outcome"],
            tool_call_limit=10,
        ),
        metadata=ExecutionMetadata(task_id="attempt-2:planner"),
    )

    assert inspection.checks["failed_attempts"]
    assert inspection.passed


def test_prompt_inspector_accepts_current_previous_iteration_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        loop_runner_module,
        "_attempt_and_iteration",
        lambda _metadata: (
            SimpleNamespace(attempt_sequence_no=1),
            SimpleNamespace(sequence_no=2),
        ),
    )

    inspection = _runner()._inspect_prompt(  # noqa: SLF001
        prompt="\n".join(
            [
                "<goal>Continue the delegated workflow.</goal>",
                '<iteration iteration_no="1" position="prior">',
                '<task id="schema" status="success">Earlier result.</task>',
                "</iteration>",
                '<iteration iteration_no="2" position="current">',
                "Next slice.",
                "</iteration>",
            ]
        ),
        agent_def=AgentDefinition(
            name="planner",
            description="test planner",
            role=AgentRole.PLANNER,
            terminals=["submit_planner_outcome", "submit_planner_outcome"],
            tool_call_limit=10,
        ),
        metadata=ExecutionMetadata(task_id="attempt-1:planner"),
    )

    assert inspection.checks["previous_iteration_results"]
    assert inspection.passed


def test_prompt_inspector_accepts_planner_with_unified_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        loop_runner_module,
        "_attempt_and_iteration",
        lambda _metadata: (
            SimpleNamespace(attempt_sequence_no=1),
            SimpleNamespace(sequence_no=1),
        ),
    )

    inspection = _runner()._inspect_prompt(  # noqa: SLF001
        prompt="\n".join(
            [
                "<context>",
                "<goal>Close this delegated recursive workflow.</goal>",
                '<iteration iteration_no="1" position="current">',
                "<iteration_goal>Close this delegated recursive workflow.</iteration_goal>",
                "</iteration>",
                "</context>",
                "<Task Guidance>",
                "Use submit_planner_outcome to close this goal in one attempt.",
                "</Task Guidance>",
            ]
        ),
        agent_def=AgentDefinition(
            name="planner",
            description="test full-only planner",
            role=AgentRole.PLANNER,
            terminals=["submit_planner_outcome"],
            tool_call_limit=10,
        ),
        metadata=ExecutionMetadata(
            task_id="recursive-1:planner",
            extras={"active_terminals": ["submit_planner_outcome"]},
        ),
    )

    assert inspection.checks == {"goal": True, "current_iteration": True}
    assert inspection.passed


def test_prompt_inspector_accepts_close_only_current_iteration_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        loop_runner_module,
        "_attempt_and_iteration",
        lambda _metadata: (
            SimpleNamespace(attempt_sequence_no=1),
            SimpleNamespace(sequence_no=1),
        ),
    )

    inspection = _runner()._inspect_prompt(  # noqa: SLF001
        prompt="\n".join(
            [
                '<context role="planner">',
                "<workflow>",
                "<goal>Close this delegated recursive workflow.</goal>",
                '<current_iteration sequence="1">',
                "<goal>Close this delegated recursive workflow.</goal>",
                "</current_iteration>",
                "</workflow>",
                "</context>",
                "<terminal_tool_selection>",
                "`submit_planner_outcome`",
                "</terminal_tool_selection>",
            ]
        ),
        agent_def=AgentDefinition(
            name="planner",
            description="test full-only planner",
            role=AgentRole.PLANNER,
            terminals=["submit_planner_outcome"],
            tool_call_limit=10,
        ),
        metadata=ExecutionMetadata(
            task_id="recursive-1:planner",
            extras={"active_terminals": ["submit_planner_outcome"]},
        ),
    )

    assert inspection.checks == {"goal": True, "current_iteration": True}
    assert inspection.passed


def test_prompt_inspector_verifies_dependent_executor_outcomes() -> None:
    task_id = "attempt-1:gen:b"

    inspection = _runner()._inspect_prompt(  # noqa: SLF001
        prompt=_role_context(
            role="generator",
            task_id=task_id,
            dependency_sections=[
                _dependency_section("attempt-1:gen:a", task_outcome="Generator a completed.")
            ],
        ),
        agent_def=_agent("executor", AgentRole.GENERATOR),
        metadata=_metadata_for_task(task_id, needs=["attempt-1:gen:a"]),
    )

    assert inspection.checks == {
        "assigned_task": True,
        "dependencies": True,
        "dependency_outcomes": True,
    }
    assert inspection.passed


def test_prompt_inspector_flags_dependent_executor_without_outcomes() -> None:
    task_id = "attempt-1:gen:b"

    inspection = _runner()._inspect_prompt(  # noqa: SLF001
        prompt=_role_context(
            role="generator",
            task_id=task_id,
            dependency_sections=[_dependency_section("attempt-1:gen:a")],
        ),
        agent_def=_agent("executor", AgentRole.GENERATOR),
        metadata=_metadata_for_task(task_id, needs=["attempt-1:gen:a"]),
    )

    assert inspection.checks["dependencies"]
    assert not inspection.checks["dependency_outcomes"]
    assert not inspection.passed


def test_prompt_inspector_verifies_reducer_dependency_outcomes() -> None:
    task_id = "attempt-1:red:reduce"

    inspection = _runner()._inspect_prompt(  # noqa: SLF001
        prompt=_role_context(
            role="reducer",
            task_id=task_id,
            dependency_sections=[
                _dependency_section("attempt-1:gen:a", task_outcome="Generator a completed."),
                _dependency_section(
                    "attempt-1:gen:b",
                    task_outcome="Nested child outcome completed.",
                    outcome_task_id="child:gen:nested",
                ),
            ],
        ),
        agent_def=_agent("reducer", AgentRole.REDUCER),
        metadata=_metadata_for_task(
            task_id,
            needs=["attempt-1:gen:a", "attempt-1:gen:b"],
        ),
    )

    assert inspection.checks == {
        "assigned_task": True,
        "dependencies": True,
        "dependency_outcomes": True,
    }
    assert inspection.passed


def test_registered_mock_agents_install_and_restore() -> None:
    initial = {d.name for d in list_definitions()}
    with registered_mock_agents():
        installed = {d.name for d in list_definitions()}
        expected = {d.name for d in mock_agent_definitions()}
        assert installed == expected
    after = {d.name for d in list_definitions()}
    assert after == initial


def test_mock_agent_definitions_have_neutral_descriptions() -> None:
    """De-sweevo: descriptions and system prompts must not mention 'SWE-EVO'."""
    for definition in mock_agent_definitions():
        assert "SWE-EVO" not in (definition.description or "")
        assert "SWE-EVO" not in (definition.system_prompt or "")


@pytest.mark.parametrize(
    "scenario_cls",
    [CorrectnessTesting, FullCaseUserInput, FullStackAdversarial],
)
def test_composite_scenarios_have_stable_names(scenario_cls: type) -> None:
    scenario = scenario_cls()
    assert scenario.name in {
        "correctness_testing",
        "full_case_user_input",
        "full_stack_adversarial",
    }


def test_full_stack_recursive_planner_with_unified_terminal_closes_workflow() -> None:
    scenario = FullStackAdversarial()
    ctx = ScenarioContext(
        attempt=SimpleNamespace(attempt_sequence_no=1),
        iteration=SimpleNamespace(sequence_no=1, workflow_id="recursive-workflow"),
        workflow=SimpleNamespace(parent_task_id="parent-task:executor"),
        prompt="Run delegated recursive matrix.",
        metadata=ExecutionMetadata(
            agent_name="planner",
            extras={"active_terminals": ["submit_planner_outcome"]},
        ),
        audit_recorder=None,
        task_id="recursive-workflow:planner",
        agent_name="planner",
        instruction=None,
    )

    spec = scenario.planner_response(ctx)

    assert spec.tool.name == submit_planner_outcome.name
    assert "deferred_goal_for_next_iteration" not in spec.args
    task_ids = {task["id"] for task in spec.args["tasks"]}
    assert {
        "recursive_oversized_a",
        "recursive_oversized_b",
        "recursive_closure_report",
        "recursive_close_guard",
    } <= task_ids


def test_full_case_recursive_planner_with_unified_terminal_closes_workflow() -> None:
    scenario = FullCaseUserInput()
    ctx = ScenarioContext(
        attempt=SimpleNamespace(attempt_sequence_no=1),
        iteration=SimpleNamespace(sequence_no=1, workflow_id="recursive-workflow"),
        workflow=SimpleNamespace(parent_task_id="parent-task:executor"),
        prompt="Run delegated release package.",
        metadata=ExecutionMetadata(
            agent_name="planner",
            extras={"active_terminals": ["submit_planner_outcome"]},
        ),
        audit_recorder=None,
        task_id="recursive-workflow:planner",
        agent_name="planner",
        instruction=None,
    )

    spec = scenario.planner_response(ctx)

    assert spec.tool.name == submit_planner_outcome.name
    assert "deferred_goal_for_next_iteration" not in spec.args
    task_ids = {task["id"] for task in spec.args["tasks"]}
    assert {
        "recursive_inventory",
        "recursive_exec_a",
        "recursive_exec_b",
        "recursive_reconcile",
        "recursive_final_guard",
    } <= task_ids


def test_sweevo_image_environment_keeps_dataset_entrypoint_separate() -> None:
    """SWE-EVO image prompt wiring lives outside the generic runner."""
    from test_runner.environments.sweevo_image.fixtures import (
        run_scenario_on_sweevo_image,
    )

    assert callable(run_scenario_on_sweevo_image)
    assert inspect.signature(run_scenario_on_sweevo_image).parameters["instance"]
