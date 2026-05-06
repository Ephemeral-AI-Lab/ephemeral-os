"""Phase-01 live concurrency coverage for workspace-base builds."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

import pytest

from .._harness.sandbox_fixture import SandboxHandle, WORKSPACE_ROOT
from .._harness.workspace_base_metrics import (
    base_summary,
    call_row,
    env_int,
    monotonic_ms,
    percentile,
    phase01_stack_root,
    reset_layer_stack_root,
    runtime_call,
    workspace_inventory,
    write_jsonl_artifact,
)


pytestmark = pytest.mark.asyncio


async def test_20_independent_base_builds_converge_on_one_base_hash(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    case = "base_import_concurrency_independent"
    concurrency = env_int("EPHEMERALOS_PHASE01_BASE_IMPORT_CONCURRENCY", 20)
    inventory = await workspace_inventory(handle)
    roots = [
        phase01_stack_root("base-import-concurrency-independent", f"{index:02d}")
        for index in range(concurrency)
    ]
    for root in roots:
        await reset_layer_stack_root(handle, root)

    async def build_one(index: int, root: str) -> dict[str, Any]:
        started = monotonic_ms()
        result = await runtime_call(
            handle,
            "api.build_workspace_base",
            {"workspace_root": WORKSPACE_ROOT},
            layer_stack_root=root,
            timeout=300,
        )
        wall_ms = monotonic_ms() - started
        return {"index": index, "root": root, "result": result, "wall_ms": wall_ms}

    batch_start = monotonic_ms()
    outcomes = await asyncio.gather(
        *(build_one(index, root) for index, root in enumerate(roots))
    )
    batch_wall_ms = monotonic_ms() - batch_start

    rows: list[dict[str, object]] = []
    base_hashes: list[str] = []
    wall_values: list[float] = []
    first_binding: dict[str, object] | None = None
    for outcome in outcomes:
        result = outcome["result"]
        binding = result["binding"]
        assert isinstance(binding, dict)
        timings = {
            str(key): float(value)
            for key, value in dict(result.get("timings") or {}).items()
        }
        base_hashes.append(str(binding["base_root_hash"]))
        wall_values.append(float(outcome["wall_ms"]))
        first_binding = first_binding or binding
        assert binding["base_manifest_version"] == 1
        assert binding["active_manifest_version"] == 1
        assert binding["base_root_hash"] == binding["active_root_hash"]
        rows.append(
            call_row(
                case=case,
                label=f"independent_{int(outcome['index']):02d}",
                success=True,
                wall_ms=float(outcome["wall_ms"]),
                runtime_ms=timings.get("api.workspace_base.total_s", 0.0) * 1000.0,
                timings=timings,
                extra={"layer_stack_root": outcome["root"]},
            )
        )

    assert len(base_hashes) == concurrency
    assert len(set(base_hashes)) == 1
    assert first_binding is not None
    artifact = write_jsonl_artifact(
        case=case,
        summary=base_summary(
            case=case,
            binding=first_binding,
            workspace_inventory=inventory,
            timings={
                "phase01.concurrency.batch_wall_s": batch_wall_ms / 1000.0,
                "phase01.concurrency.wall_p50_s": percentile(wall_values, 50) / 1000.0,
                "phase01.concurrency.wall_p99_s": percentile(wall_values, 99) / 1000.0,
                "phase01.concurrency.wall_max_s": max(wall_values) / 1000.0,
            },
            pass_bars={
                "independent_builds": concurrency,
                "matching_base_hashes": True,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase01:{case}] artifact={artifact}")


async def test_same_root_concurrent_builds_fail_closed_without_partial_base(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    case = "base_import_concurrency_same_root"
    concurrency = env_int("EPHEMERALOS_PHASE01_BASE_IMPORT_CONCURRENCY", 20)
    inventory = await workspace_inventory(handle)
    root = phase01_stack_root("base-import-concurrency-same-root")
    await reset_layer_stack_root(handle, root)

    async def build_one(index: int) -> dict[str, Any]:
        started = monotonic_ms()
        try:
            result = await runtime_call(
                handle,
                "api.build_workspace_base",
                {"workspace_root": WORKSPACE_ROOT},
                layer_stack_root=root,
                timeout=300,
            )
            return {
                "index": index,
                "success": True,
                "result": result,
                "wall_ms": monotonic_ms() - started,
            }
        except Exception as exc:  # Runtime dispatch errors are typed dynamically.
            return {
                "index": index,
                "success": False,
                "kind": getattr(exc, "kind", type(exc).__name__),
                "message": str(exc),
                "wall_ms": monotonic_ms() - started,
            }

    batch_start = monotonic_ms()
    outcomes = await asyncio.gather(*(build_one(index) for index in range(concurrency)))
    batch_wall_ms = monotonic_ms() - batch_start
    successes = [outcome for outcome in outcomes if outcome["success"]]
    failures = [outcome for outcome in outcomes if not outcome["success"]]

    assert successes, outcomes
    assert len(successes) in {1, concurrency}, outcomes
    if failures:
        assert len(successes) == 1, outcomes
        assert all(_is_existing_base_error(outcome) for outcome in failures), outcomes

    binding_result = await runtime_call(
        handle,
        "api.workspace_binding",
        {"actor_id": handle.caller.agent_id},
        layer_stack_root=root,
        timeout=60,
    )
    binding = binding_result["binding"]
    assert isinstance(binding, dict)
    metrics = await runtime_call(
        handle,
        "api.layer_metrics",
        {"actor_id": handle.caller.agent_id},
        layer_stack_root=root,
        timeout=60,
    )
    assert metrics["manifest_version"] == 1
    assert metrics["manifest_depth"] == 1
    assert metrics["workspace_bound"] is True
    assert binding["workspace_root"] == WORKSPACE_ROOT
    assert binding["base_manifest_version"] == 1

    staging = await handle.raw_exec(
        handle.sandbox_id,
        f"find {shlex.quote(root)}/staging -mindepth 1 -maxdepth 1 -print",
        timeout=30,
    )
    assert staging.exit_code == 0, staging.stderr or staging.stdout
    assert staging.stdout.strip() == ""

    rows = [
        call_row(
            case=case,
            label=f"same_root_{int(outcome['index']):02d}",
            success=bool(outcome["success"]),
            wall_ms=float(outcome["wall_ms"]),
            extra={
                "error_kind": outcome.get("kind", ""),
                "error_message": outcome.get("message", ""),
            },
        )
        for outcome in outcomes
    ]
    artifact = write_jsonl_artifact(
        case=case,
        summary=base_summary(
            case=case,
            binding=binding,
            workspace_inventory=inventory,
            timings={
                "phase01.concurrency.batch_wall_s": batch_wall_ms / 1000.0,
                "phase01.concurrency.successes": float(len(successes)),
                "phase01.concurrency.failures": float(len(failures)),
            },
            pass_bars={
                "same_root_callers": concurrency,
                "fail_closed_or_converged": True,
                "orphan_staging_dirs": 0,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase01:{case}] artifact={artifact}")


def _is_existing_base_error(outcome: dict[str, Any]) -> bool:
    blob = f"{outcome.get('kind', '')} {outcome.get('message', '')}".lower()
    return (
        "already" in blob
        or "not empty" in blob
        or "existing" in blob
        or "workspace base" in blob
    )
