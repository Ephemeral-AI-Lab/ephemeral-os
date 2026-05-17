"""In-sandbox request snapshot performance probe shipped via ``raw_exec``.

The probe intentionally stays stdlib-only and runs inside the Daytona sandbox.
Pytest only renders this script, executes ``python3 -c <source>`` through the
existing live sandbox fixture, and parses the final JSON line.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUEST_SNAPSHOT_SCHEMA = "sandbox.live_e2e.request_snapshot_probe.v1"
REQUEST_SNAPSHOT_METRIC_LABEL = "REQUEST_SNAPSHOT_METRIC"

DEFAULT_BACKENDS = ("reflink_cp", "copy_cp", "tar_copy", "hardlink_cp")
DEFAULT_CONCURRENCIES = (1, 5, 10)
DEFAULT_WORKSPACE_SHAPES = (
    "baseline_repo",
    "many_small",
    "large_files",
    "mixed_generated",
)


_PROBE_SOURCE = r"""
import concurrent.futures
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone


CFG = json.loads(__CFG_JSON__)
SCHEMA = "sandbox.live_e2e.request_snapshot_probe.v1"
SOURCE_ROOT = CFG["source_root"]
RUN_ROOT = os.path.join(CFG["probe_root"], CFG["run_id"])
SNAPSHOT_ROOT = os.path.join(RUN_ROOT, "snapshots")
SCRATCH_ROOT = os.path.join(RUN_ROOT, "scratch")
METADATA_PATH = os.path.join(RUN_ROOT, "sources-metadata.json")
BENCH_ROOT = os.path.join(SOURCE_ROOT, ".snapshot-bench")
SENTINEL_REL = os.path.join(".snapshot-bench", "sentinel.txt")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = int(math.floor(rank))
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def round_ms(value):
    return round(float(value), 3)


def command_exists(name):
    return shutil.which(name) is not None


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)


def write_file(path, data):
    ensure_dir(os.path.dirname(path))
    with open(path, "wb") as fh:
        fh.write(data)


def write_large_file(path, size):
    ensure_dir(os.path.dirname(path))
    chunk = (b"0123456789abcdef" * 4096)
    remaining = int(size)
    with open(path, "wb") as fh:
        while remaining > 0:
            piece = chunk[: min(len(chunk), remaining)]
            fh.write(piece)
            remaining -= len(piece)


def reset_bench_root():
    remove_tree(BENCH_ROOT)
    ensure_dir(BENCH_ROOT)


def build_baseline_repo_shape():
    reset_bench_root()
    write_file(os.path.join(SOURCE_ROOT, SENTINEL_REL), b"baseline\n")


def build_many_small_shape():
    reset_bench_root()
    count = int(CFG["shape_sizes"]["many_small_files"])
    size = int(CFG["shape_sizes"]["many_small_bytes"])
    payload = b"x" * size
    base = os.path.join(BENCH_ROOT, "many-small")
    for index in range(count):
        path = os.path.join(base, "d%03d" % (index // 100), "f%05d.dat" % index)
        write_file(path, payload)
    write_file(os.path.join(SOURCE_ROOT, SENTINEL_REL), b"many-small\n")


def build_large_files_shape():
    reset_bench_root()
    count = int(CFG["shape_sizes"]["large_file_count"])
    size = int(CFG["shape_sizes"]["large_file_bytes"])
    base = os.path.join(BENCH_ROOT, "large-files")
    for index in range(count):
        write_large_file(os.path.join(base, "large-%02d.bin" % index), size)
    write_file(os.path.join(SOURCE_ROOT, SENTINEL_REL), b"large-files\n")


def build_mixed_generated_shape():
    reset_bench_root()
    small_count = int(CFG["shape_sizes"]["mixed_small_files"])
    small_size = int(CFG["shape_sizes"]["mixed_small_bytes"])
    large_count = int(CFG["shape_sizes"]["mixed_large_count"])
    large_size = int(CFG["shape_sizes"]["mixed_large_bytes"])
    payload = b"m" * small_size
    base = os.path.join(BENCH_ROOT, "mixed-generated")
    for index in range(small_count):
        path = os.path.join(base, "small", "d%03d" % (index // 100), "f%05d.dat" % index)
        write_file(path, payload)
    for index in range(large_count):
        write_large_file(os.path.join(base, "large", "asset-%02d.bin" % index), large_size)
    write_file(os.path.join(SOURCE_ROOT, SENTINEL_REL), b"mixed-generated\n")


SHAPE_BUILDERS = {
    "baseline_repo": build_baseline_repo_shape,
    "many_small": build_many_small_shape,
    "large_files": build_large_files_shape,
    "mixed_generated": build_mixed_generated_shape,
}


def build_shape(shape):
    try:
        builder = SHAPE_BUILDERS[shape]
    except KeyError:
        raise RuntimeError("unknown workspace shape: %s" % shape)
    builder()
    return workspace_metadata(shape)


def workspace_metadata(shape):
    files = 0
    dirs = 0
    total_bytes = 0
    largest = 0
    git_files = 0
    git_bytes = 0
    git_root = os.path.join(SOURCE_ROOT, ".git")
    for dirpath, dirnames, filenames in os.walk(SOURCE_ROOT):
        dirs += len(dirnames)
        in_git = dirpath == git_root or dirpath.startswith(git_root + os.sep)
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            files += 1
            total_bytes += size
            largest = max(largest, size)
            if in_git:
                git_files += 1
                git_bytes += size
    return {
        "workspace_shape": shape,
        "workspace_files": files,
        "workspace_dirs": dirs,
        "workspace_bytes": total_bytes,
        "workspace_largest_file_bytes": largest,
        "workspace_includes_git": os.path.isdir(git_root),
        "workspace_git_files": git_files,
        "workspace_git_bytes": git_bytes,
    }


def snapshot_command(backend, dest):
    if backend == "reflink_cp":
        return {
            "argv": ["cp", "-a", "--reflink=always", SOURCE_ROOT, dest],
            "requires": ("cp",),
        }
    if backend == "copy_cp":
        return {"argv": ["cp", "-a", SOURCE_ROOT, dest], "requires": ("cp",)}
    if backend == "hardlink_cp":
        return {"argv": ["cp", "-al", SOURCE_ROOT, dest], "requires": ("cp",)}
    if backend == "tar_copy":
        return {
            "shell": "cd %s && tar cf - . | tar xf - -C %s"
            % (sh_quote(SOURCE_ROOT), sh_quote(dest)),
            "requires": ("tar",),
            "precreate_dest": True,
        }
    raise RuntimeError("unknown backend: %s" % backend)


def sh_quote(value):
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def backend_requirements_available(backend):
    spec = snapshot_command(backend, os.path.join(SCRATCH_ROOT, "availability"))
    missing = [name for name in spec["requires"] if not command_exists(name)]
    return len(missing) == 0, missing


def run_create(backend, dest):
    spec = snapshot_command(backend, dest)
    if os.path.exists(dest):
        return {
            "path": dest,
            "ok": False,
            "elapsed_ms": 0.0,
            "rc": 1,
            "stderr": "snapshot destination already exists",
        }
    ensure_dir(os.path.dirname(dest))
    if spec.get("precreate_dest"):
        ensure_dir(dest)
    started = time.perf_counter()
    if "argv" in spec:
        proc = subprocess.run(spec["argv"], capture_output=True, text=True)
    else:
        proc = subprocess.run(spec["shell"], shell=True, capture_output=True, text=True)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "path": dest,
        "ok": proc.returncode == 0,
        "elapsed_ms": elapsed_ms,
        "rc": proc.returncode,
        "stderr": (proc.stderr or "")[-500:],
    }


def run_destroy(path):
    started = time.perf_counter()
    proc = subprocess.run(["rm", "-rf", path], capture_output=True, text=True)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "path": path,
        "ok": proc.returncode == 0 and not os.path.exists(path),
        "elapsed_ms": elapsed_ms,
        "rc": proc.returncode,
        "stderr": (proc.stderr or "")[-500:],
        "exists_after": os.path.exists(path),
    }


def run_parallel(count, operation):
    barrier = threading.Barrier(count + 1)

    def worker(index):
        try:
            barrier.wait()
            return operation(index)
        except Exception as exc:
            return {
                "ok": False,
                "elapsed_ms": 0.0,
                "rc": -1,
                "stderr": "%s: %s" % (type(exc).__name__, exc),
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(worker, index) for index in range(count)]
        batch_start = time.perf_counter()
        barrier.wait()
        results = [future.result() for future in futures]
        batch_wall_ms = (time.perf_counter() - batch_start) * 1000.0
    return results, batch_wall_ms


def set_sentinel(value):
    path = os.path.join(SOURCE_ROOT, SENTINEL_REL)
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(value)


def read_snapshot_sentinel(snapshot_path):
    path = os.path.join(snapshot_path, SENTINEL_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        return "__READ_ERROR__:%s:%s" % (type(exc).__name__, exc)


def count_leftover_snapshot_dirs(batch_root):
    if not os.path.isdir(batch_root):
        return 0
    count = 0
    for name in os.listdir(batch_root):
        path = os.path.join(batch_root, name)
        if os.path.isdir(path):
            count += 1
    return count


def summarize_elapsed(results, key="elapsed_ms"):
    values = [float(row.get(key, 0.0)) for row in results]
    return {
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else 0.0,
    }


def measure_batch(shape, backend, concurrency, metadata, baseline_by_pair, batch_index):
    batch_root = os.path.join(
        SNAPSHOT_ROOT,
        shape,
        backend,
        "c%02d-b%03d" % (concurrency, batch_index),
    )
    remove_tree(batch_root)
    ensure_dir(batch_root)

    requirements_ok, missing = backend_requirements_available(backend)
    errors = []
    if missing:
        errors.append("missing commands: " + ",".join(missing))
        return unavailable_row(shape, backend, concurrency, metadata, errors)

    before = "before:%s:%s:%s:%s\n" % (shape, backend, concurrency, batch_index)
    after = "after:%s:%s:%s:%s\n" % (shape, backend, concurrency, batch_index)
    set_sentinel(before)

    snapshot_paths = [
        os.path.join(batch_root, "worker-%02d" % index) for index in range(concurrency)
    ]
    if len(set(snapshot_paths)) != len(snapshot_paths):
        errors.append("duplicate snapshot paths generated")

    create_results, create_batch_wall_ms = run_parallel(
        concurrency,
        lambda index: run_create(backend, snapshot_paths[index]),
    )
    create_failures = [row for row in create_results if not row.get("ok")]
    if create_failures:
        errors.extend(
            "create worker=%s rc=%s stderr=%s"
            % (idx, row.get("rc"), row.get("stderr", ""))
            for idx, row in enumerate(create_results)
            if not row.get("ok")
        )

    set_sentinel(after)
    freeze_reads = {
        path: read_snapshot_sentinel(path)
        for path, row in zip(snapshot_paths, create_results)
        if row.get("ok")
    }
    freeze_ok = bool(freeze_reads) and all(value == before for value in freeze_reads.values())
    if not freeze_ok and freeze_reads:
        errors.append("freeze check failed")

    destroy_inputs = [path for path in snapshot_paths if os.path.exists(path)]
    if destroy_inputs:
        destroy_results, destroy_batch_wall_ms = run_parallel(
            len(destroy_inputs),
            lambda index: run_destroy(destroy_inputs[index]),
        )
    else:
        destroy_results = []
        destroy_batch_wall_ms = 0.0
    destroy_failures = [row for row in destroy_results if not row.get("ok")]
    if destroy_failures:
        errors.extend(
            "destroy path=%s rc=%s exists_after=%s stderr=%s"
            % (row.get("path"), row.get("rc"), row.get("exists_after"), row.get("stderr", ""))
            for row in destroy_failures
        )

    leftover_snapshot_dirs = count_leftover_snapshot_dirs(batch_root)
    if leftover_snapshot_dirs:
        errors.append("leftover snapshot dirs: %s" % leftover_snapshot_dirs)

    create_summary = summarize_elapsed(create_results)
    destroy_summary = summarize_elapsed(destroy_results)
    baseline_key = (shape, backend)
    if concurrency == 1 and create_results and destroy_results:
        baseline_by_pair[baseline_key] = {
            "create_ms": create_summary["p50"],
            "destroy_ms": destroy_summary["p50"],
        }
    baseline = baseline_by_pair.get(baseline_key, {})
    baseline_create_ms = float(baseline.get("create_ms") or create_summary["p50"])
    baseline_destroy_ms = float(baseline.get("destroy_ms") or destroy_summary["p50"])
    parallel_factor_create = safe_parallel_factor(
        baseline_create_ms, concurrency, create_batch_wall_ms
    )
    parallel_factor_destroy = safe_parallel_factor(
        baseline_destroy_ms, concurrency, destroy_batch_wall_ms
    )

    return {
        "schema": SCHEMA,
        "run_id": CFG["run_id"],
        "scenario": CFG["scenario"],
        "backend": backend,
        "workspace_shape": shape,
        "concurrency": concurrency,
        "available": not create_failures and requirements_ok,
        "viable": backend != "hardlink_cp" and not create_failures and freeze_ok,
        "freeze_ok": freeze_ok,
        "calls": concurrency,
        "workspace_files": metadata["workspace_files"],
        "workspace_dirs": metadata["workspace_dirs"],
        "workspace_bytes": metadata["workspace_bytes"],
        "workspace_largest_file_bytes": metadata["workspace_largest_file_bytes"],
        "workspace_includes_git": metadata["workspace_includes_git"],
        "workspace_git_files": metadata["workspace_git_files"],
        "workspace_git_bytes": metadata["workspace_git_bytes"],
        "create_batch_wall_ms": round_ms(create_batch_wall_ms),
        "create_per_call_p50_ms": round_ms(create_summary["p50"]),
        "create_per_call_p95_ms": round_ms(create_summary["p95"]),
        "create_per_call_p99_ms": round_ms(create_summary["p99"]),
        "create_per_call_max_ms": round_ms(create_summary["max"]),
        "destroy_batch_wall_ms": round_ms(destroy_batch_wall_ms),
        "destroy_per_call_p50_ms": round_ms(destroy_summary["p50"]),
        "destroy_per_call_p99_ms": round_ms(destroy_summary["p99"]),
        "parallel_factor_create": round(parallel_factor_create, 3),
        "parallel_efficiency_create": round(parallel_factor_create / concurrency, 3),
        "parallel_factor_destroy": round(parallel_factor_destroy, 3),
        "parallel_efficiency_destroy": round(parallel_factor_destroy / concurrency, 3),
        "leftover_snapshot_dirs": leftover_snapshot_dirs,
        "snapshot_paths_unique": len(set(snapshot_paths)) == len(snapshot_paths),
        "errors": errors,
    }


def unavailable_row(shape, backend, concurrency, metadata, errors):
    return {
        "schema": SCHEMA,
        "run_id": CFG["run_id"],
        "scenario": CFG["scenario"],
        "backend": backend,
        "workspace_shape": shape,
        "concurrency": concurrency,
        "available": False,
        "viable": False,
        "freeze_ok": False,
        "calls": concurrency,
        "workspace_files": metadata["workspace_files"],
        "workspace_dirs": metadata["workspace_dirs"],
        "workspace_bytes": metadata["workspace_bytes"],
        "workspace_largest_file_bytes": metadata["workspace_largest_file_bytes"],
        "workspace_includes_git": metadata["workspace_includes_git"],
        "workspace_git_files": metadata["workspace_git_files"],
        "workspace_git_bytes": metadata["workspace_git_bytes"],
        "create_batch_wall_ms": 0.0,
        "create_per_call_p50_ms": 0.0,
        "create_per_call_p95_ms": 0.0,
        "create_per_call_p99_ms": 0.0,
        "create_per_call_max_ms": 0.0,
        "destroy_batch_wall_ms": 0.0,
        "destroy_per_call_p50_ms": 0.0,
        "destroy_per_call_p99_ms": 0.0,
        "parallel_factor_create": 0.0,
        "parallel_efficiency_create": 0.0,
        "parallel_factor_destroy": 0.0,
        "parallel_efficiency_destroy": 0.0,
        "leftover_snapshot_dirs": 0,
        "snapshot_paths_unique": True,
        "errors": errors,
    }


def safe_parallel_factor(baseline_ms, concurrency, batch_wall_ms):
    if baseline_ms <= 0 or batch_wall_ms <= 0:
        return 0.0
    return (baseline_ms * concurrency) / batch_wall_ms


def choose_recommended_backend(rows):
    viable = [row for row in rows if row.get("viable") and row["concurrency"] == 1]
    if not viable:
        return None
    order = {"reflink_cp": 0, "copy_cp": 1, "tar_copy": 2}
    viable.sort(
        key=lambda row: (
            order.get(row["backend"], 99),
            row["create_per_call_p99_ms"],
            row["destroy_per_call_p99_ms"],
        )
    )
    return viable[0]["backend"]


def write_metadata(records):
    ensure_dir(os.path.dirname(METADATA_PATH))
    with open(METADATA_PATH, "w", encoding="utf-8") as fh:
        json.dump(records, fh, sort_keys=True, separators=(",", ":"))


def main():
    started = time.perf_counter()
    rows = []
    metadata_records = []
    baseline_by_pair = {}
    batch_index = 0
    ensure_dir(SNAPSHOT_ROOT)
    ensure_dir(SCRATCH_ROOT)
    try:
        for shape in CFG["workspace_shapes"]:
            metadata = build_shape(shape)
            metadata_records.append(metadata)
            write_metadata(metadata_records)
            shape_viable_backends = set()
            for backend in CFG["backends"]:
                concurrencies = CFG["concurrencies"]
                if CFG.get("viable_only") and backend == "hardlink_cp":
                    concurrencies = [1]
                for concurrency in concurrencies:
                    if CFG.get("viable_only") and concurrency > 1:
                        if backend not in shape_viable_backends:
                            continue
                    row = measure_batch(
                        shape,
                        backend,
                        int(concurrency),
                        metadata,
                        baseline_by_pair,
                        batch_index,
                    )
                    batch_index += 1
                    rows.append(row)
                    if row.get("viable"):
                        shape_viable_backends.add(backend)
        payload = {
            "schema": SCHEMA,
            "run_id": CFG["run_id"],
            "scenario": CFG["scenario"],
            "started_at": CFG["started_at"],
            "finished_at": now_iso(),
            "elapsed_ms": round_ms((time.perf_counter() - started) * 1000.0),
            "source_root": SOURCE_ROOT,
            "run_root": RUN_ROOT,
            "workspace_shapes": CFG["workspace_shapes"],
            "backends": CFG["backends"],
            "concurrencies": CFG["concurrencies"],
            "recommended_backend": choose_recommended_backend(rows),
            "rows": rows,
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
    finally:
        remove_tree(RUN_ROOT)


if __name__ == "__main__":
    main()
"""


def request_snapshot_probe_command(
    *,
    scenario: str,
    source_root: str = "/testbed",
    workspace_shapes: Sequence[str] = DEFAULT_WORKSPACE_SHAPES,
    backends: Sequence[str] = DEFAULT_BACKENDS,
    concurrencies: Sequence[int] = DEFAULT_CONCURRENCIES,
    viable_only: bool = False,
    run_id: str | None = None,
) -> str:
    """Return a ``python3 -c`` command that executes the in-sandbox probe."""
    source = request_snapshot_probe_source(
        scenario=scenario,
        source_root=source_root,
        workspace_shapes=workspace_shapes,
        backends=backends,
        concurrencies=concurrencies,
        viable_only=viable_only,
        run_id=run_id,
    )
    return "python3 -c " + shlex.quote(source)


def request_snapshot_probe_source(
    *,
    scenario: str,
    source_root: str = "/testbed",
    workspace_shapes: Sequence[str] = DEFAULT_WORKSPACE_SHAPES,
    backends: Sequence[str] = DEFAULT_BACKENDS,
    concurrencies: Sequence[int] = DEFAULT_CONCURRENCIES,
    viable_only: bool = False,
    run_id: str | None = None,
) -> str:
    cfg = {
        "scenario": scenario,
        "source_root": source_root,
        "probe_root": "/tmp/eos-sandbox-runtime/request-snapshot-probe",
        "run_id": run_id or _default_run_id(scenario),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "workspace_shapes": list(workspace_shapes),
        "backends": list(backends),
        "concurrencies": [int(value) for value in concurrencies],
        "viable_only": viable_only,
        "shape_sizes": _shape_sizes_from_env(),
    }
    return _PROBE_SOURCE.replace("__CFG_JSON__", repr(json.dumps(cfg)))


def parse_request_snapshot_payload(stdout: str) -> dict[str, Any]:
    """Parse the final JSON object emitted by the in-sandbox probe."""
    for line in reversed(stdout.strip().splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        payload = json.loads(text)
        if payload.get("schema") == REQUEST_SNAPSHOT_SCHEMA:
            return payload
    raise ValueError("request snapshot probe did not emit a schema JSON payload")


def emit_request_snapshot_metrics(payload: dict[str, Any]) -> None:
    """Print stable metric lines for pytest ``-s`` output."""
    for row in payload.get("rows", []):
        print(
            REQUEST_SNAPSHOT_METRIC_LABEL
            + " "
            + json.dumps(row, sort_keys=True, separators=(",", ":")),
            flush=True,
        )


def write_request_snapshot_jsonl(payload: dict[str, Any]) -> Path:
    """Append row metrics to the optional host-side JSONL artifact."""
    path = _jsonl_path()
    with path.open("a", encoding="utf-8") as file:
        for row in payload.get("rows", []):
            file.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            file.write("\n")
    return path


def configured_workspace_shapes(
    default: Sequence[str] = DEFAULT_WORKSPACE_SHAPES,
) -> tuple[str, ...]:
    return _csv_env("EPHEMERALOS_REQUEST_SNAPSHOT_SHAPES", default)


def configured_backends(default: Sequence[str] = DEFAULT_BACKENDS) -> tuple[str, ...]:
    return _csv_env("EPHEMERALOS_REQUEST_SNAPSHOT_BACKENDS", default)


def configured_concurrencies(
    default: Sequence[int] = DEFAULT_CONCURRENCIES,
) -> tuple[int, ...]:
    raw = os.environ.get("EPHEMERALOS_REQUEST_SNAPSHOT_CONCURRENCIES", "").strip()
    if not raw:
        return tuple(int(value) for value in default)
    return tuple(int(part) for part in raw.split(",") if part.strip())


def configured_timeout(default: int = 1800) -> int:
    raw = os.environ.get("EPHEMERALOS_REQUEST_SNAPSHOT_TIMEOUT", "").strip()
    return int(raw) if raw else default


def viable_backend_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in payload.get("rows", [])
        if row.get("backend") != "hardlink_cp" and row.get("available") and row.get("freeze_ok")
    ]


def _shape_sizes_from_env() -> dict[str, int]:
    return {
        "many_small_files": _int_env("EPHEMERALOS_REQUEST_SNAPSHOT_MANY_SMALL_FILES", 1000),
        "many_small_bytes": _int_env("EPHEMERALOS_REQUEST_SNAPSHOT_MANY_SMALL_BYTES", 4096),
        "large_file_count": _int_env("EPHEMERALOS_REQUEST_SNAPSHOT_LARGE_FILE_COUNT", 4),
        "large_file_bytes": _int_env(
            "EPHEMERALOS_REQUEST_SNAPSHOT_LARGE_FILE_BYTES",
            16 * 1024 * 1024,
        ),
        "mixed_small_files": _int_env("EPHEMERALOS_REQUEST_SNAPSHOT_MIXED_SMALL_FILES", 2000),
        "mixed_small_bytes": _int_env("EPHEMERALOS_REQUEST_SNAPSHOT_MIXED_SMALL_BYTES", 4096),
        "mixed_large_count": _int_env("EPHEMERALOS_REQUEST_SNAPSHOT_MIXED_LARGE_COUNT", 2),
        "mixed_large_bytes": _int_env(
            "EPHEMERALOS_REQUEST_SNAPSHOT_MIXED_LARGE_BYTES",
            8 * 1024 * 1024,
        ),
    }


def _jsonl_path() -> Path:
    configured = os.environ.get("EPHEMERALOS_REQUEST_SNAPSHOT_JSONL", "").strip()
    if configured:
        path = Path(configured)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = Path.cwd() / ".omc" / "results" / f"live-e2e-request-snapshot-probe-{stamp}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _default_run_id(scenario: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in scenario)
    return f"{safe}-{stamp}-{os.getpid()}"


def _csv_env(name: str, default: Iterable[str]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return tuple(default)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


__all__ = [
    "DEFAULT_BACKENDS",
    "DEFAULT_CONCURRENCIES",
    "DEFAULT_WORKSPACE_SHAPES",
    "REQUEST_SNAPSHOT_METRIC_LABEL",
    "REQUEST_SNAPSHOT_SCHEMA",
    "configured_backends",
    "configured_concurrencies",
    "configured_timeout",
    "configured_workspace_shapes",
    "emit_request_snapshot_metrics",
    "parse_request_snapshot_payload",
    "request_snapshot_probe_command",
    "request_snapshot_probe_source",
    "viable_backend_rows",
    "write_request_snapshot_jsonl",
]
