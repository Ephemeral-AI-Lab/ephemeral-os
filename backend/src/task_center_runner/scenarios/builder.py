"""``build_scenario_config`` — assembles the ``RunConfig`` for a mock scenario."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from task_center_runner.core.config import RunConfig
from task_center_runner.core.sandbox import AttachExisting
from task_center_runner.scenarios.base import Scenario
from task_center_runner.scenarios.lifecycle import ScenarioLifecycle

if TYPE_CHECKING:
    from task_center_runner.core.config import RunContext


def build_scenario_config(
    scenario: Scenario,
    *,
    sandbox_id: str,
    audit_dir: Path,
    repo_dir: str,
    entry_prompt: str,
    instance_id: str = "",
) -> tuple[RunConfig, ScenarioLifecycle]:
    """Construct the mock-mode ``RunConfig`` plus its lifecycle."""
    lifecycle = ScenarioLifecycle()

    def _make_runner(ctx: "RunContext"):
        # Imported lazily to keep scenario import-time setup free of runner state.
        from task_center_runner.agent.mock.scenario_loop_runner import (
            ScenarioLoopRunner,
        )

        return ScenarioLoopRunner(
            repo_dir=repo_dir,
            bus=ctx.bus,
            scenario=scenario,
        )

    # A real ``RuntimeConfig`` is threaded as ``runtime_config`` so the launcher
    # passes it (not a bare ``SimpleNamespace``) to the runner: the event-source
    # path needs ``resolve_settings``/``external_api_client``/
    # ``event_source_factory`` to reach ``run_ephemeral_agent`` → ``spawn_agent``.
    from task_center_runner.agent.mock.scenario_loop_runner import (
        make_mock_runtime_config,
    )

    config = RunConfig(
        entry_prompt=entry_prompt,
        repo_dir=repo_dir,
        sandbox=AttachExisting(sandbox_id),
        runner_factory=_make_runner,
        lifecycle=lifecycle,
        bootstrap=None,
        audit_dir=audit_dir,
        run_label=f"scenario_logs/{scenario.name}",
        instance_id=instance_id,
        extras={
            "scenario_name": scenario.name,
            "runtime_config": make_mock_runtime_config(repo_dir),
        },
    )
    return config, lifecycle


__all__ = ["build_scenario_config"]
