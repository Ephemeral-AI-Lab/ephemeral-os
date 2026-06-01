#!/usr/bin/env python3
"""Live Docker AV-7 forward/back on-disk parity gate."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import shlex
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "backend" / "src"
SCRIPT_DIR = Path(__file__).resolve().parent
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bench_rust_daemon_phase2 import (  # noqa: E402
    PID_PATH,
    RUNTIME_ROOT,
    SOCKET_PATH,
    WORKSPACE_ROOT,
    reset_runtime,
    require_success,
    temporary_env,
    upload_artifact,
)
from bench_sandbox_e2e import (  # noqa: E402
    DEFAULT_DOCKER_IMAGE,
    DockerBench,
    collect_environment,
    elapsed_ms,
)

REPORT_PATH = ROOT / "bench" / "phase3t-av7-forward-back-parity-20260601.json"
RUST_FIRST_ROOT = "/eos/av7/rust-first"
PYTHON_FIRST_ROOT = "/eos/av7/python-first"
EXPECTED_CONTENT = {
    "av7/write.txt": "payload-write\n",
    "av7/edit.txt": "edit-after\n",
    "av7/command.txt": "command-payload\n",
    "av7/dedup.txt": "dedup-payload\n",
}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run(args))
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(
        f"wrote {path} (gate={report['gate_pass']} av7={report['av7']['gate_pass']} "
        f"run_id={report['run_id']})"
    )
    return 0 if report["gate_pass"] else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--container-id", default=None)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=ROOT / "sandbox" / "dist" / "eosd-linux-amd64",
    )
    parser.add_argument("--report", default=str(REPORT_PATH))
    parser.add_argument("--keep-container", action="store_true")
    parser.add_argument("--name-prefix", default="eos-phase3t-av7")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.artifact.exists():
        raise SystemExit(f"missing eosd artifact: {args.artifact}")
    bench = await DockerBench.create(
        image=args.docker_image,
        container_id=args.container_id,
        name_prefix=args.name_prefix,
    )
    try:
        report: dict[str, Any] = {
            "mode": "docker-phase3t-av7-forward-back-parity",
            "run_id": os.environ.get("EOS_TIER_RUN_ID") or f"local-{uuid.uuid4().hex[:12]}",
            "sandbox_id": bench.sandbox_id,
            "created_container": bench.created,
            "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
            "environment": await collect_environment(bench),
            "roots": {"rust_first": RUST_FIRST_ROOT, "python_first": PYTHON_FIRST_ROOT},
        }
        await reset_runtime(bench)
        require_success(
            await bench.exec("rm -rf /eos/av7", timeout=30),
            "reset AV-7 roots",
        )
        report["python_bundle"] = await ensure_python_bundle_uploaded(bench)
        report["artifact"] = await upload_artifact(bench, args.artifact)

        report["rust_first"] = await direction(
            bench,
            publisher="rust",
            reader="python",
            root=RUST_FIRST_ROOT,
        )
        report["python_first"] = await direction(
            bench,
            publisher="python",
            reader="rust",
            root=PYTHON_FIRST_ROOT,
        )
        report["av7"] = evaluate_av7(report)
        report["gate_pass"] = bool(
            report["artifact"]["gate_pass"]
            and report["rust_first"]["gate_pass"]
            and report["python_first"]["gate_pass"]
            and report["av7"]["gate_pass"]
        )
        return report
    finally:
        await bench.close(keep=args.keep_container)


async def ensure_python_bundle_uploaded(bench: DockerBench) -> dict[str, Any]:
    with temporary_env("EOS_SANDBOX_RUNTIME", "python"):
        from sandbox.host.runtime_bundle import ensure_runtime_uploaded

        started = time.perf_counter()
        digest = await ensure_runtime_uploaded(bench.sandbox_id)
        return {"sha256": digest, "upload_or_check_ms": elapsed_ms(started)}


async def direction(
    bench: DockerBench,
    *,
    publisher: str,
    reader: str,
    root: str,
) -> dict[str, Any]:
    await start_runtime(bench, publisher)
    seed = await call_api(
        bench,
        publisher,
        "api.build_workspace_base",
        {"workspace_root": WORKSPACE_ROOT, "reset": True},
        root=root,
        timeout=180,
    )
    ready = await call_api(bench, publisher, "api.runtime.ready", {}, root=root, timeout=30)
    publish = await publish_sequence(bench, publisher, root=root)
    before_duplicate = await inspect_stack(bench, root)

    await start_runtime(bench, reader)
    cross_reads = await read_expected(bench, reader, root=root)
    duplicate = await duplicate_head_write(bench, reader, root=root)
    after_duplicate = await inspect_stack(bench, root)
    post_duplicate_reads = await read_expected(bench, reader, root=root)

    checks = {
        "seed_success": seed.get("success") is True,
        "ready": ready.get("ready") is True,
        "publish_sequence_ok": publish["all_ok"],
        "reader_reads_ok": cross_reads["all_ok"],
        "post_duplicate_reads_ok": post_duplicate_reads["all_ok"],
        "duplicate_write_ok": duplicate["ok"],
        "head_dedup": before_duplicate["manifest"] == after_duplicate["manifest"]
        and before_duplicate["non_base_digests"] == after_duplicate["non_base_digests"],
    }
    return {
        "publisher": publisher,
        "reader": reader,
        "root": root,
        "seed": trim(seed),
        "ready": trim(ready),
        "publish": publish,
        "reader_reads": cross_reads,
        "duplicate": duplicate,
        "post_duplicate_reads": post_duplicate_reads,
        "before_duplicate": before_duplicate,
        "after_duplicate": after_duplicate,
        "checks": checks,
        "gate_pass": all(checks.values()),
    }


async def start_runtime(bench: DockerBench, runtime: str) -> None:
    await stop_daemon(bench)
    with temporary_env("EOS_SANDBOX_RUNTIME", runtime):
        from sandbox.host import daemon_client

        daemon_client.invalidate_daemon_tcp_endpoint(bench.sandbox_id)
        await daemon_client.ensure_daemon_current(bench.sandbox_id)


async def stop_daemon(bench: DockerBench) -> None:
    command = f"""
set -eu
if [ -f {shlex.quote(PID_PATH)} ]; then
  pid="$(cat {shlex.quote(PID_PATH)} 2>/dev/null || true)"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    i=0
    while [ "$i" -lt 30 ] && kill -0 "$pid" 2>/dev/null; do
      sleep 0.1
      i=$((i + 1))
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
fi
rm -f {shlex.quote(PID_PATH)} {shlex.quote(SOCKET_PATH)} \
  {shlex.quote(RUNTIME_ROOT)}/runtime.env {shlex.quote(RUNTIME_ROOT)}/runtime.log
"""
    require_success(await bench.exec(command, timeout=10), "stop daemon")


async def call_api(
    bench: DockerBench,
    runtime: str,
    op: str,
    args: dict[str, Any],
    *,
    root: str,
    timeout: int,
) -> dict[str, Any]:
    with temporary_env("EOS_SANDBOX_RUNTIME", runtime):
        from sandbox.host import daemon_client

        return await daemon_client.call_daemon_api(
            bench.sandbox_id,
            op,
            args,
            timeout=timeout,
            layer_stack_root=root,
        )


async def publish_sequence(bench: DockerBench, runtime: str, *, root: str) -> dict[str, Any]:
    operations = [
        (
            "write",
            "api.v1.write_file",
            {"path": f"{WORKSPACE_ROOT}/av7/write.txt", "content": EXPECTED_CONTENT["av7/write.txt"]},
        ),
        (
            "write_edit_base",
            "api.v1.write_file",
            {"path": f"{WORKSPACE_ROOT}/av7/edit.txt", "content": "edit-before\n"},
        ),
        (
            "edit",
            "api.v1.edit_file",
            {
                "path": f"{WORKSPACE_ROOT}/av7/edit.txt",
                "edits": [
                    {
                        "old_text": "edit-before\n",
                        "new_text": EXPECTED_CONTENT["av7/edit.txt"],
                        "replace_all": False,
                    }
                ],
            },
        ),
        (
            "exec_command",
            "api.v1.exec_command",
            {
                "cmd": "printf 'command-payload\\n' > av7/command.txt",
                "tty": False,
                "yield_time_ms": 1000,
                "timeout": 30,
            },
        ),
        (
            "dedup_head_seed",
            "api.v1.write_file",
            {"path": f"{WORKSPACE_ROOT}/av7/dedup.txt", "content": EXPECTED_CONTENT["av7/dedup.txt"]},
        ),
    ]
    samples = []
    for name, op, payload in operations:
        response = await call_api(bench, runtime, op, payload, root=root, timeout=60)
        samples.append({"name": name, "op": op, "ok": operation_ok(response), "response": trim(response)})
    return {"samples": samples, "all_ok": all(item["ok"] for item in samples)}


async def duplicate_head_write(bench: DockerBench, runtime: str, *, root: str) -> dict[str, Any]:
    response = await call_api(
        bench,
        runtime,
        "api.v1.write_file",
        {"path": f"{WORKSPACE_ROOT}/av7/dedup.txt", "content": EXPECTED_CONTENT["av7/dedup.txt"]},
        root=root,
        timeout=60,
    )
    return {"ok": operation_ok(response), "response": trim(response)}


async def read_expected(bench: DockerBench, runtime: str, *, root: str) -> dict[str, Any]:
    reads = []
    for path, expected in EXPECTED_CONTENT.items():
        response = await call_api(
            bench,
            runtime,
            "api.v1.read_file",
            {"path": f"{WORKSPACE_ROOT}/{path}"},
            root=root,
            timeout=30,
        )
        actual = response.get("content")
        reads.append(
            {
                "path": path,
                "ok": actual == expected,
                "content_sha256": sha256_text(str(actual)) if isinstance(actual, str) else None,
                "response": trim(response),
            }
        )
    workspace_hash = sha256_text(
        json.dumps({item["path"]: EXPECTED_CONTENT[item["path"]] for item in reads}, sort_keys=True)
    )
    return {"reads": reads, "all_ok": all(item["ok"] for item in reads), "workspace_hash": workspace_hash}


async def inspect_stack(bench: DockerBench, root: str) -> dict[str, Any]:
    script = r"""
import json
import os
import sys

root = sys.argv[1]
manifest_path = os.path.join(root, "manifest.json")
with open(manifest_path, encoding="utf-8") as fh:
    manifest = json.load(fh)
meta = os.path.join(root, ".layer-metadata")
digests = []
if os.path.isdir(meta):
    for name in sorted(os.listdir(meta)):
        if not name.endswith(".digest"):
            continue
        layer_id = name[:-7]
        with open(os.path.join(meta, name), encoding="utf-8") as fh:
            digest = fh.read().strip()
        digests.append({"layer_id": layer_id, "digest": digest, "base": layer_id.startswith("B")})
print(json.dumps({"manifest": manifest, "digests": digests}, sort_keys=True))
"""
    result = await bench.exec(
        f"python3 -c {shlex.quote(script)} {shlex.quote(root)}",
        timeout=30,
    )
    require_success(result, f"inspect stack {root}")
    payload = json.loads(str(getattr(result, "stdout", "")).strip())
    non_base = [item["digest"] for item in payload["digests"] if not item["base"]]
    return {
        "manifest": payload["manifest"],
        "digests": payload["digests"],
        "non_base_digests": non_base,
        "non_base_digest_stream_sha256": sha256_text(json.dumps(non_base, separators=(",", ":"))),
    }


def evaluate_av7(report: dict[str, Any]) -> dict[str, Any]:
    rust_first = report["rust_first"]
    python_first = report["python_first"]
    digest_stream_equal = (
        rust_first["after_duplicate"]["non_base_digests"]
        == python_first["after_duplicate"]["non_base_digests"]
    )
    workspace_hash_equal = (
        rust_first["post_duplicate_reads"]["workspace_hash"]
        == python_first["post_duplicate_reads"]["workspace_hash"]
    )
    checks = {
        "rust_published_python_read": rust_first["gate_pass"],
        "python_published_rust_read": python_first["gate_pass"],
        "byte_identical_layer_digest_stream": digest_stream_equal,
        "equal_final_workspace_hash": workspace_hash_equal,
        "head_dedup_both_directions": rust_first["checks"]["head_dedup"]
        and python_first["checks"]["head_dedup"],
    }
    return {
        "checks": checks,
        "rust_first_digest_stream": rust_first["after_duplicate"]["non_base_digests"],
        "python_first_digest_stream": python_first["after_duplicate"]["non_base_digests"],
        "gate_pass": all(checks.values()),
    }


def operation_ok(response: dict[str, Any]) -> bool:
    if response.get("success") is True:
        return True
    return response.get("status") in {"ok", "committed"} and not response.get("error")


def trim(response: dict[str, Any]) -> dict[str, Any]:
    return {
        key: response.get(key)
        for key in (
            "success",
            "status",
            "exit_code",
            "exists",
            "changed_paths",
            "content",
            "conflict",
            "conflict_reason",
            "error",
            "workspace",
        )
        if key in response
    }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
