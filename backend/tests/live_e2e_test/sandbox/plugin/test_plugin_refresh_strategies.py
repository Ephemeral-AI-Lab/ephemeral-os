"""Live plugin refresh strategy coverage.

This reuses the existing sandbox fixture to obtain a running Docker container,
then delegates the detailed refresh/materialization/autosquash probes to
``backend/scripts/bench_plugin_refresh_strategies.py``. The benchmark writes all
experiment state under ``/eos/plugin/*``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .._harness.sandbox_fixture import SandboxHandle
from .._harness.streaming_artifact import resolve_run_id


pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[5]
BENCH = ROOT / "backend" / "scripts" / "bench_plugin_refresh_strategies.py"


async def test_plugin_workspace_snapshot_refresh_strategy(
    integrated_sandbox: SandboxHandle,
) -> None:
    provider = os.environ.get("EOS_SANDBOX_PROVIDER", "docker").strip() or "docker"
    if provider != "docker":
        pytest.skip("plugin refresh strategy benchmark currently targets Docker containers")

    run_id = resolve_run_id()
    result_dir = ROOT / ".omc" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    report = result_dir / f"plugin-refresh-strategies-{run_id}.json"
    markdown_report = result_dir / f"plugin-refresh-strategies-{run_id}.md"
    samples = os.environ.get("EOS_PLUGIN_REFRESH_SAMPLES", "1")
    auto_squash_writes = os.environ.get("EOS_PLUGIN_REFRESH_AUTO_SQUASH_WRITES", "104")

    cmd = [
        sys.executable,
        str(BENCH),
        "--container-id",
        integrated_sandbox.sandbox_id,
        "--samples",
        samples,
        "--auto-squash-writes",
        auto_squash_writes,
        "--report",
        str(report),
        "--markdown-report",
        str(markdown_report),
    ]
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=int(os.environ.get("EOS_PLUGIN_REFRESH_TIMEOUT_S", "420")),
        check=False,
    )
    assert completed.returncode == 0, (
        "plugin refresh benchmark failed\n"
        f"cmd={' '.join(cmd)}\n"
        f"stdout={completed.stdout[-4000:]}\n"
        f"stderr={completed.stderr[-4000:]}"
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["recommendation"]["winner"] == "workspace_snapshot_refresh"
    assert payload["workspace_snapshot_refresh"]["all_samples_ok"] is True
    assert payload["fs_watch_without_materialization"]["raw_workspace_stale"] is True
    assert payload["auto_squash_then_commit"]["gate_pass"] is True
    assert payload["final_metrics"]["orphan_layer_count"] == 0
    assert payload["final_metrics"]["missing_layer_count"] == 0
