"""Phase 04 live A/B load for workspace-replaced shell execution."""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from .._harness.concurrency import gather_with_barrier
from .._harness.integrated_cases import percentile
from .._harness.sandbox_fixture import SandboxHandle, WORKSPACE_ROOT
from .._harness.workspace_base_metrics import runtime_call


pytestmark = pytest.mark.asyncio

_Policy = str
_Factory = Callable[[], Awaitable[dict[str, Any]]]
_POLICIES: tuple[_Policy, ...] = ("cache_enabled", "cache_disabled")
_POLICY_TO_ARG: dict[_Policy, str] = {
    "cache_enabled": "enabled",
    "cache_disabled": "disabled",
}
_CONCURRENCIES = (1, 5, 10, 20)
_DEFAULT_WORKSPACE_BYTES = 16 * 1024 * 1024


async def test_workspace_replaced_shell_cache_enabled_vs_disabled(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    workspace_bytes = _DEFAULT_WORKSPACE_BYTES

    policy_rows: dict[_Policy, list[dict[str, object]]] = {}
    for policy in _POLICIES:
        binding = await _seed_workspace(handle, workspace_bytes=workspace_bytes)
        rows: list[dict[str, object]] = []
        for concurrency in _CONCURRENCIES:
            before = await _layer_stack_stats(handle, binding)
            started = time.perf_counter()
            results = await gather_with_barrier(
                _shell_factories(
                    handle,
                    policy=policy,
                    workspace_bytes=workspace_bytes,
                    concurrency=concurrency,
                )
            )
            batch_wall_ms = (time.perf_counter() - started) * 1000.0
            after = await _layer_stack_stats(handle, binding)
            _assert_policy_results(results, policy=policy, concurrency=concurrency)
            await _reconcile_policy_results(
                handle,
                results,
                policy=policy,
                concurrency=concurrency,
            )
            batch_stats = _batch_stats(
                results,
                policy=policy,
                before=before,
                after=after,
            )
            rows.extend(
                _row(
                    policy=policy,
                    workspace_bytes=workspace_bytes,
                    concurrency=concurrency,
                    batch_wall_ms=batch_wall_ms,
                    result=result,
                    batch_stats=batch_stats,
                )
                for result in results
            )
        policy_rows[policy] = rows
        _write_policy_artifact(policy=policy, binding=binding, rows=rows)

    summary_rows = _summary_rows(policy_rows)
    _write_summary_artifact(summary_rows)
    assert all(row["correctness_match"] is True for row in summary_rows)


def _shell_factories(
    handle: SandboxHandle,
    *,
    policy: _Policy,
    workspace_bytes: int,
    concurrency: int,
) -> Sequence[_Factory]:
    factories: list[_Factory] = []
    for index in range(concurrency):
        output_path = f"tracked/phase04/{policy}/c{concurrency:02d}-{index:02d}.txt"
        outside_path = f"/tmp/eos-phase04-{policy}-c{concurrency:02d}-{index:02d}.txt"
        command = (
            "set -e; "
            "test -x /bin/sh; "
            f"cat {shlex.quote(WORKSPACE_ROOT + '/stable.txt')} >/dev/null; "
            f"head -c 4096 {shlex.quote(WORKSPACE_ROOT + '/payload.bin')} >/dev/null; "
            f"mkdir -p {shlex.quote(WORKSPACE_ROOT + '/tracked/phase04/' + policy)}; "
            f"printf {shlex.quote(_content(policy, concurrency, index))} "
            f"> {shlex.quote(WORKSPACE_ROOT + '/' + output_path)}; "
            f"printf outside > {shlex.quote(outside_path)}"
        )

        async def run_shell(
            index: int = index,
            output_path: str = output_path,
            command: str = command,
        ) -> dict[str, Any]:
            started = time.perf_counter()
            raw = await runtime_call(
                handle,
                "api.shell",
                {
                    "command": command,
                    "cwd": WORKSPACE_ROOT,
                    "timeout_seconds": 60,
                    "actor_id": "phase04-cache-ab",
                    "description": f"phase04 {policy} c={concurrency} i={index}",
                    "snapshot_cache_policy": _POLICY_TO_ARG[policy],
                },
                timeout=120,
            )
            raw["wall_ms"] = (time.perf_counter() - started) * 1000.0
            raw["expected_output_path"] = output_path
            raw["outside_layer_path"] = outside_path.removeprefix("/")
            raw["workspace_bytes"] = workspace_bytes
            return raw

        factories.append(run_shell)
    return factories


async def _seed_workspace(
    handle: SandboxHandle,
    *,
    workspace_bytes: int,
) -> dict[str, object]:
    script = r"""
import sys
from pathlib import Path

root = Path(sys.argv[1])
size = int(sys.argv[2])
(root / "tracked" / "phase04").mkdir(parents=True, exist_ok=True)
(root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
(root / "stable.txt").write_text("stable\n", encoding="utf-8")
with (root / "payload.bin").open("wb") as file:
    chunk = b"x" * 1024 * 1024
    remaining = size
    while remaining > 0:
        take = min(len(chunk), remaining)
        file.write(chunk[:take])
        remaining -= take
"""
    result = await handle.raw_exec(
        handle.sandbox_id,
        "python3 -c {script} {root} {size}".format(
            script=shlex.quote(script),
            root=shlex.quote(WORKSPACE_ROOT),
            size=workspace_bytes,
        ),
        timeout=120,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    built = await runtime_call(
        handle,
        "api.build_workspace_base",
        {"workspace_root": WORKSPACE_ROOT, "reset": True},
        timeout=240,
    )
    assert built.get("success") is True, built
    binding = built.get("binding")
    assert isinstance(binding, dict)
    return binding


def _assert_policy_results(
    results: Sequence[Mapping[str, Any]],
    *,
    policy: _Policy,
    concurrency: int,
) -> None:
    for index, result in enumerate(results):
        assert result.get("success") is True, result
        assert result.get("exit_code") == 0, result
        output_path = str(result["expected_output_path"])
        assert output_path in set(result.get("changed_paths") or ()), result
        assert str(result.get("stdout") or "") == ""
        assert str(result.get("stderr") or "") == ""
        capture = result.get("workspace_capture")
        assert isinstance(capture, dict), result
        assert capture.get("mount_mode") == "private_namespace", result
        expected = _content(policy, concurrency, index)
        assert expected.startswith("phase04:")


async def _reconcile_policy_results(
    handle: SandboxHandle,
    results: Sequence[Mapping[str, Any]],
    *,
    policy: _Policy,
    concurrency: int,
) -> None:
    for index, result in enumerate(results):
        output = await handle.tool.read_file(str(result["expected_output_path"]))
        assert output.success
        assert output.exists
        assert output.content == _content(policy, concurrency, index)
        outside = await handle.tool.read_file(str(result["outside_layer_path"]))
        assert outside.success
        assert outside.exists is False


def _row(
    *,
    policy: _Policy,
    workspace_bytes: int,
    concurrency: int,
    batch_wall_ms: float,
    result: Mapping[str, Any],
    batch_stats: Mapping[str, object],
) -> dict[str, object]:
    timings = _timings(result.get("timings"))
    return {
        "policy": policy,
        "workspace_bytes": workspace_bytes,
        "concurrency": concurrency,
        "batch_wall_ms": round(batch_wall_ms, 3),
        "per_call_wall_ms": round(float(result.get("wall_ms") or 0.0), 3),
        "success": bool(result.get("success")),
        "published_workspace_paths": list(result.get("changed_paths") or ()),
        "outside_workspace_paths_not_published": True,
        "api.shell.total_s": timings.get("api.shell.total_s", 0.0),
        "command_exec.prepare_snapshot_s": timings.get(
            "command_exec.prepare_snapshot_s",
            0.0,
        ),
        "command_exec.mount_workspace_s": timings.get(
            "command_exec.mount_workspace_s",
            0.0,
        ),
        "command_exec.run_command_s": timings.get("command_exec.run_command_s", 0.0),
        "command_exec.capture_upperdir_s": timings.get(
            "command_exec.capture_upperdir_s",
            0.0,
        ),
        "command_exec.occ_apply_s": timings.get("command_exec.occ_apply_s", 0.0),
        "command_exec.release_snapshot_s": timings.get(
            "command_exec.release_snapshot_s",
            0.0,
        ),
        "layer_stack.snapshot_cache.hit": timings.get(
            "layer_stack.snapshot_cache.hit",
            0.0,
        ),
        "layer_stack.snapshot_cache.materialize_s": timings.get(
            "layer_stack.snapshot_cache.materialize_s",
            0.0,
        ),
        "materialized_lowerdirs_peak": batch_stats["materialized_lowerdirs_peak"],
        "cache_bytes_peak": batch_stats["cache_bytes_peak"],
        "cache_bytes_after_release": batch_stats["cache_bytes_after_release"],
        "df_kb_available_before": batch_stats["df_kb_available_before"],
        "df_kb_available_after": batch_stats["df_kb_available_after"],
        "success_count": batch_stats["success_count"],
        "conflict_count": batch_stats["conflict_count"],
    }


def _summary_rows(
    policy_rows: Mapping[_Policy, Sequence[Mapping[str, object]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for concurrency in _CONCURRENCIES:
        enabled = _rows_for(policy_rows["cache_enabled"], concurrency)
        disabled = _rows_for(policy_rows["cache_disabled"], concurrency)
        enabled_wall = [float(row["per_call_wall_ms"]) for row in enabled]
        disabled_wall = [float(row["per_call_wall_ms"]) for row in disabled]
        enabled_p95 = percentile(enabled_wall, 95)
        disabled_p95 = percentile(disabled_wall, 95)
        enabled_batch = max((float(row["batch_wall_ms"]) for row in enabled), default=0.0)
        disabled_batch = max(
            (float(row["batch_wall_ms"]) for row in disabled),
            default=0.0,
        )
        enabled_cache_peak = max(
            (int(row.get("cache_bytes_peak") or 0) for row in enabled),
            default=0,
        )
        disabled_cache_peak = max(
            (int(row.get("cache_bytes_peak") or 0) for row in disabled),
            default=0,
        )
        p95_saved = disabled_p95 - enabled_p95
        batch_saved = disabled_batch - enabled_batch
        correctness_match = _success_count(enabled) == _success_count(disabled)
        rows.append(
            {
                "concurrency": concurrency,
                "cache_enabled_p50_wall_ms": round(percentile(enabled_wall, 50), 3),
                "cache_enabled_p95_wall_ms": round(enabled_p95, 3),
                "cache_enabled_batch_wall_ms": round(enabled_batch, 3),
                "cache_disabled_p50_wall_ms": round(percentile(disabled_wall, 50), 3),
                "cache_disabled_p95_wall_ms": round(disabled_p95, 3),
                "cache_disabled_batch_wall_ms": round(disabled_batch, 3),
                "absolute_p95_ms_saved": round(p95_saved, 3),
                "relative_p95_saved_percent": _percent(p95_saved, disabled_p95),
                "absolute_batch_ms_saved": round(batch_saved, 3),
                "relative_batch_saved_percent": _percent(batch_saved, disabled_batch),
                "extra_cache_bytes_peak": max(0, enabled_cache_peak - disabled_cache_peak),
                "correctness_match": correctness_match,
                "keep_cache_recommendation": correctness_match
                and (
                    _percent(p95_saved, disabled_p95) >= 20.0
                    or p95_saved >= 250.0
                    or _percent(batch_saved, disabled_batch) >= 20.0
                    or batch_saved >= 250.0
                ),
            }
        )
    return rows


async def _layer_stack_stats(
    handle: SandboxHandle,
    binding: Mapping[str, object],
) -> dict[str, int]:
    layer_stack_root = str(binding["layer_stack_root"])
    script = r"""
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
materialized = root / "materialized"
snapshots = [
    child for child in materialized.iterdir()
    if child.is_dir() and child.name != ".staging"
] if materialized.is_dir() else []
cache_bytes = 0
for snapshot in snapshots:
    lowerdir = snapshot / "lower"
    for entry in (lowerdir.rglob("*") if lowerdir.is_dir() else ()):
        try:
            if entry.is_file() or entry.is_symlink():
                cache_bytes += entry.lstat().st_size
        except OSError:
            pass
stat = os.statvfs(str(root))
print(json.dumps({
    "materialized_lowerdirs": len(snapshots),
    "cache_bytes": cache_bytes,
    "df_kb_available": (stat.f_bavail * stat.f_frsize) // 1024,
}))
"""
    result = await handle.raw_exec(
        handle.sandbox_id,
        "python3 -c {script} {root}".format(
            script=shlex.quote(script),
            root=shlex.quote(layer_stack_root),
        ),
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip())
    assert isinstance(payload, dict)
    return {str(key): int(value) for key, value in payload.items()}


def _batch_stats(
    results: Sequence[Mapping[str, Any]],
    *,
    policy: _Policy,
    before: Mapping[str, int],
    after: Mapping[str, int],
) -> dict[str, int]:
    active_cache_bytes = 0
    if policy == "cache_enabled":
        active_cache_bytes = max(
            (
                int(
                    _timings(result.get("timings")).get(
                        "layer_stack.snapshot_cache.bytes",
                        0.0,
                    )
                )
                for result in results
            ),
            default=0,
        )
    return {
        "materialized_lowerdirs_peak": max(
            int(before["materialized_lowerdirs"]),
            int(after["materialized_lowerdirs"]),
            1 if active_cache_bytes > 0 else 0,
        ),
        "cache_bytes_peak": max(
            int(before["cache_bytes"]),
            int(after["cache_bytes"]),
            active_cache_bytes,
        ),
        "cache_bytes_after_release": int(after["cache_bytes"]),
        "df_kb_available_before": int(before["df_kb_available"]),
        "df_kb_available_after": int(after["df_kb_available"]),
        "success_count": _success_count(results),
        "conflict_count": sum(1 for result in results if result.get("conflict")),
    }


def _write_policy_artifact(
    *,
    policy: _Policy,
    binding: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
) -> Path:
    artifact = _artifact_path(f"live-e2e-phase04-shell-cache-ab-{policy}")
    summary = {
        "kind": "summary",
        "policy": policy,
        "workspace_root": binding.get("workspace_root"),
        "layer_stack_root": binding.get("layer_stack_root"),
        "rows": len(rows),
    }
    _write_jsonl(artifact, (summary, *rows))
    return artifact


def _write_summary_artifact(rows: Sequence[Mapping[str, object]]) -> Path:
    artifact = _artifact_path("live-e2e-phase04-shell-cache-ab-summary")
    _write_jsonl(artifact, rows)
    return artifact


def _artifact_path(prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path.cwd() / ".omc" / "results" / f"{prefix}-{stamp}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            file.write("\n")


def _rows_for(
    rows: Sequence[Mapping[str, object]],
    concurrency: int,
) -> list[Mapping[str, object]]:
    return [row for row in rows if int(row["concurrency"]) == concurrency]


def _success_count(rows: Sequence[Mapping[str, object]]) -> int:
    return sum(1 for row in rows if bool(row.get("success")))


def _percent(delta: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return round((delta / baseline) * 100.0, 3)


def _timings(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): float(value) for key, value in raw.items()}


def _content(policy: _Policy, concurrency: int, index: int) -> str:
    return f"phase04:{policy}:c{concurrency:02d}:i{index:02d}\n"
