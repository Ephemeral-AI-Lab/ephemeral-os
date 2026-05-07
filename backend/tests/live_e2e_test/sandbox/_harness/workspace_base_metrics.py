"""Shared host-side metrics helpers for phase-01 workspace-base live tests."""

from __future__ import annotations

import json
import os
import shlex
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sandbox.host.daemon_client as daemon_client_mod

from .native_probe import BUNDLE_REMOTE_DIR
from .sandbox_fixture import SandboxHandle, WORKSPACE_ROOT


SCHEMA = "sandbox.live_e2e.phase01_workspace_base.v1"
LAYER_STACK_ROOT = f"{BUNDLE_REMOTE_DIR}/layer-stack"
PHASE01_ROOT_PREFIX = f"{BUNDLE_REMOTE_DIR}/layer-stack-phase01"


def phase01_stack_root(case: str, suffix: str = "") -> str:
    safe_case = _safe_name(case)
    safe_suffix = _safe_name(suffix) if suffix else ""
    tail = f"-{safe_suffix}" if safe_suffix else ""
    return f"{PHASE01_ROOT_PREFIX}-{safe_case}{tail}"


async def reset_layer_stack_root(handle: SandboxHandle, layer_stack_root: str) -> None:
    quoted = shlex.quote(layer_stack_root)
    result = await handle.raw_exec(
        handle.sandbox_id,
        f"rm -rf {quoted} && mkdir -p {quoted}",
        timeout=60,
    )
    assert result.exit_code == 0, result.stderr or result.stdout


async def runtime_call(
    handle: SandboxHandle,
    op: str,
    args: Mapping[str, object] | None = None,
    *,
    layer_stack_root: str = LAYER_STACK_ROOT,
    timeout: int = 180,
) -> dict[str, Any]:
    return await daemon_client_mod.call_daemon_api(
        handle.sandbox_id,
        op,
        dict(args or {}),
        timeout=timeout,
        layer_stack_root=layer_stack_root,
    )


async def workspace_inventory(
    handle: SandboxHandle,
    *,
    root: str = WORKSPACE_ROOT,
    sample_limit: int = 16,
    full: bool = False,
) -> dict[str, Any]:
    script = r"""
import hashlib, json, os, subprocess, sys

root = sys.argv[1]
sample_limit = int(sys.argv[2])
full = sys.argv[3] == "1"

def rel(path):
    return os.path.relpath(path, root).replace(os.sep, "/")

def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

files = 0
dirs = 0
symlinks = 0
total_bytes = 0
sample_hashes = {}
symlink_targets = {}
empty_dirs = []
special = []

for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
    dirnames.sort()
    filenames.sort()
    kept_dirs = []
    child_count = len(filenames)
    for dirname in dirnames:
        path = os.path.join(current_root, dirname)
        child_count += 1
        if os.path.islink(path):
            symlinks += 1
            symlink_targets[rel(path)] = os.readlink(path)
            continue
        dirs += 1
        kept_dirs.append(dirname)
    dirnames[:] = kept_dirs
    if current_root != root and child_count == 0:
        empty_dirs.append(rel(current_root))

    for filename in filenames:
        path = os.path.join(current_root, filename)
        name = rel(path)
        try:
            stat = os.lstat(path)
        except FileNotFoundError:
            special.append(name)
            continue
        if os.path.islink(path):
            symlinks += 1
            symlink_targets[name] = os.readlink(path)
            continue
        if not os.path.isfile(path):
            special.append(name)
            continue
        files += 1
        total_bytes += stat.st_size
        if full or len(sample_hashes) < sample_limit:
            sample_hashes[name] = sha(path)

try:
    repo_commit = subprocess.check_output(
        ["git", "-C", root, "rev-parse", "HEAD"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
except Exception:
    repo_commit = ""

print(json.dumps({
    "files": files,
    "dirs": dirs,
    "symlinks": symlinks,
    "bytes": total_bytes,
    "sample_hashes": sample_hashes,
    "symlink_targets": symlink_targets,
    "empty_dirs": empty_dirs[:sample_limit],
    "special": special,
    "repo_commit": repo_commit,
}, separators=(",", ":"), sort_keys=True))
"""
    cmd = "python3 -c {src} {root} {limit} {full}".format(
        src=shlex.quote(script),
        root=shlex.quote(root),
        limit=sample_limit,
        full="1" if full else "0",
    )
    result = await handle.raw_exec(handle.sandbox_id, cmd, timeout=90)
    assert result.exit_code == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert isinstance(payload, dict), payload
    return payload


async def selected_text_paths(
    handle: SandboxHandle,
    *,
    max_files: int = 16,
    root: str = WORKSPACE_ROOT,
) -> list[str]:
    suffix_expr = r"\( -name '*.cfg' -o -name '*.css' -o -name '*.ini' -o -name '*.js' -o -name '*.json' -o -name '*.md' -o -name '*.py' -o -name '*.rst' -o -name '*.toml' -o -name '*.txt' -o -name '*.yaml' -o -name '*.yml' \)"
    cmd = (
        f"find {shlex.quote(root)} -xdev -type f {suffix_expr} "
        r"-printf '%P\n' | sort"
    )
    result = await handle.raw_exec(handle.sandbox_id, cmd, timeout=30)
    assert result.exit_code == 0, result.stderr or result.stdout
    return result.stdout.splitlines()[:max(1, max_files)]


async def path_sha256(handle: SandboxHandle, absolute_path: str) -> str:
    result = await handle.raw_exec(
        handle.sandbox_id,
        "python3 -c {src} {path}".format(
            src=shlex.quote(
                "import hashlib,sys;"
                "d=hashlib.sha256();"
                "f=open(sys.argv[1],'rb');"
                "\nfor c in iter(lambda:f.read(1024*1024), b''): d.update(c);"
                "\nprint(d.hexdigest())"
            ),
            path=shlex.quote(absolute_path),
        ),
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    return result.stdout.strip()


def base_summary(
    *,
    case: str,
    binding: Mapping[str, object],
    workspace_inventory: Mapping[str, object],
    timings: Mapping[str, float] | None = None,
    pass_bars: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    timing_values = {str(key): float(value) for key, value in (timings or {}).items()}
    return {
        "schema": SCHEMA,
        "kind": "summary",
        "case": case,
        "workspace_root": str(binding["workspace_root"]),
        "layer_stack_root": str(binding["layer_stack_root"]),
        "base_manifest_version": int(binding["base_manifest_version"]),
        "base_root_hash": str(binding["base_root_hash"]),
        "active_manifest_version": int(binding["active_manifest_version"]),
        "active_root_hash": str(binding["active_root_hash"]),
        "repo_commit": str(workspace_inventory.get("repo_commit") or ""),
        "workspace_inventory": {
            "files": int(workspace_inventory.get("files") or 0),
            "dirs": int(workspace_inventory.get("dirs") or 0),
            "symlinks": int(workspace_inventory.get("symlinks") or 0),
            "bytes": int(workspace_inventory.get("bytes") or 0),
            "sample_hashes": dict(workspace_inventory.get("sample_hashes") or {}),
        },
        "timings": timing_values,
        "timings_ms": {
            key: round(value * 1000.0, 3)
            for key, value in timing_values.items()
            if key.endswith("_s")
        },
        "pass_bars": dict(pass_bars or {}),
    }


def call_row(
    *,
    case: str,
    label: str,
    success: bool,
    wall_ms: float,
    runtime_ms: float = 0.0,
    timings: Mapping[str, float] | None = None,
    resource: Mapping[str, object] | None = None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": "call",
        "case": case,
        "label": label,
        "success": success,
        "wall_ms": round(wall_ms, 3),
        "runtime_ms": round(runtime_ms, 3),
        "timings": {
            str(key): round(float(value), 6)
            for key, value in sorted((timings or {}).items())
        },
        "resource": dict(resource or {}),
    }
    row.update(dict(extra or {}))
    return row


def write_jsonl_artifact(
    *,
    case: str,
    summary: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = (
        Path.cwd()
        / ".omc"
        / "results"
        / f"live-e2e-phase01-workspace-base-{case}-{stamp}.jsonl"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with artifact.open("w", encoding="utf-8") as file:
        for row in (summary, *rows):
            file.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            file.write("\n")
    return artifact


def percentile(values: Sequence[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (percentile_value / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def monotonic_ms() -> float:
    return time.perf_counter() * 1000.0


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value)
    return safe.strip("-") or "case"


__all__ = [
    "LAYER_STACK_ROOT",
    "PHASE01_ROOT_PREFIX",
    "SCHEMA",
    "base_summary",
    "call_row",
    "env_flag",
    "env_int",
    "monotonic_ms",
    "path_sha256",
    "percentile",
    "phase01_stack_root",
    "reset_layer_stack_root",
    "runtime_call",
    "selected_text_paths",
    "workspace_inventory",
    "write_jsonl_artifact",
]
