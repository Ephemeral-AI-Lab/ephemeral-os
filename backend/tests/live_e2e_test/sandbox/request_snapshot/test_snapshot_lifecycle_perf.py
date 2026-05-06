"""Phase 0 request snapshot lifecycle performance probes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from .._harness.request_snapshot_probe import (
    configured_backends,
    configured_concurrencies,
    configured_timeout,
    configured_workspace_shapes,
    emit_request_snapshot_metrics,
    parse_request_snapshot_payload,
    request_snapshot_probe_command,
    viable_backend_rows,
    write_request_snapshot_jsonl,
)
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio


async def _run_probe(
    handle: SandboxHandle,
    *,
    scenario: str,
    workspace_shapes: Sequence[str],
    concurrencies: Sequence[int],
    viable_only: bool = False,
) -> dict[str, Any]:
    command = request_snapshot_probe_command(
        scenario=scenario,
        source_root=handle.workspace_root,
        workspace_shapes=workspace_shapes,
        backends=configured_backends(),
        concurrencies=concurrencies,
        viable_only=viable_only,
    )
    result = await handle.raw_exec(
        handle.sandbox_id,
        command,
        timeout=configured_timeout(),
    )
    assert result.exit_code == 0, (
        f"request snapshot probe failed (rc={result.exit_code}): "
        f"{result.stderr or result.stdout}"
    )
    payload = parse_request_snapshot_payload(result.stdout)
    emit_request_snapshot_metrics(payload)
    write_request_snapshot_jsonl(payload)
    return payload


def _available_non_diagnostic_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in payload.get("rows", [])
        if row.get("backend") != "hardlink_cp" and row.get("available")
    ]


def _assert_no_hard_bar_failures(payload: dict[str, Any]) -> None:
    for row in payload.get("rows", []):
        assert row["schema"] == "sandbox.live_e2e.request_snapshot_probe.v1"
        assert row["snapshot_paths_unique"], row
        assert row["leftover_snapshot_dirs"] == 0, row
        if row["available"]:
            assert row["create_batch_wall_ms"] > 0.0, row
            assert row["create_per_call_p50_ms"] > 0.0, row
            assert row["destroy_batch_wall_ms"] > 0.0, row
            assert row["destroy_per_call_p50_ms"] > 0.0, row


async def test_snapshot_backend_capabilities(
    overlay_sandbox: SandboxHandle,
) -> None:
    payload = await _run_probe(
        overlay_sandbox,
        scenario="request_snapshot_capabilities",
        workspace_shapes=("baseline_repo",),
        concurrencies=(1,),
    )
    _assert_no_hard_bar_failures(payload)

    viable_rows = viable_backend_rows(payload)
    assert viable_rows, payload

    for row in _available_non_diagnostic_rows(payload):
        assert row["freeze_ok"], row
        assert row["viable"], row

    for row in payload.get("rows", []):
        if row["backend"] == "hardlink_cp" and not row["freeze_ok"]:
            assert not row["viable"], row


async def test_snapshot_create_destroy_latency_profiles(
    overlay_sandbox: SandboxHandle,
) -> None:
    shapes = configured_workspace_shapes()
    payload = await _run_probe(
        overlay_sandbox,
        scenario="request_snapshot_latency_profiles",
        workspace_shapes=shapes,
        concurrencies=(1,),
    )
    _assert_no_hard_bar_failures(payload)

    seen_shapes = {row["workspace_shape"] for row in payload.get("rows", [])}
    assert set(shapes).issubset(seen_shapes), payload

    rows_by_shape: dict[str, list[dict[str, Any]]] = {shape: [] for shape in shapes}
    for row in payload.get("rows", []):
        rows_by_shape.setdefault(row["workspace_shape"], []).append(row)
    for shape, rows in rows_by_shape.items():
        assert rows, shape
        assert any(row.get("viable") for row in rows), rows

    for row in _available_non_diagnostic_rows(payload):
        assert row["freeze_ok"], row
        assert row["workspace_files"] > 0, row
        assert row["workspace_bytes"] >= 0, row


async def test_concurrent_snapshot_creation_parallel_factor_1_5_10(
    overlay_sandbox: SandboxHandle,
) -> None:
    concurrencies = configured_concurrencies()
    payload = await _run_probe(
        overlay_sandbox,
        scenario="request_snapshot_concurrency",
        workspace_shapes=("baseline_repo",),
        concurrencies=concurrencies,
        viable_only=True,
    )
    _assert_no_hard_bar_failures(payload)

    viable_rows = viable_backend_rows(payload)
    assert viable_rows, payload

    seen_concurrency = {row["concurrency"] for row in viable_rows}
    assert set(concurrencies).issubset(seen_concurrency), payload

    for row in viable_rows:
        assert row["parallel_factor_create"] > 0.0, row
        assert row["parallel_factor_destroy"] > 0.0, row
        assert row["parallel_efficiency_create"] > 0.0, row
        assert row["parallel_efficiency_destroy"] > 0.0, row
        assert row["freeze_ok"], row
        assert row["leftover_snapshot_dirs"] == 0, row
