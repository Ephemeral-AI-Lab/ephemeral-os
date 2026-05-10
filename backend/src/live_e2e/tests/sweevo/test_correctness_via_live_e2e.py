"""Live e2e regression: ``CorrectnessTesting`` driven through the generic
``live_e2e.run_scenario`` (with SWE-EVO sandbox + entry prompt).

This complements ``test_correctness.py`` (which goes through the SWE-EVO
adapter) by exercising the generic entry point directly. Both must produce the
same end-to-end behaviour post-migration.

Skipped when:

- ``EPHEMERALOS_DATABASE_URL`` is unset (PG required for stores).
- The Daytona tier-0 health probe fails (no live sandbox available).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR
from benchmarks.sweevo.prompt import build_sweevo_user_prompt
from live_e2e import run_scenario
from live_e2e.audit.events import EventType
from live_e2e.hooks.builtins import count_events
from live_e2e.scenarios.correctness_testing import CorrectnessTesting


def _require_daytona_healthy() -> None:
    """Tier-0 health gate. Skip cleanly if Daytona is unavailable."""
    repo_root = Path(__file__).resolve().parents[5]
    tier0_path = (
        repo_root
        / "backend"
        / "tests"
        / "live_e2e_test"
        / "_tools"
        / "tier0_health.py"
    )
    if not tier0_path.exists():
        pytest.skip(f"tier0_health module not found at {tier0_path}")
    spec = importlib.util.spec_from_file_location(
        "_live_e2e_tier0_health", tier0_path
    )
    if spec is None or spec.loader is None:
        pytest.skip(f"tier0_health module not loadable from {tier0_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    result = module.probe_tier0()
    if not result.passed:
        pytest.skip(
            f"Tier-0 health gate failed: api_health={result.api_health!r} "
            f"notes={result.notes!r}"
        )


@pytest.mark.asyncio
async def test_correctness_testing_via_live_e2e(
    sweevo_instance: SWEEvoInstance,
    sweevo_sandbox: dict[str, object],
    audit_dir: Path,
) -> None:
    if not os.environ.get("EPHEMERALOS_DATABASE_URL"):
        pytest.skip("EPHEMERALOS_DATABASE_URL not set — live_e2e requires PostgreSQL")
    _require_daytona_healthy()

    scenario = CorrectnessTesting()
    extra_hooks = (
        count_events(EventType.PLANNER_INVOKED, name="planner_invocations"),
        count_events(EventType.EVALUATOR_INVOKED, name="evaluator_invocations"),
    )
    report = await run_scenario(
        scenario,
        sandbox_id=str(sweevo_sandbox["sandbox_id"]),
        audit_dir=audit_dir,
        repo_dir=_REPO_DIR,
        entry_prompt=build_sweevo_user_prompt(sweevo_instance, repo_dir=_REPO_DIR),
        extra_hooks=extra_hooks,
        instance_id=sweevo_instance.instance_id,
    )

    assert report.task_center_status == "done", (
        f"task_center_status={report.task_center_status!r}: {report.metrics}"
    )
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]

    run_dir = report.run_dir
    assert (run_dir / "run.json").exists()
    run_payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_payload["task_center_run_id"] == report.task_center_run_id
    assert run_payload["scenario_name"] == scenario.name
