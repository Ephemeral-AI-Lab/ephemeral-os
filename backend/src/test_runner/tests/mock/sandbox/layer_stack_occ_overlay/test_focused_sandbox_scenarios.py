"""Live regressions for focused sandbox integration scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_runner.benchmarks.sweevo.models import SWEEvoInstance
from test_runner.audit.events import EventType
from test_runner.core.stores import TaskStoreBundle
from test_runner.environments.sweevo_image.fixtures import run_scenario_on_sweevo_image
from test_runner.scenarios import SCENARIO_REGISTRY
from test_runner.tests._live_config import database_configured
from test_runner.tests.mock._focused_scenario_contracts import (
    FocusedScenarioCase,
    assert_focused_scenario_report,
)

pytestmark = pytest.mark.asyncio


_FOCUSED_SANDBOX_CASES: tuple[FocusedScenarioCase, ...] = (
    FocusedScenarioCase(
        "sandbox.occ_concurrent_conflicts",
        min_event_counts={
            EventType.SANDBOX_BATCH_EDIT_APPLIED: 1,
            EventType.SANDBOX_CONFLICT_DETECTED: 1,
        },
        min_done_role_tasks={"executor": 1},
        attempt_count=1,
    ),
)


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.parametrize(
    "case",
    _FOCUSED_SANDBOX_CASES,
    ids=[case.name for case in _FOCUSED_SANDBOX_CASES],
)
async def test_focused_sandbox_reference_scenario_runs(
    case: FocusedScenarioCase,
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskStoreBundle,
) -> None:
    scenario = SCENARIO_REGISTRY[case.name]()
    report = await run_scenario_on_sweevo_image(
        scenario,
        instance=sweevo_image_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
    )

    assert_focused_scenario_report(report, case)
