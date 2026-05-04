"""Shared fixtures and probes for live per-call snapshot experiments.

These tests intentionally use a real Daytona sandbox and raw
``sandbox.process.exec`` as the live substrate. Production layer-stack/OCC
binding tests are marked xfail until the typed sandbox API is wired to real
Daytona sandboxes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shlex
import textwrap
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import pytest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveExecResult:
    command: str
    stdout: str
    exit_code: int
    elapsed_ms: float
    error: str | None = None


@dataclass(frozen=True)
class LiveSnapshotSandbox:
    sandbox_id: str
    sandbox: Any


def full_experiment_enabled() -> bool:
    return os.environ.get("EOS_LIVE_SNAPSHOT_FULL") == "1"


def depth_matrix(*, include_depth_200: bool = False) -> list[int]:
    if not full_experiment_enabled():
        return [1, 5, 10]
    depths = [1, 5, 10, 30, 50, 80, 100]
    if include_depth_200:
        depths.append(200)
    return depths


def print_live_metric(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    print(json.dumps(payload, sort_keys=True), flush=True)
    logger.info("%s", json.dumps(payload, sort_keys=True))


def _daytona_configured() -> bool:
    try:
        from sandbox.providers.daytona.client.credentials import load_credentials

        api_key, api_url, _target = load_credentials()
    except Exception:
        return False
    return bool(api_key and api_url)


@pytest.fixture(scope="session")
def live_snapshot_sandbox_info() -> dict[str, Any]:
    if not _daytona_configured():
        pytest.skip("Daytona credentials are not configured")

    from sandbox.providers.daytona.bootstrap import bootstrap_daytona_provider
    from sandbox.testing import create_test_sandbox, delete_test_sandbox

    bootstrap_daytona_provider()
    info = create_test_sandbox("per-call-snapshot-live")
    print_live_metric("sandbox.created", sandbox_id=info["id"])
    try:
        yield info
    finally:
        delete_test_sandbox(str(info["id"]))
        print_live_metric("sandbox.deleted", sandbox_id=info["id"])


@pytest.fixture()
async def live_snapshot_sandbox(live_snapshot_sandbox_info: dict[str, Any]) -> LiveSnapshotSandbox:
    from sandbox.providers.daytona.client.async_ import get_async_sandbox

    sandbox_id = str(live_snapshot_sandbox_info["id"])
    sandbox = await get_async_sandbox(sandbox_id)
    return LiveSnapshotSandbox(sandbox_id=sandbox_id, sandbox=sandbox)


async def run_live_command(
    env: LiveSnapshotSandbox,
    command: str,
    *,
    timeout: int = 60,
    label: str,
) -> LiveExecResult:
    wrapped = f"env LC_ALL=C LANG=C bash -lc {shlex.quote(command)}"
    start = time.monotonic()
    try:
        response = await env.sandbox.process.exec(wrapped, timeout=timeout)
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        result = LiveExecResult(
            command=command,
            stdout="",
            exit_code=-99,
            elapsed_ms=elapsed_ms,
            error=str(exc)[:500],
        )
        print_live_metric(
            "live.exec",
            label=label,
            exit_code=result.exit_code,
            elapsed_ms=round(elapsed_ms, 2),
            error=result.error,
        )
        return result

    elapsed_ms = (time.monotonic() - start) * 1000
    stdout = str(getattr(response, "result", "") or "")
    result = LiveExecResult(
        command=command,
        stdout=stdout,
        exit_code=int(getattr(response, "exit_code", 0) or 0),
        elapsed_ms=elapsed_ms,
    )
    print_live_metric(
        "live.exec",
        label=label,
        exit_code=result.exit_code,
        elapsed_ms=round(elapsed_ms, 2),
        stdout_tail=result.stdout[-300:],
    )
    return result


def assert_success(result: LiveExecResult) -> None:
    assert result.exit_code == 0, (
        f"command failed with exit={result.exit_code} error={result.error}\n"
        f"stdout tail:\n{result.stdout[-2000:]}"
    )


def parse_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AssertionError(f"no JSON object found in stdout:\n{stdout[-2000:]}")


async def require_commands(env: LiveSnapshotSandbox, *names: str) -> None:
    quoted_names = " ".join(shlex.quote(name) for name in names)
    command = (
        "missing=0; "
        f"for name in {quoted_names}; do "
        'command -v "$name" >/dev/null 2>&1 || { echo "$name"; missing=1; }; '
        "done; exit $missing"
    )
    result = await run_live_command(
        env,
        command,
        timeout=30,
        label="require_commands:" + ",".join(names),
    )
    if result.exit_code != 0:
        missing = ", ".join(line.strip() for line in result.stdout.splitlines() if line.strip())
        pytest.skip(f"live sandbox image is missing required command(s): {missing}")


async def make_workdir(env: LiveSnapshotSandbox, prefix: str) -> str:
    safe_prefix = "".join(ch for ch in prefix if ch.isalnum() or ch in "-_")
    command = f"mktemp -d /tmp/eos_{safe_prefix}_{uuid.uuid4().hex[:8]}_XXXXXX"
    result = await run_live_command(env, command, timeout=30, label=f"{prefix}.mktemp")
    assert_success(result)
    return result.stdout.strip().splitlines()[-1]


def p95_ms(samples: Iterable[float]) -> float:
    return _percentile(samples, 95)


def p99_ms(samples: Iterable[float]) -> float:
    return _percentile(samples, 99)


def _percentile(samples: Iterable[float], q: int) -> float:
    values = sorted(float(sample) for sample in samples)
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, math.ceil(len(values) * (q / 100)) - 1))
    return values[index]


def xfail_production_binding_missing(experiment: str) -> None:
    pytest.xfail(
        f"{experiment}: production sandbox.api layer-stack/OCC live binding is not wired "
        "to real Daytona sandboxes yet"
    )


def overlay_probe_command(
    *,
    depths: list[int],
    iterations: int,
    read_files: int = 0,
    write_check: bool = False,
) -> str:
    script = r"""
import ctypes
import json
import math
import os
import pathlib
import shutil
import tempfile
import time
import traceback

DEPTHS = __DEPTHS__
ITERATIONS = __ITERATIONS__
READ_FILES = __READ_FILES__
WRITE_CHECK = __WRITE_CHECK__

libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_ulong,
    ctypes.c_char_p,
]
libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
MNT_DETACH = 2


def percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * (q / 100)) - 1))
    return ordered[index]


def mount_overlay(target, opts):
    start = time.perf_counter()
    rc = libc.mount(b"overlay", str(target).encode(), b"overlay", 0, opts.encode())
    elapsed_ms = (time.perf_counter() - start) * 1000
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), opts)
    return elapsed_ms


def mount_tmpfs(target):
    rc = libc.mount(b"tmpfs", str(target).encode(), b"tmpfs", 0, b"size=512m")
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(target))


def unmount_overlay(target):
    rc = libc.umount2(str(target).encode(), MNT_DETACH)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(target))


def build_layers(root, depth):
    layers = []
    layer_root = root / "layers"
    for index in range(depth):
        layer = layer_root / f"l{index}"
        layer.mkdir(parents=True, exist_ok=True)
        if index == 0:
            (layer / "base.txt").write_text("base\n", encoding="utf-8")
            if READ_FILES:
                tree = layer / "tree"
                tree.mkdir()
                for file_index in range(READ_FILES):
                    shard = tree / f"{file_index // 100:03d}"
                    shard.mkdir(exist_ok=True)
                    (shard / f"f{file_index:05d}.txt").write_text(
                        f"{file_index}:base\n",
                        encoding="utf-8",
                    )
        elif index % 5 == 0:
            (layer / f"marker_{index}.txt").write_text(f"layer {index}\n", encoding="utf-8")
        layers.append(layer)
    return layers


def read_tree(merged):
    start = time.perf_counter()
    total = 0
    count = 0
    tree = merged / "tree"
    if tree.exists():
        for path in sorted(tree.rglob("*.txt")):
            total += len(path.read_bytes())
            count += 1
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {"elapsed_ms": elapsed_ms, "bytes": total, "files": count}


def run_depth(depth):
    root = pathlib.Path(tempfile.mkdtemp(prefix=f"eos_overlay_depth_{depth}_"))
    mount_samples = []
    read_passes = []
    tmpfs_mounted = False
    try:
        mount_tmpfs(root)
        tmpfs_mounted = True
        layers = build_layers(root, depth)
        lowerdir = ":".join(str(layer) for layer in reversed(layers))
        for iteration in range(ITERATIONS):
            upper = root / f"upper_{iteration}"
            work = root / f"work_{iteration}"
            merged = root / f"merged_{iteration}"
            upper.mkdir()
            work.mkdir()
            merged.mkdir()
            opts = f"lowerdir={lowerdir},upperdir={upper},workdir={work},userxattr"
            mount_ms = mount_overlay(merged, opts)
            mount_samples.append(mount_ms)
            try:
                assert (merged / "base.txt").read_text(encoding="utf-8") == "base\n"
                if WRITE_CHECK:
                    output = merged / f"write_check_{depth}_{iteration}.txt"
                    output.write_text("through overlay\n", encoding="utf-8")
                    upper_output = upper / output.name
                    assert upper_output.read_text(encoding="utf-8") == "through overlay\n"
                if READ_FILES:
                    read_passes.append(read_tree(merged))
                    read_passes.append(read_tree(merged))
            finally:
                unmount_overlay(merged)
        return {
            "depth": depth,
            "iterations": ITERATIONS,
            "mount_p50_ms": percentile(mount_samples, 50),
            "mount_p95_ms": percentile(mount_samples, 95),
            "mount_p99_ms": percentile(mount_samples, 99),
            "mount_samples_ms": mount_samples,
            "read_passes": read_passes,
            "error": None,
        }
    except BaseException as exc:
        return {
            "depth": depth,
            "iterations": ITERATIONS,
            "mount_samples_ms": mount_samples,
            "read_passes": read_passes,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=5),
        }
    finally:
        if tmpfs_mounted:
            try:
                unmount_overlay(root)
            except BaseException:
                pass
        shutil.rmtree(root, ignore_errors=True)


print(json.dumps({"depths": [run_depth(depth) for depth in DEPTHS]}, sort_keys=True))
"""
    script = (
        script.replace("__DEPTHS__", json.dumps(depths))
        .replace("__ITERATIONS__", str(iterations))
        .replace("__READ_FILES__", str(read_files))
        .replace("__WRITE_CHECK__", "True" if write_check else "False")
    )
    return "set -euo pipefail\nunshare -Urm python3 - <<'PY'\n" + script + "\nPY"


def python_json_command(source: str) -> str:
    return "set -euo pipefail\npython3 - <<'PY'\n" + textwrap.dedent(source).strip() + "\nPY"
