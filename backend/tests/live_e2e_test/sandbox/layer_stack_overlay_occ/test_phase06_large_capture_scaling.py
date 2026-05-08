"""Phase 06 — K-scaling benchmark for shell large captures.

Emits per-call timings across K ∈ {1, 100, 1000, 10000} on both a tracked
prefix and a gitignored prefix so that any future K-scaling regression is
visible in `commit_per_file_us`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from .._harness.integrated_cases import emit_metric, timed_call
from .._harness.large_capture_workload import build_k_capture_command
from .._harness.phase05_public_file_ops import seed_phase05_imported_base
from .._harness.sandbox_fixture import SandboxHandle
from .._harness.streaming_artifact import (
    load_prior_data_rows as _load_prior_data_rows,
    resolve_run_id as _resolve_run_id,
    rewrite_artifact as _rewrite_artifact,
    stream_row as _stream_row,
)


pytestmark = pytest.mark.asyncio

_PREFIXES = ("tracked/load/k_capture", "dist/k_capture")
_K_VALUES = (1, 100, 1000, 10_000)
_K1000_SPOT_VALUES = (1000,)


def _artifact_path(label: str = "phase06-large-capture-scaling") -> Path:
    target = Path.cwd() / ".omc" / "results" / f"{label}-{_resolve_run_id()}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _slug(prefix: str) -> str:
    return prefix.replace("/", "_").replace(".", "")


def _cell_id(prefix: str, k: int) -> str:
    return f"k_capture:{_slug(prefix)}:k{k}"


def _completed_cell_ids(rows: list[dict[str, object]]) -> set[str]:
    return {
        str(row["cell_id"])
        for row in rows
        if row.get("passed") is True and row.get("cell_id")
    }


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
    gated_read_s = float(timings.get("occ.commit.gated_read_current_total_s", 0.0))
    gated_apply_s = float(
        timings.get("occ.commit.gated_apply_changes_total_s", 0.0)
    )
    gated_stage_s = float(timings.get("occ.commit.gated_stage_delta_total_s", 0.0))
    gated_count = float(timings.get("occ.commit.gated_path_count", 0.0))
    direct_read_s = float(timings.get("occ.commit.direct_read_current_total_s", 0.0))
    direct_apply_s = float(
        timings.get("occ.commit.direct_apply_changes_total_s", 0.0)
    )
    direct_stage_s = float(timings.get("occ.commit.direct_stage_delta_total_s", 0.0))
    direct_count = float(timings.get("occ.commit.direct_path_count", 0.0))
    return {
        "schema": "phase06.large_capture_scaling.v2",
        "cell_id": _cell_id(prefix, k),
        "passed": True,
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
        "gated_read_current_total_s": round(gated_read_s, 6),
        "gated_apply_changes_total_s": round(gated_apply_s, 6),
        "gated_stage_delta_total_s": round(gated_stage_s, 6),
        "gated_path_count": gated_count,
        "direct_read_current_total_s": round(direct_read_s, 6),
        "direct_apply_changes_total_s": round(direct_apply_s, 6),
        "direct_stage_delta_total_s": round(direct_stage_s, 6),
        "direct_path_count": direct_count,
        "commit_per_file_us": round(commit_s * 1_000_000.0 / max(k, 1), 3),
        "capture_per_file_us": round(capture_s * 1_000_000.0 / max(k, 1), 3),
        "stager_per_file_us": round(
            stager_write_total_s * 1_000_000.0 / max(k, 1), 3
        ),
    }


async def _run_single_cell(
    handle: SandboxHandle,
    *,
    prefix: str,
    k: int,
) -> dict[str, object]:
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
    emit_metric(label, row)
    return row


async def test_phase06_large_capture_k_scaling(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    await seed_phase05_imported_base(handle)

    artifact = _artifact_path()
    rows = _load_prior_data_rows(artifact)
    completed = _completed_cell_ids(rows)
    skipped_resume = 0

    for prefix in _PREFIXES:
        for k in _K_VALUES:
            if _cell_id(prefix, k) in completed:
                skipped_resume += 1
                continue
            row = await _run_single_cell(handle, prefix=prefix, k=k)
            rows.append(row)
            _stream_row(artifact, row)

    summary = {
        "schema": "phase06.large_capture_scaling.summary.v1",
        "artifact": str(artifact),
        "rows": len(rows),
        "skipped_resume": skipped_resume,
    }
    _rewrite_artifact(artifact, rows, summary)

    print(f"\n[phase06:large_capture_scaling] artifact={artifact}")
    emit_metric("phase06.large_capture_scaling.summary", summary)
    assert len(rows) == len(_PREFIXES) * len(_K_VALUES)


async def test_phase06_large_capture_k1000_spot_check(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    """Tier 2 spot check: exactly tracked×K=1000 and dist×K=1000."""
    handle = workspace_base_sandbox
    await seed_phase05_imported_base(handle)

    artifact = _artifact_path("phase06-k1000-spot-check")
    rows = _load_prior_data_rows(artifact)
    completed = _completed_cell_ids(rows)
    skipped_resume = 0

    for prefix in _PREFIXES:
        for k in _K1000_SPOT_VALUES:
            if _cell_id(prefix, k) in completed:
                skipped_resume += 1
                continue
            row = await _run_single_cell(handle, prefix=prefix, k=k)
            rows.append(row)
            _stream_row(artifact, row)

    summary = {
        "schema": "phase06.k1000_spot_check.summary.v1",
        "artifact": str(artifact),
        "rows": len(rows),
        "skipped_resume": skipped_resume,
    }
    _rewrite_artifact(artifact, rows, summary)

    print(f"\n[phase06:k1000_spot_check] artifact={artifact}")
    emit_metric("phase06.k1000_spot_check.summary", summary)
    assert len(rows) == len(_PREFIXES) * len(_K1000_SPOT_VALUES)


def _isolated_k10000_artifact(label_slug: str) -> Path:
    base = _artifact_path()
    return base.with_name(
        base.name.replace(
            "phase06-large-capture-scaling-",
            f"phase06-large-capture-{label_slug}-",
        )
    )


async def _isolated_k10000_cell(
    handle: SandboxHandle,
    *,
    prefix: str,
    label_slug: str,
) -> None:
    await seed_phase05_imported_base(handle)
    artifact = _isolated_k10000_artifact(label_slug)
    row = await _run_single_cell(handle, prefix=prefix, k=10_000)
    _stream_row(artifact, row)
    print(f"\n[phase06:{label_slug}] artifact={artifact}")


async def test_phase06_large_capture_tracked_k10000(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    await _isolated_k10000_cell(
        workspace_base_sandbox,
        prefix="tracked/load/k_capture",
        label_slug="tracked-k10000",
    )


async def test_phase06_large_capture_dist_k10000(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    await _isolated_k10000_cell(
        workspace_base_sandbox,
        prefix="dist/k_capture",
        label_slug="dist-k10000",
    )
