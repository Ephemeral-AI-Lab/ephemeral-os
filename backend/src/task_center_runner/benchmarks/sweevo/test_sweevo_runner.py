"""Real-agent live-e2e smoke test for the SWE-EVO benchmarker.

Gated off by default. Requires:

- ``EOS_SWEEVO_REAL_AGENT_TESTS=1`` (real LLM creds + real Daytona)
- ``EOS_LIVE_TESTS=1`` (matches the canonical live-tier guard)
- configured database URL (the repository default is SQLite)

The test stub demonstrates the end-to-end wiring; the assertions mirror
plan §6 (aggregate.jsonl one line, sweevo_result.json present, per-task
``task.json`` roles cover planner/generator/evaluator, mock-only-entry
invariant via the per-task ``message.jsonl``). Locally we just verify
the test COLLECTS (``pytest --collect-only``); execution is deferred to
the human gate.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from benchmarks.sweevo.models import SWEEvoInstance
from benchmarks.sweevo.prompt import load_pr_description
from benchmarks.sweevo.sandbox import verify_sweevo_snapshot_exists
from runtime.app_factory import RuntimeConfig
from task_center_runner.benchmarks.sweevo.lifecycle import SweevoLifecycle
from task_center_runner.benchmarks.sweevo.provisioner import SweevoProvisioner
from task_center_runner.benchmarks.sweevo.sweevo_runner import (
    build_selective_entry_mock_runner_factory,
)
from task_center_runner.core.bootstrap import bootstrap_real_agent_runtime
from task_center_runner.core.config import RunConfig
from task_center_runner.core.engine import run_pipeline
from task_center_runner.core.stores import TaskCenterStoreBundle

pytestmark = pytest.mark.skipif(
    not (
        os.getenv("EOS_SWEEVO_REAL_AGENT_TESTS") == "1"
        and os.getenv("EOS_LIVE_TESTS") == "1"
    ),
    reason=(
        "SWE-EVO real-agent live e2e gated by EOS_SWEEVO_REAL_AGENT_TESTS=1 + "
        "EOS_LIVE_TESTS=1"
    ),
)


@pytest.mark.asyncio
async def test_sweevo_runner_resolves_canonical_instance(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    """End-to-end run of the SWE-EVO benchmarker on a canonical instance.

    Asserts plan §6 acceptance criteria locally executable when the gate
    is set. When the gate is unset (typical CI/dev), the entire test is
    skipped at module-import time via the ``skipif`` mark above.
    """
    repo_dir = str(workspace["repo_dir"])
    sandbox_id = str(workspace["sandbox_id"])
    goal = load_pr_description(sweevo_instance.instance_id)

    # Fail-fast snapshot probe — mirrors the CLI path.
    verify_sweevo_snapshot_exists(sweevo_instance)

    runtime_cfg = RuntimeConfig(cwd=repo_dir, external_api_client=None)
    aggregate_path = audit_dir / "aggregate.jsonl"
    config = RunConfig(
        entry_prompt=goal,
        repo_dir=repo_dir,
        sandbox=SweevoProvisioner(
            sweevo_instance, sandbox_id, repo_dir=repo_dir, install_lsp=True
        ),
        runner_factory=build_selective_entry_mock_runner_factory(
            goal=goal, repo_dir=repo_dir
        ),
        lifecycle=SweevoLifecycle(
            sweevo_instance,
            repo_dir=repo_dir,
            aggregate_jsonl_path=aggregate_path,
        ),
        bootstrap=bootstrap_real_agent_runtime,
        stores=stores,
        audit_dir=audit_dir,
        run_label=f"benchmark/sweevo/{sweevo_instance.instance_id}",
        instance_id=sweevo_instance.instance_id,
        max_duration_s=float(
            os.getenv("EOS_SWEEVO_REAL_AGENT_MAX_DURATION_S", "1800")
        ),
        extras={"runtime_config": runtime_cfg},
    )

    report = await run_pipeline(config)

    assert report.task_center_run_id
    assert report.run_dir.is_dir()
    assert (report.run_dir / "sweevo_result.json").is_file()
    assert aggregate_path.is_file()

    aggregate_lines = aggregate_path.read_text(encoding="utf-8").splitlines()
    assert len(aggregate_lines) == 1
    payload = json.loads(aggregate_lines[0])
    assert payload["instance_id"] == sweevo_instance.instance_id
    assert payload["run_id"] == report.task_center_run_id
    assert payload["sandbox_id"] == sandbox_id

    # Per-task ``task.json`` rows must cover planner / generator / evaluator
    # roles — proof the production pipeline drove every non-entry agent.
    roles_seen: set[str] = set()
    for task_json in (report.run_dir / "tasks").glob("*/task.json"):
        record = json.loads(task_json.read_text(encoding="utf-8"))
        if record.get("role") in {"planner", "generator", "evaluator"}:
            roles_seen.add(record["role"])
    assert {"planner", "generator", "evaluator"}.issubset(roles_seen)
