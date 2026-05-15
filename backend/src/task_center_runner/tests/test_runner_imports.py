"""Offline integration test for the task_center_runner wiring.

Verifies that the framework's public surface is importable, the squad runner
constructs without an SWE-EVO instance, the agent registry can be installed +
restored cleanly, and a scenario can produce a planner response from a fresh
ScenarioContext — all without invoking Daytona or Postgres.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from agents import AgentDefinition, AgentKind
from agents import list_definitions
from task_center_runner import RunReport, run_scenario
from task_center_runner.audit.bus import AuditEventBus
from task_center_runner.hooks.registry import HookSet, MutableMockState
from task_center_runner.scenarios.correctness_testing import CorrectnessTesting
from task_center_runner.scenarios.full_case_user_input import FullCaseUserInput
from task_center_runner.scenarios.full_stack_adversarial import FullStackAdversarial
from task_center_runner.agent.mock.definitions import (
    mock_agent_definitions,
    registered_mock_agents,
)
from task_center_runner.agent.mock.runner import MockSquadRunner
from tools._framework.core.runtime import ExecutionMetadata


def test_runner_top_level_exports_are_callable() -> None:
    assert callable(run_scenario)
    assert RunReport.__module__ == "task_center_runner.core.runner"
    sig = inspect.signature(run_scenario)
    # de-sweevo-fied signature: no ``instance``, ``repo_dir`` is required, and
    # ``entry_prompt`` is required.
    params = sig.parameters
    assert "instance" not in params
    assert params["repo_dir"].default is inspect.Parameter.empty
    assert params["entry_prompt"].default is inspect.Parameter.empty
    assert params["sandbox_id"].default is inspect.Parameter.empty


def test_squad_runner_constructs_without_instance() -> None:
    bus = AuditEventBus()
    state = MutableMockState()
    runner = MockSquadRunner(
        repo_dir="/tmp/live_e2e_test_repo",
        bus=bus,
        scenario=CorrectnessTesting(),
        mutable_state=state,
    )
    assert runner._repo_dir == "/tmp/live_e2e_test_repo"  # noqa: SLF001
    assert not hasattr(runner, "_instance"), (
        "MockSquadRunner must not retain an SWE-EVO ``instance`` attribute "
        "after the de-sweevo migration"
    )
    # Probe paths are preserved verbatim per the migration spec.
    assert runner._probe_path() == ".ephemeralos/sweevo-mock/probe.txt"  # noqa: SLF001


def test_prompt_inspector_accepts_current_failed_attempt_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = MockSquadRunner(
        repo_dir="/tmp/live_e2e_test_repo",
        bus=AuditEventBus(),
        scenario=CorrectnessTesting(),
        mutable_state=MutableMockState(),
    )
    monkeypatch.setattr(
        runner,
        "_current_attempt_and_episode",
        lambda _metadata: (
            SimpleNamespace(attempt_sequence_no=2),
            SimpleNamespace(sequence_no=1),
        ),
    )

    inspection = runner._inspect_prompt(  # noqa: SLF001
        prompt="\n".join(
            [
                "# Goal / Current Iteration",
                "Do the retry work.",
                "# Failed Trials",
                "Trial 1 failed.",
            ]
        ),
        agent_def=AgentDefinition(
            name="planner",
            description="test planner",
            agent_kind=AgentKind.PLANNER,
        ),
        metadata=ExecutionMetadata(task_center_task_id="attempt-2:planner"),
    )

    assert inspection.checks["failed_attempts"]
    assert inspection.passed


def test_registered_mock_agents_install_and_restore() -> None:
    initial = {d.name for d in list_definitions()}
    with registered_mock_agents():
        installed = {d.name for d in list_definitions()}
        assert installed == {
            "entry_executor",
            "planner",
            "planner_full_only",
            "executor",
            "executor_success_failure",
            "executor_success_handoff",
            "verifier",
            "evaluator",
        }
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
def test_scenarios_register_hookset_cleanly(scenario_cls: type) -> None:
    scenario = scenario_cls()
    hook_set = HookSet()
    for hook in scenario.hooks():
        hook_set.register(hook)
    assert scenario.name in {
        "correctness_testing",
        "full_case_user_input",
        "full_stack_adversarial",
    }
    assert hasattr(scenario, "expected_event_sequence")


def test_sweevo_adapter_keeps_dataset_entrypoint_separate() -> None:
    """SWE-EVO-specific prompt wiring lives outside the generic runner."""
    from task_center_runner.benchmarks.sweevo.fixtures import (
        run_sweevo_scenario,
    )

    assert callable(run_sweevo_scenario)
    assert inspect.signature(run_sweevo_scenario).parameters["instance"]
