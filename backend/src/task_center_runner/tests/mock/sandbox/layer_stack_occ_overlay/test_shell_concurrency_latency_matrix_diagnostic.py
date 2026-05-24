"""Diagnostic shell latency matrix for concurrency 1/5/10.

Skipped by default so the 3.1 gate does not pay this probe unless explicitly
requested with ``EOS_RUN_SHELL_LATENCY_MATRIX=1``.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import pytest

import sandbox.api as sandbox_api
from sandbox.api import SandboxCaller, ShellRequest
from sandbox._shared.clock import monotonic_now


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("EOS_RUN_SHELL_LATENCY_MATRIX") != "1",
        reason="shell latency matrix is an explicit diagnostic",
    ),
]

_LEVELS = (1, 5, 10)
_ROOT = "/testbed/.ephemeralos/sweevo-mock/shell_concurrency_latency_matrix"
_ARTIFACT_DIR = Path(".sweevo_runs/manual_diagnostics/shell_concurrency_latency")
_TIMING_KEYS = (
    "api.shell.dispatch_total_s",
    "api.shell.total_s",
    "command_exec.total_s",
    "command_exec.mount_workspace_s",
    "command_exec.run_command_s",
    "command_exec.capture_upperdir_s",
    "command_exec.occ_apply_s",
    "layer_stack.prepare_workspace_snapshot.total_s",
    "layer_stack.transaction.lock_wait_s",
    "occ.apply.commit_queue_wait_s",
    "occ.apply.total_s",
    "runtime.dispatch_s",
    "runtime.read_request_s",
    "resource.layer_stack.manifest_depth",
)


@pytest.mark.timeout(900)
async def test_shell_concurrency_latency_matrix(workspace: dict[str, object]) -> None:
    sandbox_id = str(workspace["sandbox_id"])
    groups: list[dict[str, Any]] = []
    for level in _LEVELS:
        wall_start = monotonic_now()
        results = await asyncio.gather(
            *(_run_shell(sandbox_id, level, index) for index in range(level))
        )
        wall_s = monotonic_now() - wall_start
        groups.append(_summarize_group(level, wall_s, results))

    payload = {
        "schema": "task_center_runner.shell_concurrency_latency_matrix.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "sandbox_id": sandbox_id,
        "levels": list(_LEVELS),
        "groups": groups,
    }
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = _ARTIFACT_DIR / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SHELL_LATENCY_MATRIX_ARTIFACT={path.as_posix()}")
    print(json.dumps(payload, sort_keys=True))

    assert all(
        sample["success"] and sample["exit_code"] == 0
        for group in groups
        for sample in group["samples"]
    )


async def _run_shell(
    sandbox_id: str,
    level: int,
    index: int,
) -> dict[str, Any]:
    caller = SandboxCaller(
        agent_id=f"shell-latency-c{level}-{index}",
        agent_run_id=f"shell-latency-c{level}-{index}",
        tool_name="shell",
        tool_id=f"diagnostic-c{level}-{index}",
    )
    command = (
        f"mkdir -p {_ROOT}/c{level} && "
        f"printf 'level={level}\\nworker={index}\\n' > {_ROOT}/c{level}/worker-{index:02d}.txt && "
        f"cat {_ROOT}/c{level}/worker-{index:02d}.txt"
    )
    wall_start = monotonic_now()
    result = await sandbox_api.shell(
        sandbox_id,
        ShellRequest(
            command=command,
            cwd="/testbed",
            timeout=120,
            caller=caller,
            description=f"shell latency diagnostic concurrency={level}",
        ),
    )
    wall_s = monotonic_now() - wall_start
    return {
        "index": index,
        "success": bool(result.success),
        "status": result.status,
        "exit_code": result.exit_code,
        "wall_s": wall_s,
        "changed_paths": list(result.changed_paths),
        "timings": {key: float(value) for key, value in result.timings.items()},
    }


def _summarize_group(
    level: int,
    wall_s: float,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "concurrency": level,
        "wall_s": wall_s,
        "sample_count": len(samples),
        "timing_summary": {
            key: _stats(
                sample["timings"][key]
                for sample in samples
                if key in sample["timings"]
            )
            for key in _TIMING_KEYS
        },
        "samples": samples,
    }


def _stats(values_iter: Any) -> dict[str, float | int | None]:
    values = sorted(float(value) for value in values_iter)
    if not values:
        return {"count": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "min": values[0],
        "mean": sum(values) / len(values),
        "p50": median(values),
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
        "max": values[-1],
    }
