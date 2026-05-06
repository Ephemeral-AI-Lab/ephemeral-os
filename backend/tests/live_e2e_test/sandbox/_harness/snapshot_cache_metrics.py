"""Harness helpers for Phase 02 materialized snapshot-cache live tests."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sandbox.api.tool._runtime import DEFAULT_LAYER_STACK_ROOT, call_runtime_api

from .integrated_cases import assert_committed, q
from .sandbox_fixture import SandboxHandle, WORKSPACE_ROOT


SCHEMA = "sandbox.live_e2e.phase02_snapshot_cache_leases.v1"
LAYER_STACK_ROOT = DEFAULT_LAYER_STACK_ROOT


@dataclass(frozen=True)
class SnapshotPrepare:
    label: str
    payload: dict[str, Any]
    wall_ms: float
    cache_dir_count_before: int
    cache_dir_count_after: int

    @property
    def success(self) -> bool:
        return bool(self.payload.get("success", False))

    @property
    def lease_id(self) -> str:
        return str(self.payload["lease_id"])

    @property
    def manifest_version(self) -> int:
        return int(self.payload["manifest_version"])

    @property
    def root_hash(self) -> str:
        return str(self.payload["root_hash"])

    @property
    def lowerdir(self) -> str:
        return str(self.payload["lowerdir"])

    @property
    def cache_hit(self) -> bool:
        return bool(self.payload["cache_hit"])

    @property
    def materialized_byte_count(self) -> int:
        return int(self.payload.get("materialized_byte_count") or 0)

    @property
    def timings(self) -> dict[str, float]:
        raw = self.payload.get("timings")
        if not isinstance(raw, dict):
            return {}
        return {str(key): float(value) for key, value in raw.items()}


async def prepare_snapshot(
    handle: SandboxHandle,
    label: str,
    *,
    request_id: str,
    layer_stack_root: str = LAYER_STACK_ROOT,
    timeout: int = 180,
) -> SnapshotPrepare:
    before = await materialized_dir_count(handle, layer_stack_root=layer_stack_root)
    started = time.perf_counter()
    payload = await call_runtime_api(
        handle.sandbox_id,
        "api.prepare_workspace_snapshot",
        {"request_id": request_id},
        timeout=timeout,
        layer_stack_root=layer_stack_root,
    )
    wall_ms = (time.perf_counter() - started) * 1000.0
    after = await materialized_dir_count(handle, layer_stack_root=layer_stack_root)
    result = SnapshotPrepare(
        label=label,
        payload=payload,
        wall_ms=wall_ms,
        cache_dir_count_before=before,
        cache_dir_count_after=after,
    )
    assert result.success, payload
    return result


async def release_snapshot(
    handle: SandboxHandle,
    prepare: SnapshotPrepare,
    *,
    layer_stack_root: str = LAYER_STACK_ROOT,
) -> dict[str, Any]:
    payload = await call_runtime_api(
        handle.sandbox_id,
        "api.release_workspace_snapshot",
        {"lease_id": prepare.lease_id},
        timeout=60,
        layer_stack_root=layer_stack_root,
    )
    assert payload.get("success") is True, payload
    assert payload.get("released") is True, payload
    return payload


async def runtime_layer_metrics(
    handle: SandboxHandle,
    *,
    layer_stack_root: str = LAYER_STACK_ROOT,
) -> dict[str, Any]:
    payload = await call_runtime_api(
        handle.sandbox_id,
        "api.layer_metrics",
        {},
        timeout=60,
        layer_stack_root=layer_stack_root,
    )
    assert payload.get("success") is True, payload
    return payload


async def write_public_file(
    handle: SandboxHandle,
    path: str,
    content: str,
    *,
    description: str,
) -> None:
    result = await handle.tool.write_file(path, content, description=description)
    assert_committed(result, path=path)


async def write_large_public_file(
    handle: SandboxHandle,
    path: str,
    *,
    byte_count: int,
    fill_byte: int,
    description: str,
) -> None:
    """Create a large tracked payload through the guarded public shell API.

    Large payloads are generated inside the sandbox so the test measures
    layer-stack behavior instead of the host thin-client argv limit.
    """
    command = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"path = Path({path!r})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"path.write_bytes(bytes([{fill_byte}]) * {byte_count})\n"
        "PY"
    )
    result = await handle.tool.shell(command, timeout=180, description=description)
    assert_committed(result, path=path)


async def materialized_dir_count(
    handle: SandboxHandle,
    *,
    layer_stack_root: str = LAYER_STACK_ROOT,
) -> int:
    materialized = f"{layer_stack_root.rstrip('/')}/materialized"
    stdout = await raw_stdout(
        handle,
        (
            f"if [ -d {q(materialized)} ]; then "
            f"find {q(materialized)} -mindepth 1 -maxdepth 1 -type d "
            "! -name .staging | wc -l; "
            "else echo 0; fi"
        ),
        timeout=30,
    )
    return int(stdout.splitlines()[-1].strip())


async def materialized_cache_bytes(
    handle: SandboxHandle,
    *,
    layer_stack_root: str = LAYER_STACK_ROOT,
) -> int:
    materialized = f"{layer_stack_root.rstrip('/')}/materialized"
    stdout = await raw_stdout(
        handle,
        (
            f"if [ -d {q(materialized)} ]; then "
            f"du -sb {q(materialized)} | awk '{{print $1}}'; "
            "else echo 0; fi"
        ),
        timeout=60,
    )
    return int(stdout.splitlines()[-1].strip())


async def df_available_kb(
    handle: SandboxHandle,
    *,
    layer_stack_root: str = LAYER_STACK_ROOT,
) -> int:
    stdout = await raw_stdout(
        handle,
        f"mkdir -p {q(layer_stack_root)} && df -Pk {q(layer_stack_root)} | awk 'NR==2 {{print $4}}'",
        timeout=30,
    )
    return int(stdout.splitlines()[-1].strip())


async def path_exists(handle: SandboxHandle, path: str) -> bool:
    result = await handle.raw_exec(handle.sandbox_id, f"test -e {q(path)}", timeout=15)
    return result.exit_code == 0


async def path_is_dir(handle: SandboxHandle, path: str) -> bool:
    result = await handle.raw_exec(handle.sandbox_id, f"test -d {q(path)}", timeout=15)
    return result.exit_code == 0


async def lowerdir_file_content(
    handle: SandboxHandle,
    lowerdir: str,
    relative_path: str,
) -> str:
    path = f"{lowerdir.rstrip('/')}/{relative_path.lstrip('/')}"
    script = (
        "import json,sys;"
        "from pathlib import Path;"
        "sys.stdout.write(json.dumps(Path(sys.argv[1]).read_text(encoding='utf-8')))"
    )
    stdout = await raw_stdout(
        handle,
        f"python3 -c {q(script)} {q(path)}",
        timeout=30,
    )
    return str(json.loads(stdout))


async def raw_stdout(
    handle: SandboxHandle,
    command: str,
    *,
    timeout: int,
    strip: bool = True,
) -> str:
    result = await handle.raw_exec(handle.sandbox_id, command, timeout=timeout)
    if result.exit_code != 0:
        pytest.fail(f"side-channel command failed: {command}\n{result.stderr or result.stdout}")
    return result.stdout.strip() if strip else result.stdout


def prepare_call_row(case: str, prepare: SnapshotPrepare) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "kind": "call",
        "case": case,
        "label": prepare.label,
        "op": "api.prepare_workspace_snapshot",
        "success": prepare.success,
        "cache_hit": prepare.cache_hit,
        "manifest_version": prepare.manifest_version,
        "root_hash": prepare.root_hash,
        "lowerdir": prepare.lowerdir,
        "materialized_byte_count": prepare.materialized_byte_count,
        "cache_dir_count_before": prepare.cache_dir_count_before,
        "cache_dir_count_after": prepare.cache_dir_count_after,
        "created_lowerdir": not prepare.cache_hit,
        "wall_ms": round(prepare.wall_ms, 3),
        "timings": _round_timings(prepare.timings),
    }


def release_call_row(case: str, label: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "kind": "call",
        "case": case,
        "label": label,
        "op": "api.release_workspace_snapshot",
        "success": payload.get("success") is True,
        "released": payload.get("released") is True,
    }


def summary_record(
    *,
    case: str,
    metrics: dict[str, Any] | None,
    root_hash: str = "",
    cache_creation: dict[str, int] | None = None,
    performance: dict[str, Any] | None = None,
    timings_ms: dict[str, float] | None = None,
    pass_bars: dict[str, Any] | None = None,
    cache_bytes_before: int = 0,
    cache_bytes_after_prepare: int = 0,
    cache_bytes_after_eviction: int = 0,
    df_kb_available_before: int = 0,
    df_kb_available_after_eviction: int = 0,
) -> dict[str, Any]:
    metrics = metrics or {}
    lowerdir_cache = _dict(metrics.get("lowerdir_cache"))
    return {
        "schema": SCHEMA,
        "kind": "summary",
        "case": case,
        "workspace_root": WORKSPACE_ROOT,
        "layer_stack_root": LAYER_STACK_ROOT,
        "active_manifest_version": _int(metrics.get("manifest_version")),
        "root_hash": root_hash,
        "materialized_lowerdirs": _int(metrics.get("materialized_lowerdirs")),
        "active_leases": _int(metrics.get("active_leases")),
        "pinned_lowerdirs": _int(metrics.get("pinned_lowerdirs")),
        "cache_creation": cache_creation
        or {
            "created_lowerdirs": 0,
            "reused_lowerdirs": 0,
            "unexpected_extra_lowerdirs": 0,
        },
        "cache_bytes_before": cache_bytes_before,
        "cache_bytes_after_prepare": cache_bytes_after_prepare,
        "cache_bytes_after_eviction": cache_bytes_after_eviction,
        "df_kb_available_before": df_kb_available_before,
        "df_kb_available_after_eviction": df_kb_available_after_eviction,
        "lowerdir_cache": lowerdir_cache,
        "performance": performance
        or {
            "cold_miss_samples": 0,
            "warm_hit_samples": 0,
            "median_cold_miss_wall_ms": 0.0,
            "median_warm_hit_wall_ms": 0.0,
            "median_materialize_ms_saved": 0.0,
            "warm_hit_faster_than_cold_miss": False,
        },
        "timings_ms": timings_ms or {},
        "pass_bars": pass_bars or {},
    }


def write_jsonl_artifact(
    *,
    case: str,
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = (
        Path.cwd()
        / ".omc"
        / "results"
        / f"live-e2e-phase02-snapshot-cache-leases-{case}-{timestamp}.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in (summary, *rows):
            file.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            file.write("\n")
    return path


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def lowerdir_cache_int(metrics: dict[str, Any], key: str) -> int:
    return int(float(_dict(metrics.get("lowerdir_cache")).get(key, 0)))


def materialize_ms(prepare: SnapshotPrepare) -> float:
    return float(prepare.timings.get("layer_stack.snapshot_cache.materialize_s", 0.0)) * 1000.0


def api_prepare_ms(prepare: SnapshotPrepare) -> float:
    return float(prepare.timings.get("api.prepare_workspace_snapshot.total_s", 0.0)) * 1000.0


def _round_timings(timings: dict[str, float]) -> dict[str, float]:
    return {key: round(float(value), 6) for key, value in sorted(timings.items())}


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _int(value: object) -> int:
    if value is None:
        return 0
    return int(float(value))
