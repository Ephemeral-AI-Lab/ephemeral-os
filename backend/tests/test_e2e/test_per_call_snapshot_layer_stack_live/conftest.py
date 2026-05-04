"""Shared fixtures and probes for per-call snapshot layer-stack experiments.

The experiments exercise the public ``sandbox.api`` verbs against the same
OCC + overlay binding used by production tool calls. Raw provider exec stays
out of the test path except where lower-level lifecycle tests cover it directly.
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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Iterable

import pytest

from sandbox.api import SearchReplaceEdit
from sandbox.control.ops.runtime_services import (
    RemoteRuntimeServiceBinding,
    ShellBatchCall,
    create_remote_runtime_services,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveExecResult:
    command: str
    stdout: str
    exit_code: int
    elapsed_ms: float
    success: bool = True
    stderr: str = ""
    status: str = ""
    changed_paths: tuple[str, ...] = ()
    conflict_reason: str | None = None
    timings: dict[str, float] | None = None
    error: str | None = None


LiveSnapshotSandbox = RemoteRuntimeServiceBinding


def barrier_overlay(env: LiveSnapshotSandbox, *, parties: int):
    return env.barrier_overlay(parties=parties)


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


@pytest.fixture()
def daytona_snapshot_sandbox_info() -> dict[str, object]:
    if not _daytona_configured():
        pytest.skip("Daytona credentials are not configured")

    from sandbox.providers.daytona.bootstrap import bootstrap_daytona_provider
    from sandbox.testing import create_test_sandbox, delete_test_sandbox

    bootstrap_daytona_provider()
    info = create_test_sandbox("per-call-snapshot-live")
    sandbox_id = str(info["id"])
    env = create_remote_runtime_services(
        sandbox_id=sandbox_id,
        layer_stack_root=f"/tmp/eos-layer-stack-{uuid.uuid4().hex[:8]}",
    )
    print_live_metric(
        "sandbox.created",
        backend="daytona",
        sandbox_id=sandbox_id,
        layer_stack_root=env.layer_stack_root,
    )
    try:
        yield {"id": sandbox_id, "env": env}
    finally:
        try:
            delete_test_sandbox(sandbox_id)
        finally:
            env.dispose()
            print_live_metric("sandbox.deleted", backend="daytona", sandbox_id=sandbox_id)


@pytest.fixture()
def live_snapshot_sandbox_info(
    request: pytest.FixtureRequest,
) -> dict[str, object]:
    yield request.getfixturevalue("daytona_snapshot_sandbox_info")


@pytest.fixture()
async def live_snapshot_sandbox(
    live_snapshot_sandbox_info: dict[str, object],
) -> LiveSnapshotSandbox:
    env = live_snapshot_sandbox_info["env"]
    assert isinstance(env, RemoteRuntimeServiceBinding)
    return env


async def run_live_command(
    env: LiveSnapshotSandbox,
    command: str,
    *,
    timeout: int = 60,
    label: str,
) -> LiveExecResult:
    api_command = f"export LC_ALL=C LANG=C; {command}"
    start = time.monotonic()
    try:
        response = await env.shell(
            command=api_command,
            timeout=timeout,
            cwd=".",
            actor=env.actor(label),
            description=label,
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        result = LiveExecResult(
            command=command,
            stdout="",
            exit_code=-99,
            elapsed_ms=elapsed_ms,
            success=False,
            error=str(exc)[:500],
        )
        print_live_metric(
            "sandbox.api.shell",
            label=label,
            exit_code=result.exit_code,
            elapsed_ms=round(elapsed_ms, 2),
            error=result.error,
        )
        return result

    elapsed_ms = (time.monotonic() - start) * 1000
    result = LiveExecResult(
        command=command,
        stdout=response.stdout,
        stderr=response.stderr,
        exit_code=response.exit_code,
        elapsed_ms=elapsed_ms,
        success=response.success and response.exit_code == 0,
        status=response.status,
        changed_paths=tuple(response.changed_paths),
        conflict_reason=response.conflict_reason,
        timings=dict(response.timings),
    )
    print_live_metric(
        "sandbox.api.shell",
        label=label,
        exit_code=result.exit_code,
        success=result.success,
        status=result.status,
        changed_paths=list(result.changed_paths),
        conflict_reason=result.conflict_reason,
        elapsed_ms=round(elapsed_ms, 2),
        stdout_tail=result.stdout[-300:],
        stderr_tail=result.stderr[-300:],
    )
    return result


async def run_live_commands(
    env: LiveSnapshotSandbox,
    commands: Sequence[str],
    *,
    timeout: int = 60,
    label: str,
    labels: Sequence[str] | None = None,
    max_concurrency: int = 32,
) -> list[LiveExecResult]:
    item_labels = list(labels or ())
    if not item_labels:
        item_labels = [f"{label}.{index}" for index in range(len(commands))]
    if len(item_labels) != len(commands):
        raise ValueError("labels must match commands")

    api_commands = [f"export LC_ALL=C LANG=C; {command}" for command in commands]
    start = time.monotonic()
    try:
        responses = await env.shell_batch(
            [
                ShellBatchCall(
                    command=api_command,
                    timeout=timeout,
                    cwd=".",
                    actor=env.actor(item_label),
                    description=item_label,
                )
                for api_command, item_label in zip(api_commands, item_labels)
            ],
            max_concurrency=max_concurrency,
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        error = str(exc)[:500]
        print_live_metric(
            "sandbox.api.shell_batch",
            label=label,
            total=len(commands),
            elapsed_ms=round(elapsed_ms, 2),
            error=error,
        )
        return [
            LiveExecResult(
                command=command,
                stdout="",
                exit_code=-99,
                elapsed_ms=elapsed_ms,
                success=False,
                error=error,
            )
            for command in commands
        ]

    elapsed_ms = (time.monotonic() - start) * 1000
    results = [
        LiveExecResult(
            command=command,
            stdout=response.stdout,
            stderr=response.stderr,
            exit_code=response.exit_code,
            elapsed_ms=elapsed_ms,
            success=response.success and response.exit_code == 0,
            status=response.status,
            changed_paths=tuple(response.changed_paths),
            conflict_reason=response.conflict_reason,
            timings=dict(response.timings),
        )
        for command, response in zip(commands, responses)
    ]
    print_live_metric(
        "sandbox.api.shell_batch",
        label=label,
        total=len(commands),
        successes=sum(1 for result in results if result.success),
        failures=sum(1 for result in results if not result.success),
        elapsed_ms=round(elapsed_ms, 2),
        max_concurrency=max_concurrency,
    )
    return results


def assert_success(result: LiveExecResult) -> None:
    assert result.success and result.exit_code == 0, (
        f"command failed with exit={result.exit_code} status={result.status} "
        f"conflict={result.conflict_reason} error={result.error}\n"
        f"stdout tail:\n{result.stdout[-2000:]}"
        f"\nstderr tail:\n{result.stderr[-2000:]}"
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
    if not result.success or result.exit_code != 0:
        missing = ", ".join(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )
        pytest.skip(f"live sandbox image is missing required command(s): {missing}")


async def make_workdir(env: LiveSnapshotSandbox, prefix: str) -> str:
    safe_prefix = "".join(ch for ch in prefix if ch.isalnum() or ch in "-_")
    workdir = f"work/{safe_prefix}_{uuid.uuid4().hex[:8]}"
    result = await run_live_command(
        env,
        (
            f"mkdir -p {shlex.quote(workdir)} && "
            f": > {shlex.quote(workdir)}/.eos_keep && "
            f"printf '%s\\n' {shlex.quote(workdir)}"
        ),
        timeout=30,
        label=f"{prefix}.mkdir",
    )
    assert_success(result)
    return workdir


async def write_live_file(
    env: LiveSnapshotSandbox,
    path: str,
    content: str,
    *,
    label: str,
) -> None:
    result = await env.write_file(
        path=path,
        content=content,
        actor=env.actor(label),
        description=label,
    )
    assert result.success, result.conflict_reason


async def edit_live_file(
    env: LiveSnapshotSandbox,
    path: str,
    *,
    old_text: str,
    new_text: str,
    label: str,
) -> None:
    result = await env.edit_file(
        path=path,
        edits=(SearchReplaceEdit(old_text=old_text, new_text=new_text),),
        actor=env.actor(label),
        description=label,
    )
    assert result.success, result.conflict_reason


async def read_live_file(env: LiveSnapshotSandbox, path: str, *, label: str) -> str:
    result = await env.read_file(path=path, actor=env.actor(label))
    assert result.success and result.exists
    return result.content


def mark_ignored(env: LiveSnapshotSandbox, paths: Sequence[str]) -> None:
    env.mark_ignored(paths)


async def pinned_layers(env: LiveSnapshotSandbox) -> tuple[str, ...]:
    return await env.pinned_layers()


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
    layer_root = root / "l"
    for index in range(depth):
        layer = layer_root / f"{index:x}"
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
    root = pathlib.Path(f"/tmp/o{os.getpid() % 4096:x}{depth:x}")
    mount_samples = []
    read_passes = []
    tmpfs_mounted = False
    lowerdir_len = 0
    try:
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        mount_tmpfs(root)
        tmpfs_mounted = True
        layers = build_layers(root, depth)
        lowerdir = ":".join(str(layer) for layer in reversed(layers))
        lowerdir_len = len(lowerdir)
        for iteration in range(ITERATIONS):
            iteration_id = f"{iteration:x}"
            upper = root / f"u{iteration_id}"
            work = root / f"w{iteration_id}"
            merged = root / f"m{iteration_id}"
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
            "lowerdir_len": lowerdir_len,
            "error": None,
        }
    except BaseException as exc:
        return {
            "depth": depth,
            "iterations": ITERATIONS,
            "mount_samples_ms": mount_samples,
            "read_passes": read_passes,
            "lowerdir_len": lowerdir_len,
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
