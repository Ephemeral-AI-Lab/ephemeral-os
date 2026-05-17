"""Shared helpers for Phase 05 public file-op live tests."""

from __future__ import annotations

import json
import os
import shlex
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import sandbox.host.daemon_client as daemon_client_mod
from sandbox.host.daemon_client import DEFAULT_LAYER_STACK_ROOT

from .integrated_cases import RuntimeCallMetric, percentile, q
from .sandbox_fixture import SandboxHandle, WORKSPACE_ROOT
from .workspace_base_public import selected_runtime_ms


SCHEMA = "sandbox.live_e2e.phase05_public_file_ops.v1"
CONCURRENCIES = (1, 5, 10, 20)
LAYER_STACK_ROOT = DEFAULT_LAYER_STACK_ROOT
OUTSIDE_SYMLINK_TARGET = "/tmp/eos-phase05-outside/escape.txt"
LARGE_PATH = "tracked/large.txt"
LARGE_PATTERN = "0123456789abcdef"
LARGE_PATTERN_REPETITIONS = 65536
LARGE_OLD_TAIL = "PHASE05_LARGE_TAIL=old"
LARGE_NEW_TAIL = "PHASE05_LARGE_TAIL=new"


def phase05_base_files(*, max_concurrency: int = 20) -> dict[str, str]:
    """Return the deterministic text fixture imported as `/testbed` base."""
    files = {
        ".gitignore": "dist/\n.tmp/\n",
        "README.md": "# Phase 05 Base\n\nImported workspace base.\n",
        "raw.txt": "base\n",
        "src/app.py": "def render():\n    return 'base-app'\n",
        "src/config/settings.json": '{\n  "mode": "base",\n  "debug": false\n}\n',
        "frontend/src/App.tsx": "export function App() { return <main>base</main>; }\n",
        "tracked/edit-target.txt": "alpha=old\nbeta=stable\ngamma=old\n",
        "tracked/edge/create-only-existing.txt": "existing\n",
        "tracked/edge/delete-vs-write.txt": "base-delete\n",
        "tracked/edge/disjoint-edit.txt": "alpha=old\nbeta=stable\ngamma=old\n",
        "tracked/edge/overlap-edit.txt": "shared=old\n",
        "tracked/edge/same-write.txt": "base-write\n",
        "tracked/edge/shell-stale.txt": "base-shell\n",
        "dist/existing-ignored.txt": "ignored-base\n",
    }
    for index in range(max_concurrency * 2):
        files[f"tracked/load/read/read-{index:02d}.txt"] = (
            f"read-base-{index:02d}\n"
        )
    for concurrency in CONCURRENCIES:
        for index in range(max_concurrency):
            files[f"tracked/load/edit/c{concurrency:02d}-{index:02d}.txt"] = (
                f"edit-base-c{concurrency:02d}-{index:02d}\n"
            )
            files[f"tracked/load/mixed/edit-c{concurrency:02d}-{index:02d}.txt"] = (
                f"mixed-edit-base-c{concurrency:02d}-{index:02d}\n"
            )
    return files


def large_text_content() -> str:
    return (
        "phase05-large-start\n"
        + (LARGE_PATTERN * LARGE_PATTERN_REPETITIONS)
        + f"\n{LARGE_OLD_TAIL}\n"
    )


def edited_large_text_content() -> str:
    return large_text_content().replace(LARGE_OLD_TAIL, LARGE_NEW_TAIL, 1)


async def seed_phase05_imported_base(
    handle: SandboxHandle,
    *,
    max_concurrency: int = 20,
) -> dict[str, object]:
    """Seed raw `/testbed`, then import it as the public runtime base."""
    files = phase05_base_files(max_concurrency=max_concurrency)
    for path in files:
        _validate_relative_path(path)

    payload = {
        "files": files,
        "large_path": LARGE_PATH,
        "large_pattern": LARGE_PATTERN,
        "large_repetitions": LARGE_PATTERN_REPETITIONS,
        "large_tail": LARGE_OLD_TAIL,
        "outside_target": OUTSIDE_SYMLINK_TARGET,
    }
    script = r"""
import json
import os
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
payload = json.loads(sys.argv[2])

outside_target = Path(payload["outside_target"])
shutil.rmtree(outside_target.parent, ignore_errors=True)
outside_target.parent.mkdir(parents=True, exist_ok=True)
outside_target.write_text("outside-base\n", encoding="utf-8")

for rel, content in payload["files"].items():
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

large = (
    "phase05-large-start\n"
    + (payload["large_pattern"] * int(payload["large_repetitions"]))
    + "\n"
    + payload["large_tail"]
    + "\n"
)
large_path = root / payload["large_path"]
large_path.parent.mkdir(parents=True, exist_ok=True)
large_path.write_text(large, encoding="utf-8")

binary_path = root / "tracked" / "binary.bin"
binary_path.parent.mkdir(parents=True, exist_ok=True)
binary_path.write_bytes(b"\xff\xfe\x00\x00phase05")

links = root / "links"
links.mkdir(parents=True, exist_ok=True)
for name in ("inside", "outside"):
    target = links / name
    try:
        target.unlink()
    except FileNotFoundError:
        pass
os.symlink("../src/app.py", links / "inside")
os.symlink(str(outside_target), links / "outside")
"""
    result = await handle.raw_exec(
        handle.sandbox_id,
        "python3 -c {script} {root} {payload}".format(
            script=shlex.quote(script),
            root=shlex.quote(WORKSPACE_ROOT),
            payload=shlex.quote(json.dumps(payload, ensure_ascii=False)),
        ),
        timeout=120,
    )
    assert result.exit_code == 0, result.stderr or result.stdout

    built = await daemon_client_mod.call_daemon_api(
        handle.sandbox_id,
        "api.build_workspace_base",
        {"workspace_root": WORKSPACE_ROOT, "reset": True},
        timeout=180,
    )
    assert built.get("success") is True, built
    binding = built.get("binding")
    assert isinstance(binding, dict)
    assert binding.get("workspace_root") == WORKSPACE_ROOT
    assert binding.get("layer_stack_root") == LAYER_STACK_ROOT
    assert binding.get("base_manifest_version") == 1
    return binding


def phase05_call_row(
    *,
    case: str,
    metric: RuntimeCallMetric,
    concurrency: int,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "kind": "call",
        "case": case,
        "op": metric.op,
        "concurrency": concurrency,
        "label": metric.label,
        "success": metric.success,
        "status": metric.status,
        "changed_paths": list(metric.changed_paths),
        "conflict_reason": metric.conflict_reason,
        "wall_ms": round(metric.elapsed_ms, 3),
        "runtime_ms": round(selected_runtime_ms(metric), 3),
        "timings": _timings(metric.timings),
        **dict(extra or {}),
    }


def phase05_summary_row(
    *,
    case: str,
    binding: Mapping[str, object],
    concurrency: int,
    metrics: Sequence[RuntimeCallMetric],
    batch_wall_ms: float,
    correctness: Mapping[str, object],
    pass_bars: Mapping[str, object] | None = None,
) -> dict[str, object]:
    wall_values = [metric.elapsed_ms for metric in metrics]
    runtime_values = [selected_runtime_ms(metric) for metric in metrics]
    batch_seconds = batch_wall_ms / 1000.0
    parallel_factor = sum(wall_values) / batch_wall_ms if batch_wall_ms > 0 else 0.0
    return {
        "schema": SCHEMA,
        "kind": "summary",
        "case": case,
        "workspace_root": str(binding["workspace_root"]),
        "layer_stack_root": str(binding["layer_stack_root"]),
        "concurrency": concurrency,
        "calls": len(metrics),
        "batch_wall_ms": round(batch_wall_ms, 3),
        "per_call_wall_p50_ms": round(percentile(wall_values, 50), 3),
        "per_call_wall_p95_ms": round(percentile(wall_values, 95), 3),
        "per_call_wall_p99_ms": round(percentile(wall_values, 99), 3),
        "runtime_p99_ms": round(percentile(runtime_values, 99), 3),
        "parallel_factor": round(parallel_factor, 3),
        "parallel_efficiency": round(
            parallel_factor / max(float(concurrency), 1.0),
            3,
        ),
        "throughput_ops_s": round(
            len(metrics) / batch_seconds if batch_seconds > 0 else 0.0,
            3,
        ),
        "correctness": dict(correctness),
        "pass_bars": dict(pass_bars or {}),
    }


def write_phase05_jsonl_artifact(
    *,
    case: str,
    rows: Sequence[Mapping[str, object]],
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = (
        Path.cwd()
        / ".omc"
        / "results"
        / f"live-e2e-phase05-public-file-ops-{_safe_name(case)}-{stamp}.jsonl"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with artifact.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            file.write("\n")
    return artifact


async def public_reconcile(
    handle: SandboxHandle,
    expected: Mapping[str, str],
) -> None:
    for path, content in expected.items():
        result = await handle.tool.read_file(path)
        assert result.success, path
        assert result.exists, path
        assert result.content == content


async def raw_read(handle: SandboxHandle, path: str) -> str:
    result = await handle.raw_exec(
        handle.sandbox_id,
        f"cat -- {q(path)}",
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    return result.stdout


async def raw_exists(handle: SandboxHandle, path: str) -> bool:
    result = await handle.raw_exec(
        handle.sandbox_id,
        f"test -e {q(path)}",
        timeout=15,
    )
    return result.exit_code == 0


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def monotonic_ms() -> float:
    return time.perf_counter() * 1000.0


def _timings(timings: Mapping[str, float]) -> dict[str, float]:
    return {
        str(key): round(float(value), 6)
        for key, value in sorted(timings.items())
    }


def _validate_relative_path(path: str) -> None:
    posix = PurePosixPath(path)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError(f"test fixture path must be workspace-relative: {path!r}")


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value)
    return safe.strip("-") or "case"


__all__ = [
    "CONCURRENCIES",
    "LARGE_NEW_TAIL",
    "LARGE_OLD_TAIL",
    "LARGE_PATH",
    "LAYER_STACK_ROOT",
    "OUTSIDE_SYMLINK_TARGET",
    "SCHEMA",
    "edited_large_text_content",
    "env_float",
    "large_text_content",
    "monotonic_ms",
    "phase05_base_files",
    "phase05_call_row",
    "phase05_summary_row",
    "public_reconcile",
    "raw_exists",
    "raw_read",
    "seed_phase05_imported_base",
    "write_phase05_jsonl_artifact",
]
