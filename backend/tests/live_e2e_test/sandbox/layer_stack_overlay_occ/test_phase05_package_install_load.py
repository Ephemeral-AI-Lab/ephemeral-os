"""Phase 05 shell package-install performance probe.

This suite intentionally uses local synthetic package artifacts. It measures
the public shell path's workspace snapshot, command execution, capture, and OCC
publication costs without coupling the result to registry or network variance.
"""

from __future__ import annotations

import json
import os
import shlex
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sandbox.api.tool import _runtime as runtime_mod

from .._harness.concurrency import gather_with_barrier
from .._harness.integrated_cases import RuntimeCallMetric, percentile, q, timed_call
from .._harness.phase05_public_file_ops import LAYER_STACK_ROOT
from .._harness.sandbox_fixture import SandboxHandle, WORKSPACE_ROOT
from .._harness.workspace_base_public import selected_runtime_ms


pytestmark = pytest.mark.asyncio

SCHEMA = "sandbox.live_e2e.phase05_package_install.v1"
DEFAULT_CONCURRENCIES = (1, 5, 10, 20)
DEFAULT_FILE_COUNT = 300
DEFAULT_FILE_BYTES = 1024
PIP_WHEEL = "pkg-src/pip/eos_many_files_pkg-0.0.1-py3-none-any.whl"
NPM_TARBALL = "pkg-src/npm/eos-many-files-npm-0.0.1.tgz"

_Factory = Callable[[], Awaitable[tuple[object, RuntimeCallMetric]]]


async def test_phase05_shell_package_install_load_matrix_c1_c5_c10_c20(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    file_count = _env_int("EPHEMERALOS_PACKAGE_INSTALL_FILE_COUNT", DEFAULT_FILE_COUNT)
    file_bytes = _env_int("EPHEMERALOS_PACKAGE_INSTALL_FILE_BYTES", DEFAULT_FILE_BYTES)
    timeout_s = _env_int("EPHEMERALOS_PACKAGE_INSTALL_TIMEOUT", 240)
    concurrencies = _env_concurrencies()

    availability = await _package_manager_availability(handle)
    if not availability["pip"] and not availability["npm"]:
        pytest.skip("package install probe requires python3 -m pip or npm")

    binding = await _seed_package_install_base(
        handle,
        file_count=file_count,
        file_bytes=file_bytes,
    )

    rows: list[dict[str, object]] = [
        {
            "schema": SCHEMA,
            "kind": "environment",
            "availability": availability,
            "file_count": file_count,
            "file_bytes": file_bytes,
            "concurrencies": list(concurrencies),
        }
    ]
    all_metrics: list[RuntimeCallMetric] = []

    workloads = [
        ("pip_install_local_wheel", "pip"),
        ("npm_install_local_tgz", "npm"),
    ]
    for workload, manager in workloads:
        if not availability[manager]:
            rows.append(
                {
                    "schema": SCHEMA,
                    "kind": "skipped",
                    "case": workload,
                    "reason": f"{manager} is not available in the sandbox image",
                }
            )
            continue

        for concurrency in concurrencies:
            factories = _install_factories(
                handle,
                workload=workload,
                concurrency=concurrency,
                file_count=file_count,
                timeout_s=timeout_s,
            )
            started = time.perf_counter()
            results = await gather_with_barrier(factories)
            batch_wall_ms = (time.perf_counter() - started) * 1000.0
            metrics = [metric for _, metric in results]
            all_metrics.extend(metrics)

            assert len(results) == concurrency
            for result, metric in results:
                assert metric.success, metric.conflict_reason
                assert metric.status in {"ok", "committed", "accepted"}, result
                assert metric.changed_paths, metric

            summary = _summary_row(
                case=workload,
                binding=binding,
                concurrency=concurrency,
                file_count=file_count,
                file_bytes=file_bytes,
                metrics=metrics,
                batch_wall_ms=batch_wall_ms,
            )
            rows.append(summary)
            rows.extend(
                _call_row(
                    case=workload,
                    concurrency=concurrency,
                    file_count=file_count,
                    file_bytes=file_bytes,
                    metric=metric,
                )
                for metric in metrics
            )

    artifact = _write_jsonl_artifact(case="package_install_load_matrix", rows=rows)
    print(f"\n[phase05:package_install_load] artifact={artifact}")
    assert all_metrics, "package install probe did not execute any workload"


def _install_factories(
    handle: SandboxHandle,
    *,
    workload: str,
    concurrency: int,
    file_count: int,
    timeout_s: int,
) -> list[_Factory]:
    factories: list[_Factory] = []
    for index in range(concurrency):
        if workload == "pip_install_local_wheel":
            command = _pip_install_command(concurrency, index, file_count)
        elif workload == "npm_install_local_tgz":
            command = _npm_install_command(concurrency, index, file_count)
        else:
            raise AssertionError(f"unknown workload: {workload}")

        async def run(index: int = index, command: str = command):
            return await timed_call(
                f"phase05_package_{workload}_c{concurrency:02d}_{index:02d}",
                handle.tool.shell(
                    command,
                    timeout=timeout_s,
                    description=(
                        f"phase05 package install {workload} "
                        f"c={concurrency} i={index}"
                    ),
                ),
            )

        factories.append(run)
    return factories


def _pip_install_command(concurrency: int, index: int, file_count: int) -> str:
    target = f".pkg-install/pip/c{concurrency:02d}-{index:02d}"
    package_dir = f"{target}/eos_many_files_pkg"
    marker = f"{package_dir}/module_{file_count - 1:04d}.py"
    return (
        "set -euo pipefail; "
        "export PIP_DISABLE_PIP_VERSION_CHECK=1; "
        f"rm -rf {q(target)}; "
        f"python3 -m pip install --quiet --no-index --no-deps --no-cache-dir "
        f"--target {q(target)} {q(PIP_WHEEL)}; "
        f"test -f {q(marker)}; "
        f"test $(find {q(package_dir)} -type f | wc -l | tr -d ' ') -ge "
        f"{file_count}"
    )


def _npm_install_command(concurrency: int, index: int, file_count: int) -> str:
    target = f".pkg-install/npm/c{concurrency:02d}-{index:02d}"
    cache = f"/tmp/eos-phase05-npm-cache/c{concurrency:02d}-{index:02d}"
    package_dir = f"{target}/node_modules/eos-many-files-npm"
    marker = f"{package_dir}/files/file_{file_count - 1:04d}.txt"
    return (
        "set -euo pipefail; "
        f"rm -rf {q(target)} {q(cache)}; "
        f"npm install --silent --ignore-scripts --no-audit --no-fund "
        f"--package-lock=false --cache {q(cache)} --prefix {q(target)} "
        f"{q(NPM_TARBALL)}; "
        f"test -f {q(marker)}; "
        f"test $(find {q(package_dir)} -type f | wc -l | tr -d ' ') -ge "
        f"{file_count}"
    )


async def _package_manager_availability(handle: SandboxHandle) -> dict[str, bool]:
    result = await handle.raw_exec(
        handle.sandbox_id,
        "set -e; "
        "python3 -m pip --version >/dev/null 2>&1 && echo pip=1 || echo pip=0; "
        "command -v npm >/dev/null 2>&1 && echo npm=1 || echo npm=0",
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return {
        "pip": values.get("pip") == "1",
        "npm": values.get("npm") == "1",
    }


async def _seed_package_install_base(
    handle: SandboxHandle,
    *,
    file_count: int,
    file_bytes: int,
) -> dict[str, object]:
    script = r"""
import base64
import csv
import gzip
import hashlib
import io
import json
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
file_count = int(sys.argv[2])
file_bytes = int(sys.argv[3])

shutil.rmtree(root / "pkg-src", ignore_errors=True)
shutil.rmtree(root / ".pkg-install", ignore_errors=True)
(root / "pkg-src" / "pip").mkdir(parents=True, exist_ok=True)
(root / "pkg-src" / "npm").mkdir(parents=True, exist_ok=True)

(root / ".gitignore").write_text(
    ".pkg-install/\n"
    "node_modules/\n"
    ".npm-cache/\n"
    ".pip-cache/\n",
    encoding="utf-8",
)
(root / "README.md").write_text("# Phase 05 Package Install Base\n", encoding="utf-8")

def payload(index: int) -> str:
    seed = f"{index:04d}:"
    repeats = max(file_bytes // len(seed), 1)
    return (seed * repeats)[:file_bytes]

wheel_name = "eos_many_files_pkg-0.0.1-py3-none-any.whl"
wheel_path = root / "pkg-src" / "pip" / wheel_name
wheel_records = []

def add_wheel_file(zf, name: str, data: bytes) -> None:
    zf.writestr(name, data)
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    wheel_records.append((name, f"sha256={digest.decode()}", str(len(data))))

with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    add_wheel_file(zf, "eos_many_files_pkg/__init__.py", b"VALUE = 'phase05'\n")
    for index in range(file_count):
        body = (
            f"DATA_{index:04d} = "
            + repr(payload(index))
            + "\n"
        ).encode("utf-8")
        add_wheel_file(zf, f"eos_many_files_pkg/module_{index:04d}.py", body)
    dist = "eos_many_files_pkg-0.0.1.dist-info"
    add_wheel_file(
        zf,
        f"{dist}/METADATA",
        b"Metadata-Version: 2.1\nName: eos-many-files-pkg\nVersion: 0.0.1\n",
    )
    add_wheel_file(
        zf,
        f"{dist}/WHEEL",
        b"Wheel-Version: 1.0\nGenerator: phase05\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    )
    record_path = f"{dist}/RECORD"
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(wheel_records)
    writer.writerow((record_path, "", ""))
    zf.writestr(record_path, output.getvalue().encode("utf-8"))

tar_name = "eos-many-files-npm-0.0.1.tgz"
tar_path = root / "pkg-src" / "npm" / tar_name

def add_tar_file(tf, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    tf.addfile(info, io.BytesIO(data))

with gzip.GzipFile(tar_path, "wb", mtime=0) as gz:
    with tarfile.open(fileobj=gz, mode="w") as tf:
        package_json = json.dumps(
            {
                "name": "eos-many-files-npm",
                "version": "0.0.1",
                "main": "index.js",
                "files": ["index.js", "files"],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        add_tar_file(tf, "package/package.json", package_json)
        add_tar_file(tf, "package/index.js", b"module.exports = 'phase05';\n")
        for index in range(file_count):
            add_tar_file(
                tf,
                f"package/files/file_{index:04d}.txt",
                payload(index).encode("utf-8"),
            )
"""
    result = await handle.raw_exec(
        handle.sandbox_id,
        "python3 -c {script} {root} {count} {size}".format(
            script=shlex.quote(script),
            root=shlex.quote(WORKSPACE_ROOT),
            count=file_count,
            size=file_bytes,
        ),
        timeout=120,
    )
    assert result.exit_code == 0, result.stderr or result.stdout

    built = await runtime_mod.call_runtime_api(
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


def _summary_row(
    *,
    case: str,
    binding: Mapping[str, object],
    concurrency: int,
    file_count: int,
    file_bytes: int,
    metrics: Sequence[RuntimeCallMetric],
    batch_wall_ms: float,
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
        "file_count_per_package": file_count,
        "file_bytes_per_package_file": file_bytes,
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
        "changed_paths_p50": round(
            percentile([len(metric.changed_paths) for metric in metrics], 50),
            3,
        ),
        "changed_paths_p99": round(
            percentile([len(metric.changed_paths) for metric in metrics], 99),
            3,
        ),
        "timing_p99_ms": _timing_p99(metrics),
        "correctness": {
            "all_calls_accounted": len(metrics) == concurrency,
            "all_calls_succeeded": all(metric.success for metric in metrics),
            "unexpected_conflicts": sum(
                1 for metric in metrics if metric.conflict_reason
            ),
        },
    }


def _call_row(
    *,
    case: str,
    concurrency: int,
    file_count: int,
    file_bytes: int,
    metric: RuntimeCallMetric,
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "kind": "call",
        "case": case,
        "op": metric.op,
        "concurrency": concurrency,
        "file_count_per_package": file_count,
        "file_bytes_per_package_file": file_bytes,
        "label": metric.label,
        "success": metric.success,
        "status": metric.status,
        "changed_path_count": len(metric.changed_paths),
        "changed_path_sample": list(metric.changed_paths[:10]),
        "conflict_reason": metric.conflict_reason,
        "wall_ms": round(metric.elapsed_ms, 3),
        "runtime_ms": round(selected_runtime_ms(metric), 3),
        "timings": _timings(metric.timings),
    }


def _timing_p99(metrics: Sequence[RuntimeCallMetric]) -> dict[str, float]:
    keys = (
        "api.shell.total_s",
        "api.shell.overlay_s",
        "api.shell.occ_apply_s",
        "command_exec.prepare_snapshot_s",
        "command_exec.mount_workspace_s",
        "command_exec.run_command_s",
        "command_exec.capture_upperdir_s",
        "command_exec.release_snapshot_s",
        "occ.prepare.route_and_base_hash_s",
        "occ.apply.total_s",
        "occ.commit.total_s",
        "occ.serial.batch_size",
        "occ.apply.manifest_lag",
    )
    output: dict[str, float] = {}
    for key in keys:
        values = [
            float(metric.timings[key])
            for metric in metrics
            if key in metric.timings and metric.timings[key] is not None
        ]
        if not values:
            continue
        multiplier = 1.0 if key.endswith("batch_size") or key.endswith("manifest_lag") else 1000.0
        output[key] = round(percentile(values, 99) * multiplier, 3)
    return output


def _timings(timings: Mapping[str, float]) -> dict[str, float]:
    return {key: round(float(value), 6) for key, value in sorted(timings.items())}


def _write_jsonl_artifact(
    *,
    case: str,
    rows: Sequence[Mapping[str, object]],
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = (
        Path.cwd()
        / ".omc"
        / "results"
        / f"live-e2e-phase05-{case}-{stamp}.jsonl"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with artifact.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            file.write("\n")
    return artifact


def _env_concurrencies() -> tuple[int, ...]:
    raw = os.environ.get("EPHEMERALOS_PACKAGE_INSTALL_CONCURRENCIES")
    if raw is None or raw.strip() == "":
        return DEFAULT_CONCURRENCIES
    values = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    if not values:
        raise ValueError("EPHEMERALOS_PACKAGE_INSTALL_CONCURRENCIES cannot be empty")
    return values


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
