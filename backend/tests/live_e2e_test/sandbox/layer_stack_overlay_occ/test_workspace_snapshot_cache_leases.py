"""Live public-runtime coverage for materialized lowerdir cache leases."""

from __future__ import annotations

import asyncio
import statistics

import pytest

from .._harness.integrated_cases import token
from .._harness.sandbox_fixture import SandboxHandle
from .._harness.snapshot_cache_metrics import (
    api_prepare_ms,
    df_available_kb,
    env_int,
    lowerdir_cache_int,
    lowerdir_file_content,
    materialize_ms,
    materialized_cache_bytes,
    materialized_dir_count,
    path_exists,
    path_is_dir,
    prepare_call_row,
    prepare_snapshot,
    release_call_row,
    release_snapshot,
    runtime_layer_metrics,
    summary_record,
    write_jsonl_artifact,
    write_large_public_file,
    write_public_file,
)


pytestmark = pytest.mark.asyncio


async def test_latest_snapshot_cache_reuses_and_manifest_advance_does_not_evict(
    integrated_sandbox: SandboxHandle,
) -> None:
    handle = integrated_sandbox
    case = "reuse_and_manifest_advance_non_eviction"
    path = "tracked/cache-reuse.txt"
    rows: list[dict[str, object]] = []

    await write_public_file(
        handle,
        path,
        "v1\n",
        description="phase02 cache reuse seed",
    )
    initial_count = await materialized_dir_count(handle)

    first = await prepare_snapshot(handle, "prepare_a", request_id=token("lease-a"))
    rows.append(prepare_call_row(case, first))
    second = await prepare_snapshot(handle, "prepare_b", request_id=token("lease-b"))
    rows.append(prepare_call_row(case, second))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.cache_dir_count_after == initial_count + 1
    assert second.cache_dir_count_after == first.cache_dir_count_after
    assert second.manifest_version == first.manifest_version
    assert second.root_hash == first.root_hash
    assert second.lowerdir == first.lowerdir
    assert await path_is_dir(handle, first.lowerdir)
    assert await lowerdir_file_content(handle, first.lowerdir, path) == "v1\n"

    metrics_after_prepare = await runtime_layer_metrics(handle)
    assert int(metrics_after_prepare["active_leases"]) == 2, metrics_after_prepare
    assert int(metrics_after_prepare["pinned_lowerdirs"]) == 1, metrics_after_prepare
    assert int(metrics_after_prepare["materialized_lowerdirs"]) == 1, metrics_after_prepare
    assert lowerdir_cache_int(metrics_after_prepare, "lowerdir_cache_misses") == 1
    assert lowerdir_cache_int(metrics_after_prepare, "lowerdir_cache_hits") == 1

    released = await release_snapshot(handle, first)
    rows.append(release_call_row(case, "release_a", released))
    assert await path_is_dir(handle, first.lowerdir)

    released = await release_snapshot(handle, second)
    rows.append(release_call_row(case, "release_b", released))
    assert await path_is_dir(handle, first.lowerdir)

    third = await prepare_snapshot(handle, "prepare_c", request_id=token("lease-c"))
    rows.append(prepare_call_row(case, third))
    assert third.cache_hit is True
    assert third.lowerdir == first.lowerdir
    released = await release_snapshot(handle, third)
    rows.append(release_call_row(case, "release_c", released))

    count_before_advance = await materialized_dir_count(handle)
    await write_public_file(
        handle,
        path,
        "v2\n",
        description="phase02 cache reuse manifest advance",
    )
    assert await path_is_dir(handle, first.lowerdir)
    assert await materialized_dir_count(handle) == count_before_advance

    final_metrics = await runtime_layer_metrics(handle)
    assert int(final_metrics["active_leases"]) == 0, final_metrics
    assert int(final_metrics["pinned_lowerdirs"]) == 0, final_metrics

    artifact = write_jsonl_artifact(
        case=case,
        summary=summary_record(
            case=case,
            metrics=final_metrics,
            root_hash=first.root_hash,
            cache_creation={
                "created_lowerdirs": 1,
                "reused_lowerdirs": 2,
                "unexpected_extra_lowerdirs": 0,
            },
            pass_bars={
                "first_prepare_created_one_lowerdir": True,
                "same_manifest_reused_lowerdir": True,
                "latest_unleased_lowerdir_remained_reusable": True,
                "manifest_advance_did_not_evict_stale_unleased_lowerdir": True,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase02:{case}] artifact={artifact}")


async def test_stale_snapshot_cache_evicts_on_final_release(
    integrated_sandbox: SandboxHandle,
) -> None:
    handle = integrated_sandbox
    case = "stale_final_lease_eviction"
    path = "tracked/cache-stale.txt"
    rows: list[dict[str, object]] = []

    await write_public_file(handle, path, "v1\n", description="phase02 stale seed")
    snapshot = await prepare_snapshot(handle, "prepare_stale", request_id=token("stale"))
    rows.append(prepare_call_row(case, snapshot))

    await write_public_file(
        handle,
        path,
        "v2\n",
        description="phase02 stale manifest advance while leased",
    )
    assert await path_is_dir(handle, snapshot.lowerdir)

    metrics_while_pinned = await runtime_layer_metrics(handle)
    assert int(metrics_while_pinned["active_leases"]) == 1, metrics_while_pinned
    assert int(metrics_while_pinned["pinned_lowerdirs"]) == 1, metrics_while_pinned

    released = await release_snapshot(handle, snapshot)
    rows.append(release_call_row(case, "release_stale", released))
    assert not await path_exists(handle, snapshot.lowerdir)

    final_metrics = await runtime_layer_metrics(handle)
    assert int(final_metrics["active_leases"]) == 0, final_metrics
    assert int(final_metrics["pinned_lowerdirs"]) == 0, final_metrics

    artifact = write_jsonl_artifact(
        case=case,
        summary=summary_record(
            case=case,
            metrics=final_metrics,
            root_hash=snapshot.root_hash,
            cache_creation={
                "created_lowerdirs": 1,
                "reused_lowerdirs": 0,
                "unexpected_extra_lowerdirs": 0,
            },
            pass_bars={
                "stale_lowerdir_pinned_through_manifest_change": True,
                "final_stale_release_evicted_lowerdir": True,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase02:{case}] artifact={artifact}")


async def test_prepare_workspace_snapshot_misses_after_manifest_change(
    integrated_sandbox: SandboxHandle,
) -> None:
    handle = integrated_sandbox
    case = "changed_manifest_miss"
    path = "tracked/cache-version.txt"
    rows: list[dict[str, object]] = []

    await write_public_file(handle, path, "v1\n", description="phase02 version v1")
    first = await prepare_snapshot(handle, "prepare_v1", request_id=token("version-a"))
    rows.append(prepare_call_row(case, first))
    await release_snapshot(handle, first)

    await write_public_file(handle, path, "v2\n", description="phase02 version v2")
    assert await path_is_dir(handle, first.lowerdir)

    second = await prepare_snapshot(handle, "prepare_v2", request_id=token("version-b"))
    rows.append(prepare_call_row(case, second))
    assert second.cache_hit is False
    assert second.manifest_version != first.manifest_version
    assert second.root_hash != first.root_hash
    assert second.lowerdir != first.lowerdir
    assert await lowerdir_file_content(handle, second.lowerdir, path) == "v2\n"

    metrics_after_second = await runtime_layer_metrics(handle)
    assert lowerdir_cache_int(metrics_after_second, "lowerdir_cache_misses") >= 2

    await release_snapshot(handle, second)
    assert await path_is_dir(handle, second.lowerdir)

    await write_public_file(handle, path, "v3\n", description="phase02 version v3")
    assert await path_is_dir(handle, second.lowerdir)

    final_metrics = await runtime_layer_metrics(handle)
    artifact = write_jsonl_artifact(
        case=case,
        summary=summary_record(
            case=case,
            metrics=final_metrics,
            root_hash=second.root_hash,
            cache_creation={
                "created_lowerdirs": 2,
                "reused_lowerdirs": 0,
                "unexpected_extra_lowerdirs": 0,
            },
            pass_bars={
                "changed_manifest_missed_cache": True,
                "changed_manifest_new_root_hash": True,
                "latest_lowerdir_survived_release": True,
                "next_manifest_advance_left_latest_snapshot_cache": True,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase02:{case}] artifact={artifact}")


async def test_concurrent_prepare_same_manifest_fans_into_one_lowerdir(
    integrated_sandbox: SandboxHandle,
) -> None:
    handle = integrated_sandbox
    case = "concurrent_prepare_fan_in"
    concurrency = 10
    path = "tracked/cache-concurrent.txt"

    await write_public_file(
        handle,
        path,
        "shared\n",
        description="phase02 concurrent prepare seed",
    )

    prepares = await asyncio.gather(
        *(
            prepare_snapshot(
                handle,
                f"prepare_{index:02d}",
                request_id=token(f"fan-in-{index:02d}"),
            )
            for index in range(concurrency)
        )
    )
    rows = [prepare_call_row(case, prepare) for prepare in prepares]

    assert all(prepare.success for prepare in prepares)
    assert {prepare.manifest_version for prepare in prepares} == {
        prepares[0].manifest_version
    }
    assert {prepare.root_hash for prepare in prepares} == {prepares[0].root_hash}
    assert {prepare.lowerdir for prepare in prepares} == {prepares[0].lowerdir}
    assert sum(1 for prepare in prepares if not prepare.cache_hit) == 1
    assert sum(1 for prepare in prepares if prepare.cache_hit) == concurrency - 1
    assert await materialized_dir_count(handle) == 1

    metrics_before_release = await runtime_layer_metrics(handle)
    assert int(metrics_before_release["active_leases"]) == concurrency
    assert int(metrics_before_release["pinned_lowerdirs"]) == 1

    for index, prepare in enumerate(prepares):
        released = await release_snapshot(handle, prepare)
        rows.append(release_call_row(case, f"release_{index:02d}", released))

    shared_lowerdir = prepares[0].lowerdir
    assert await path_is_dir(handle, shared_lowerdir)

    reuse = await prepare_snapshot(handle, "prepare_reuse", request_id=token("fan-in-reuse"))
    rows.append(prepare_call_row(case, reuse))
    assert reuse.cache_hit is True
    assert reuse.lowerdir == shared_lowerdir
    await release_snapshot(handle, reuse)

    await write_public_file(
        handle,
        path,
        "advanced\n",
        description="phase02 concurrent prepare manifest advance",
    )
    assert await path_is_dir(handle, shared_lowerdir)

    final_metrics = await runtime_layer_metrics(handle)
    artifact = write_jsonl_artifact(
        case=case,
        summary=summary_record(
            case=case,
            metrics=final_metrics,
            root_hash=prepares[0].root_hash,
            cache_creation={
                "created_lowerdirs": 1,
                "reused_lowerdirs": concurrency,
                "unexpected_extra_lowerdirs": 0,
            },
            pass_bars={
                "concurrency": concurrency,
                "all_prepares_shared_manifest_identity": True,
                "one_miss_rest_hits": True,
                "final_state_one_lowerdir_not_n": True,
                "manifest_advance_left_shared_lowerdir_until_release_event": True,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase02:{case}] artifact={artifact}")


async def test_deep_manifest_materializes_one_cache_entry_not_one_per_layer(
    integrated_sandbox: SandboxHandle,
) -> None:
    handle = integrated_sandbox
    case = "deep_manifest_one_cache_entry"
    depth = env_int("EPHEMERALOS_LIVE_E2E_PHASE02_DEPTH", 20)
    rows: list[dict[str, object]] = []

    for index in range(depth):
        await write_public_file(
            handle,
            f"tracked/cache-depth/layer-{index:03d}.txt",
            f"depth-{index:03d}\n",
            description=f"phase02 deep manifest seed {index:03d}",
        )

    metrics_before = await runtime_layer_metrics(handle)
    assert int(metrics_before["manifest_depth"]) > 1, metrics_before
    assert int(metrics_before["manifest_depth"]) >= depth, metrics_before

    snapshot = await prepare_snapshot(handle, "prepare_deep", request_id=token("deep"))
    rows.append(prepare_call_row(case, snapshot))
    assert snapshot.cache_hit is False
    assert await materialized_dir_count(handle) == 1
    assert snapshot.materialized_byte_count > 0

    metrics_after_prepare = await runtime_layer_metrics(handle)
    assert int(metrics_after_prepare["materialized_lowerdirs"]) == 1

    released = await release_snapshot(handle, snapshot)
    rows.append(release_call_row(case, "release_deep", released))
    assert await path_is_dir(handle, snapshot.lowerdir)

    reuse = await prepare_snapshot(handle, "prepare_deep_reuse", request_id=token("deep-reuse"))
    rows.append(prepare_call_row(case, reuse))
    assert reuse.cache_hit is True
    assert reuse.lowerdir == snapshot.lowerdir
    await release_snapshot(handle, reuse)

    await write_public_file(
        handle,
        f"tracked/cache-depth/layer-{depth:03d}.txt",
        f"depth-{depth:03d}\n",
        description="phase02 deep manifest advance",
    )
    assert await path_is_dir(handle, snapshot.lowerdir)

    final_metrics = await runtime_layer_metrics(handle)
    artifact = write_jsonl_artifact(
        case=case,
        summary=summary_record(
            case=case,
            metrics=final_metrics,
            root_hash=snapshot.root_hash,
            cache_creation={
                "created_lowerdirs": 1,
                "reused_lowerdirs": 1,
                "unexpected_extra_lowerdirs": 0,
            },
            pass_bars={
                "configured_depth": depth,
                "depth_visible_in_metrics": True,
                "one_cache_entry_for_one_manifest_identity": True,
                "manifest_advance_left_old_deep_lowerdir_until_release_event": True,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase02:{case}] artifact={artifact}")


async def test_cache_hit_reduces_prepare_cost_for_same_manifest(
    integrated_sandbox: SandboxHandle,
) -> None:
    handle = integrated_sandbox
    case = "cache_hit_prepare_cost"
    pairs = env_int("EPHEMERALOS_LIVE_E2E_PHASE02_PERF_PAIRS", 5)
    payload_mb = env_int("EPHEMERALOS_LIVE_E2E_PHASE02_PERF_MB", 16)
    payload_bytes = payload_mb * 1024 * 1024
    rows: list[dict[str, object]] = []
    cold: list[SnapshotPair] = []

    for index in range(pairs):
        await write_large_public_file(
            handle,
            "tracked/cache-perf/payload.bin",
            byte_count=payload_bytes,
            fill_byte=65 + (index % 26),
            description=f"phase02 perf payload pair {index}",
        )
        cold_prepare = await prepare_snapshot(
            handle,
            f"pair_{index:02d}_cold",
            request_id=token(f"perf-cold-{index:02d}"),
            timeout=240,
        )
        rows.append(prepare_call_row(case, cold_prepare))
        assert cold_prepare.cache_hit is False
        assert "layer_stack.snapshot_cache.materialize_s" in cold_prepare.timings
        assert cold_prepare.materialized_byte_count >= payload_bytes
        await release_snapshot(handle, cold_prepare)

        warm_prepare = await prepare_snapshot(
            handle,
            f"pair_{index:02d}_warm",
            request_id=token(f"perf-warm-{index:02d}"),
            timeout=240,
        )
        rows.append(prepare_call_row(case, warm_prepare))
        assert warm_prepare.cache_hit is True
        assert "layer_stack.snapshot_cache.materialize_s" not in warm_prepare.timings
        assert warm_prepare.manifest_version == cold_prepare.manifest_version
        assert warm_prepare.root_hash == cold_prepare.root_hash
        assert warm_prepare.lowerdir == cold_prepare.lowerdir
        assert warm_prepare.cache_dir_count_after == cold_prepare.cache_dir_count_after

        cold.append(SnapshotPair(cold=cold_prepare, warm=warm_prepare))
        await write_public_file(
            handle,
            "tracked/cache-perf/evict.txt",
            f"evict {index}\n",
            description=f"phase02 perf advance before stale release {index}",
        )
        assert await path_is_dir(handle, cold_prepare.lowerdir)
        released = await release_snapshot(handle, warm_prepare)
        rows.append(release_call_row(case, f"pair_{index:02d}_release_warm_stale", released))
        assert not await path_exists(handle, cold_prepare.lowerdir)

    cold_wall = [pair.cold.wall_ms for pair in cold]
    warm_wall = [pair.warm.wall_ms for pair in cold]
    materialize_saved = [materialize_ms(pair.cold) for pair in cold]
    median_cold = statistics.median(cold_wall)
    median_warm = statistics.median(warm_wall)
    median_saved = statistics.median(materialize_saved)
    assert median_saved > 0.0
    assert median_warm < median_cold, {
        "median_cold_miss_wall_ms": median_cold,
        "median_warm_hit_wall_ms": median_warm,
        "cold_wall_ms": cold_wall,
        "warm_wall_ms": warm_wall,
        "api_cold_ms": [api_prepare_ms(pair.cold) for pair in cold],
        "api_warm_ms": [api_prepare_ms(pair.warm) for pair in cold],
    }

    final_metrics = await runtime_layer_metrics(handle)
    artifact = write_jsonl_artifact(
        case=case,
        summary=summary_record(
            case=case,
            metrics=final_metrics,
            root_hash=cold[-1].cold.root_hash if cold else "",
            cache_creation={
                "created_lowerdirs": pairs,
                "reused_lowerdirs": pairs,
                "unexpected_extra_lowerdirs": 0,
            },
            performance={
                "cold_miss_samples": pairs,
                "warm_hit_samples": pairs,
                "median_cold_miss_wall_ms": round(median_cold, 3),
                "median_warm_hit_wall_ms": round(median_warm, 3),
                "median_materialize_ms_saved": round(median_saved, 3),
                "min_cold_miss_wall_ms": round(min(cold_wall), 3),
                "max_cold_miss_wall_ms": round(max(cold_wall), 3),
                "min_warm_hit_wall_ms": round(min(warm_wall), 3),
                "max_warm_hit_wall_ms": round(max(warm_wall), 3),
                "warm_hit_faster_than_cold_miss": median_warm < median_cold,
            },
            pass_bars={
                "payload_mb": payload_mb,
                "paired_same_sandbox_samples": pairs,
                "warm_hit_skipped_materialization": True,
                "median_warm_wall_lt_median_cold_wall": True,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase02:{case}] artifact={artifact}")


async def test_cache_size_is_observable_and_eviction_returns_unpinned_space(
    integrated_sandbox: SandboxHandle,
) -> None:
    handle = integrated_sandbox
    case = "disk_budget_and_eviction"
    payload_mb = env_int("EPHEMERALOS_LIVE_E2E_PHASE02_LARGE_MB", 16)
    payload_bytes = payload_mb * 1024 * 1024

    df_before = await df_available_kb(handle)
    cache_bytes_before = await materialized_cache_bytes(handle)
    await write_large_public_file(
        handle,
        "tracked/cache-disk/payload.bin",
        byte_count=payload_bytes,
        fill_byte=90,
        description="phase02 disk payload",
    )

    snapshot = await prepare_snapshot(
        handle,
        "prepare_disk",
        request_id=token("disk"),
        timeout=240,
    )
    rows = [prepare_call_row(case, snapshot)]
    cache_bytes_after_prepare = await materialized_cache_bytes(handle)
    assert snapshot.materialized_byte_count >= payload_bytes
    assert cache_bytes_after_prepare > cache_bytes_before

    await write_public_file(
        handle,
        "tracked/cache-disk/evict.txt",
        "evict\n",
        description="phase02 disk manifest advance before stale release",
    )
    cache_bytes_while_stale_pinned = await materialized_cache_bytes(handle)
    assert await path_is_dir(handle, snapshot.lowerdir)
    assert cache_bytes_while_stale_pinned >= payload_bytes

    released = await release_snapshot(handle, snapshot)
    rows.append(release_call_row(case, "release_disk_stale", released))
    assert not await path_exists(handle, snapshot.lowerdir)
    cache_bytes_after_eviction = await materialized_cache_bytes(handle)
    df_after_eviction = await df_available_kb(handle)
    assert cache_bytes_after_eviction <= max(cache_bytes_before + 8192, 8192)

    final_metrics = await runtime_layer_metrics(handle)
    artifact = write_jsonl_artifact(
        case=case,
        summary=summary_record(
            case=case,
            metrics=final_metrics,
            root_hash=snapshot.root_hash,
            cache_creation={
                "created_lowerdirs": 1,
                "reused_lowerdirs": 0,
                "unexpected_extra_lowerdirs": 0,
            },
            cache_bytes_before=cache_bytes_before,
            cache_bytes_after_prepare=cache_bytes_after_prepare,
            cache_bytes_after_eviction=cache_bytes_after_eviction,
            df_kb_available_before=df_before,
            df_kb_available_after_eviction=df_after_eviction,
            pass_bars={
                "payload_mb": payload_mb,
                "cache_bytes_increased_after_prepare": True,
                "manifest_advance_kept_stale_leased_space": True,
                "final_stale_release_returned_unpinned_cache_space": True,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase02:{case}] artifact={artifact}")


async def test_prepare_workspace_snapshot_fails_closed_without_workspace_binding(
    integrated_sandbox: SandboxHandle,
) -> None:
    handle = integrated_sandbox
    case = "missing_workspace_binding_fail_closed"
    temp_root = f"/tmp/eos-phase02-missing-binding-{token('root')}"
    await handle.raw_exec(handle.sandbox_id, f"rm -rf {temp_root}", timeout=30)
    try:
        with pytest.raises(Exception) as exc_info:
            await prepare_snapshot(
                handle,
                "prepare_missing_binding",
                request_id=token("missing-binding"),
                layer_stack_root=temp_root,
            )
        message = str(exc_info.value).lower()
        assert "workspace binding" in message or "active manifest" in message

        assert await materialized_dir_count(handle, layer_stack_root=temp_root) == 0
        metrics = await runtime_layer_metrics(handle, layer_stack_root=temp_root)
        assert metrics["workspace_bound"] is False
        assert int(metrics["active_leases"]) == 0, metrics
        assert int(metrics["pinned_lowerdirs"]) == 0, metrics
        assert int(metrics["materialized_lowerdirs"]) == 0, metrics

        artifact = write_jsonl_artifact(
            case=case,
            summary=summary_record(
                case=case,
                metrics=metrics,
                cache_creation={
                    "created_lowerdirs": 0,
                    "reused_lowerdirs": 0,
                    "unexpected_extra_lowerdirs": 0,
                },
                pass_bars={
                    "runtime_call_failed": True,
                    "error_named_missing_binding_or_empty_manifest": True,
                    "no_cache_directory_created": True,
                    "no_lease_remained_pinned": True,
                    "temporary_layer_stack_root": temp_root,
                },
            ),
            rows=[],
        )
        print(f"\n[phase02:{case}] artifact={artifact}")
    finally:
        await handle.raw_exec(handle.sandbox_id, f"rm -rf {temp_root}", timeout=30)


class SnapshotPair:
    def __init__(self, *, cold: object, warm: object) -> None:
        self.cold = cold
        self.warm = warm
