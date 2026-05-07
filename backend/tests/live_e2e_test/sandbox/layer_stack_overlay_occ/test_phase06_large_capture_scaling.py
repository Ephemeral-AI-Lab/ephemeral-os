"""Phase 06 — K-scaling benchmark for shell large captures.

Diagnostic only: emits per-call timings at K ∈ {1, 100, 1000, 10000} for both a
tracked prefix and a gitignored prefix. Phase 2.2 reads the artifact and selects
the optimisation lane (Lane A/B/C from the plan §5).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import pytest

from .._harness.integrated_cases import emit_metric, timed_call
from .._harness.large_capture_workload import build_k_capture_command
from .._harness.phase05_public_file_ops import seed_phase05_imported_base
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio

_PREFIXES = ("tracked/load/k_capture", "dist/k_capture")
_K_VALUES = (1, 100, 1000, 10_000)


def _artifact_path() -> Path:
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{os.getpid()}"
    )
    target = Path.cwd() / ".omc" / "results" / (
        f"phase06-large-capture-scaling-{run_id}.jsonl"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _slug(prefix: str) -> str:
    return prefix.replace("/", "_").replace(".", "")


def _row_for_cell(
    *,
    prefix: str,
    k: int,
    timings: Mapping[str, float],
    wall_ms: float,
) -> dict[str, object]:
    capture_s = float(timings.get("command_exec.capture_upperdir_s", 0.0))
    occ_apply_s = float(timings.get("command_exec.occ_apply_s", 0.0))
    commit_s = float(timings.get("occ.commit.total_s", 0.0))
    validate_groups_s = float(timings.get("occ.commit.validate_groups_s", 0.0))
    publish_layer_s = float(timings.get("occ.commit.publish_layer_s", 0.0))
    stager_write_total_s = float(
        timings.get("occ.commit.stager_write_total_s", 0.0)
    )
    stager_write_count = float(timings.get("occ.commit.stager_write_count", 0.0))
    prepare_groups_s = float(timings.get("occ.prepare.prepare_groups_s", 0.0))
    group_by_route_s = float(timings.get("occ.prepare.group_by_route_s", 0.0))
    return {
        "schema": "phase06.large_capture_scaling.v1",
        "prefix": prefix,
        "k": k,
        "wall_ms": round(wall_ms, 3),
        "capture_upperdir_s": round(capture_s, 6),
        "occ_apply_s": round(occ_apply_s, 6),
        "commit_s": round(commit_s, 6),
        "validate_groups_s": round(validate_groups_s, 6),
        "publish_layer_s": round(publish_layer_s, 6),
        "stager_write_total_s": round(stager_write_total_s, 6),
        "stager_write_count": stager_write_count,
        "occ_prepare_groups_s": round(prepare_groups_s, 6),
        "occ_group_by_route_s": round(group_by_route_s, 6),
        "commit_per_file_us": round(commit_s * 1_000_000.0 / max(k, 1), 3),
        "capture_per_file_us": round(capture_s * 1_000_000.0 / max(k, 1), 3),
        "stager_per_file_us": round(
            stager_write_total_s * 1_000_000.0 / max(k, 1), 3
        ),
    }


async def test_phase06_large_capture_k_scaling(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    await seed_phase05_imported_base(handle)

    artifact = _artifact_path()
    rows: list[dict[str, object]] = []

    for prefix in _PREFIXES:
        for k in _K_VALUES:
            command = build_k_capture_command(prefix=prefix, k=k)
            label = f"phase06.large_capture.{_slug(prefix)}.k{k}"
            result, metric = await timed_call(
                label,
                handle.tool.shell(
                    command,
                    timeout=300,
                    description=f"k_capture prefix={prefix} k={k}",
                ),
            )
            assert result.success, f"shell failed for {prefix} k={k}: {result}"
            row = _row_for_cell(
                prefix=prefix,
                k=k,
                timings=metric.timings,
                wall_ms=metric.elapsed_ms,
            )
            rows.append(row)
            emit_metric(label, row)

    with artifact.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            fh.write("\n")

    print(f"\n[phase06:large_capture_scaling] artifact={artifact}")
    emit_metric(
        "phase06.large_capture_scaling.summary",
        {"artifact": str(artifact), "rows": len(rows)},
    )
    assert len(rows) == len(_PREFIXES) * len(_K_VALUES)
