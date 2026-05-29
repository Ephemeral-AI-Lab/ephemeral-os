"""``build_scenario_config`` — assembles the ``RunConfig`` for a mock scenario.

Single point of truth where the ``MockSquadRunner`` factory, the
``ScenarioLifecycle`` (and thus ``HookSet``), and the shared
``MutableMockState`` are wired together so they share state. Outside this
builder no other module imports ``MutableMockState`` — the engine remains
runner-agnostic.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from task_center_runner.core.config import RunConfig
from task_center_runner.core.sandbox import AttachExisting
from task_center_runner.hooks.registry import Hook, HookSet, MutableMockState
from task_center_runner.scenarios.base import Scenario
from task_center_runner.scenarios.lifecycle import ScenarioLifecycle

if TYPE_CHECKING:
    from task_center_runner.core.config import RunContext


# Migration seam: when truthy, mock scenarios run through the real query loop
# via ``ScenarioLoopRunner`` + an injected ``ScenarioEventSource`` instead of the
# imperative ``MockSquadRunner``. Default-off keeps un-migrated scenarios on the
# old runner until each is ported (Phase 2). Flipped to default-on once the
# migration completes.
_EVENT_SOURCE_RUNNER_ENV = "EOS_MOCK_EVENT_SOURCE_RUNNER"


def _event_source_runner_enabled() -> bool:
    raw = os.environ.get(_EVENT_SOURCE_RUNNER_ENV)
    return bool(raw) and raw.strip().lower() not in {"false", "0", "no", "off"}


def build_scenario_config(
    scenario: Scenario,
    *,
    sandbox_id: str,
    audit_dir: Path,
    repo_dir: str,
    entry_prompt: str,
    extra_hooks: Sequence[Hook] = (),
    instance_id: str = "",
) -> tuple[RunConfig, MutableMockState, ScenarioLifecycle]:
    """Construct the mock-mode ``RunConfig`` plus the shared mutable state.

    Returns the config alongside the ``MutableMockState`` and
    ``ScenarioLifecycle`` so callers (the ``run_scenario`` shim) can read
    their state after the run for the legacy ``RunReport`` assembly.
    """
    mutable_state = MutableMockState()
    hook_set = HookSet()
    for hook in scenario.hooks():
        hook_set.register(hook)
    for hook in extra_hooks:
        hook_set.register(hook)
    lifecycle = ScenarioLifecycle(
        scenario=scenario, hook_set=hook_set, mutable_state=mutable_state
    )

    def _make_runner(ctx: "RunContext"):
        # Imported lazily to keep scenario import-time setup free of runner state.
        if _event_source_runner_enabled():
            from task_center_runner.agent.mock.scenario_loop_runner import (
                ScenarioLoopRunner,
            )

            return ScenarioLoopRunner(
                repo_dir=repo_dir,
                bus=ctx.bus,
                scenario=scenario,
                mutable_state=mutable_state,
            )

        from task_center_runner.agent.mock.runner import MockSquadRunner

        return MockSquadRunner(
            repo_dir=repo_dir,
            bus=ctx.bus,
            task_center_run_id="",
            scenario=scenario,
            mutable_state=mutable_state,
            audit_recorder=None,
        )

    # A real ``RuntimeConfig`` is threaded as ``runtime_config`` so the launcher
    # passes it (not a bare ``SimpleNamespace``) to the runner: the event-source
    # path needs ``resolve_settings``/``external_api_client``/
    # ``event_source_factory`` to reach ``run_ephemeral_agent`` → ``spawn_agent``.
    # Harmless to ``MockSquadRunner`` (it only reads ``.cwd``).
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
    return config, mutable_state, lifecycle


__all__ = ["build_scenario_config"]
