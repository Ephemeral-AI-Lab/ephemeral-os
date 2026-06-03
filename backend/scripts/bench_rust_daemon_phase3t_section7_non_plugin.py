#!/usr/bin/env python3
"""Live Docker Phase 3T §7 non-plugin differential/property gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shlex
import sys
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
    WORKSPACE_ROOT,
    reset_runtime,
    require_success,
    upload_artifact,
)
from bench_rust_daemon_phase3t_av7_parity import (  # noqa: E402
    call_api,
    ensure_python_bundle_uploaded,
    inspect_stack,
    operation_ok,
    sha256_text,
    start_runtime,
    trim,
)
from bench_sandbox_e2e import (  # noqa: E402
    DEFAULT_DOCKER_IMAGE,
    DockerBench,
    collect_environment,
)

REPORT_PATH = ROOT / "bench" / "phase3t-section7-non-plugin-differential-20260601.json"
PYTHON_ROOT = "/eos/section7/python"
RUST_ROOT = "/eos/section7/rust"
SQUASH_WRITES = 105


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run(args))
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(
        f"wrote {path} (gate={report['gate_pass']} section7={report['section7']['gate_pass']} "
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
    parser.add_argument("--squash-writes", type=int, default=SQUASH_WRITES)
    parser.add_argument("--keep-container", action="store_true")
    parser.add_argument("--name-prefix", default="eos-phase3t-section7")
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
            "mode": "docker-phase3t-section7-non-plugin-differential",
            "run_id": os.environ.get("EOS_TIER_RUN_ID") or f"local-{uuid.uuid4().hex[:12]}",
            "sandbox_id": bench.sandbox_id,
            "created_container": bench.created,
            "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
            "environment": await collect_environment(bench),
            "roots": {"python": PYTHON_ROOT, "rust": RUST_ROOT},
            "squash_writes": max(0, args.squash_writes),
        }
        await reset_runtime(bench)
        require_success(
            await bench.exec("rm -rf /eos/section7", timeout=30),
            "reset section7 roots",
        )
        report["python_bundle"] = await ensure_python_bundle_uploaded(bench)
        report["artifact"] = await upload_artifact(bench, args.artifact)
        report["python"] = await run_scenario(
            bench,
            runtime="python",
            root=PYTHON_ROOT,
            squash_writes=max(0, args.squash_writes),
        )
        report["rust"] = await run_scenario(
            bench,
            runtime="rust",
            root=RUST_ROOT,
            squash_writes=max(0, args.squash_writes),
        )
        report["section7"] = evaluate_section7(report)
        report["gate_pass"] = bool(
            report["artifact"]["gate_pass"]
            and report["python"]["gate_pass"]
            and report["rust"]["gate_pass"]
            and report["section7"]["gate_pass"]
        )
        return report
    finally:
        await bench.close(keep=args.keep_container)


async def run_scenario(
    bench: DockerBench,
    *,
    runtime: str,
    root: str,
    squash_writes: int,
) -> dict[str, Any]:
    await start_runtime(bench, runtime)
    seed = await call_api(
        bench,
        runtime,
        "api.build_workspace_base",
        {"workspace_root": WORKSPACE_ROOT, "reset": True},
        root=root,
        timeout=180,
    )
    ready = await call_api(bench, runtime, "api.runtime.ready", {}, root=root, timeout=30)
    samples: list[dict[str, Any]] = []

    async def call(
        name: str, op: str, payload: dict[str, Any], *, timeout: int = 60
    ) -> dict[str, Any]:
        response = await call_api(bench, runtime, op, payload, root=root, timeout=timeout)
        samples.append(
            {"name": name, "op": op, "ok": operation_ok(response), "response": trim(response)}
        )
        return response

    await call("seed_read", "api.v1.write_file", write_payload("s7/read.txt", "read-seed\n"))
    await call(
        "seed_grep", "api.v1.write_file", write_payload("s7/grep.txt", "needle one\nneedle two\n")
    )
    await call("seed_edit", "api.v1.write_file", write_payload("s7/edit.txt", "edit-before\n"))
    await call("seed_delete", "api.v1.write_file", write_payload("s7/delete.txt", "delete-me\n"))
    await call("seed_conflict", "api.v1.write_file", write_payload("s7/conflict.txt", "base\n"))

    await call("read", "api.v1.read_file", {"path": f"{WORKSPACE_ROOT}/s7/read.txt"}, timeout=30)
    await call("glob", "api.v1.glob", {"pattern": "s7/*.txt", "path": "."}, timeout=30)
    await call(
        "grep",
        "api.v1.grep",
        {
            "pattern": "needle",
            "path": ".",
            "output_mode": "content",
            "offset": 0,
            "case_insensitive": False,
            "line_numbers": True,
            "multiline": False,
        },
        timeout=30,
    )
    await call(
        "edit",
        "api.v1.edit_file",
        {
            "path": f"{WORKSPACE_ROOT}/s7/edit.txt",
            "edits": [
                {"old_text": "edit-before\n", "new_text": "edit-after\n", "replace_all": False}
            ],
        },
    )
    await call(
        "atomic_multi_path_exec",
        "api.v1.exec_command",
        exec_payload("printf 'multi-a\\n' > s7/multi-a.txt; printf 'multi-b\\n' > s7/multi-b.txt"),
    )
    await call("delete_whiteout", "api.v1.exec_command", exec_payload("rm s7/delete.txt"))
    await call("symlink", "api.v1.exec_command", exec_payload("ln -s write.txt s7/link-to-write"))

    before_noop = await inspect_stack(bench, root)
    await call("noop_capture", "api.v1.exec_command", exec_payload("true"))
    after_noop = await inspect_stack(bench, root)

    conflict_results = await asyncio.gather(
        *(
            call_api(
                bench,
                runtime,
                "api.v1.edit_file",
                {
                    "path": f"{WORKSPACE_ROOT}/s7/conflict.txt",
                    "edits": [{"old_text": "base\n", "new_text": "winner\n", "replace_all": False}],
                },
                root=root,
                timeout=60,
            )
            for _ in range(5)
        )
    )
    conflict = summarize_conflicts(conflict_results)

    squash_samples = []
    for index in range(squash_writes):
        response = await call_api(
            bench,
            runtime,
            "api.v1.write_file",
            write_payload(f"s7/squash/file-{index:03d}.txt", f"squash-{index}\n"),
            root=root,
            timeout=60,
        )
        squash_samples.append(operation_ok(response))

    command_session = await run_command_session_or_equivalent(bench, runtime, root=root)
    metrics = await call_api(bench, runtime, "api.layer_metrics", {}, root=root, timeout=30)
    final_view = await collect_final_view(bench, runtime, root=root)
    stack = await inspect_stack(bench, root)

    checks = {
        "seed_success": seed.get("success") is True,
        "ready": ready.get("ready") is True,
        "common_operations_ok": all(item["ok"] or expected_rejection_ok(item) for item in samples),
        "conflict_has_single_winner": conflict["success_count"] == 1
        and conflict["loser_count"] == 4,
        "noop_capture_no_new_layer": before_noop["manifest"] == after_noop["manifest"]
        and before_noop["non_base_digests"] == after_noop["non_base_digests"],
        "squash_writes_ok": bool(squash_samples) and all(squash_samples),
        "manifest_depth_bounded": int(metrics.get("manifest_depth") or 0) <= 100,
        "no_missing_or_orphan_layers": int(metrics.get("missing_layer_count") or 0) == 0
        and int(metrics.get("orphan_layer_count") or 0) == 0,
        "command_session_or_equivalent_ok": command_session["ok"],
        "final_view_ok": final_view["ok"],
    }
    return {
        "runtime": runtime,
        "root": root,
        "seed": trim(seed),
        "ready": trim(ready),
        "samples": samples,
        "conflict": conflict,
        "noop": {
            "before_digest_stream": before_noop["non_base_digest_stream_sha256"],
            "after_digest_stream": after_noop["non_base_digest_stream_sha256"],
        },
        "squash": {
            "write_count": squash_writes,
            "success_count": sum(1 for ok in squash_samples if ok),
        },
        "command_session": command_session,
        "metrics": trim_metrics(metrics),
        "final_view": final_view,
        "stack": {
            "manifest_version": stack["manifest"].get("version"),
            "manifest_depth": len(stack["manifest"].get("layers", [])),
            "non_base_digest_stream_sha256": stack["non_base_digest_stream_sha256"],
        },
        "checks": checks,
        "gate_pass": all(checks.values()),
    }


async def run_command_session_or_equivalent(
    bench: DockerBench, runtime: str, *, root: str
) -> dict[str, Any]:
    if runtime == "python":
        response = await call_api(
            bench,
            runtime,
            "api.v1.exec_command",
            exec_payload("printf 'command-final\\n' > s7/command-session.txt"),
            root=root,
            timeout=60,
        )
        return {
            "mode": "finite_equivalent",
            "ok": operation_ok(response),
            "response": trim(response),
        }

    start = await call_api(
        bench,
        runtime,
        "api.v1.exec_command",
        {
            "cmd": "sleep 0.1; printf 'command-final\\n' > s7/command-session.txt",
            "yield_time_ms": 10,
            "timeout": 5,
        },
        root=root,
        timeout=30,
    )
    session_id = str(start.get("command_session_id") or "")
    await asyncio.sleep(0.25)
    progress = await call_api(
        bench,
        runtime,
        "api.v1.write_stdin",
        {
            "command_session_id": session_id,
            "chars": "",
            "yield_time_ms": 50,
            "max_output_tokens": 2000,
        },
        root=root,
        timeout=30,
    )
    return {
        "mode": "command_session_finalization",
        "ok": bool(session_id) and progress.get("status") == "ok",
        "start": trim(start),
        "progress": trim(progress),
    }


async def collect_final_view(bench: DockerBench, runtime: str, *, root: str) -> dict[str, Any]:
    script = r"""
import hashlib
import json
import os

paths = [
    "s7/read.txt",
    "s7/grep.txt",
    "s7/edit.txt",
    "s7/delete.txt",
    "s7/conflict.txt",
    "s7/multi-a.txt",
    "s7/multi-b.txt",
    "s7/link-to-write",
    "s7/command-session.txt",
]
view = {}
for path in paths:
    if os.path.islink(path):
        view[path] = {"kind": "symlink", "target": os.readlink(path)}
    elif os.path.exists(path):
        with open(path, "rb") as fh:
            data = fh.read()
        view[path] = {
            "kind": "file",
            "sha256": hashlib.sha256(data).hexdigest(),
            "text": data.decode("utf-8", errors="replace"),
        }
    else:
        view[path] = {"kind": "missing"}
print(json.dumps(view, sort_keys=True))
"""
    response = await call_api(
        bench,
        runtime,
        "api.v1.exec_command",
        exec_payload(f"python3 -c {shlex.quote(script)}"),
        root=root,
        timeout=60,
    )
    stdout = str(response.get("output", {}).get("stdout", ""))
    try:
        view = json.loads(stdout)
    except json.JSONDecodeError:
        view = {}
    expected = expected_final_view()
    return {
        "ok": view == expected,
        "hash": sha256_text(json.dumps(view, sort_keys=True, separators=(",", ":"))),
        "view": view,
        "response": trim(response),
    }


def expected_final_view() -> dict[str, Any]:
    return {
        "s7/read.txt": file_view("read-seed\n"),
        "s7/grep.txt": file_view("needle one\nneedle two\n"),
        "s7/edit.txt": file_view("edit-after\n"),
        "s7/delete.txt": {"kind": "missing"},
        "s7/conflict.txt": file_view("winner\n"),
        "s7/multi-a.txt": file_view("multi-a\n"),
        "s7/multi-b.txt": file_view("multi-b\n"),
        "s7/link-to-write": {"kind": "missing"},
        "s7/command-session.txt": file_view("command-final\n"),
    }


def evaluate_section7(report: dict[str, Any]) -> dict[str, Any]:
    python = report["python"]
    rust = report["rust"]
    checks = {
        "python_gate": python["gate_pass"],
        "rust_gate": rust["gate_pass"],
        "canonical_result_classes_equal": canonical_classes(python) == canonical_classes(rust),
        "final_workspace_hash_equal": python["final_view"]["hash"] == rust["final_view"]["hash"],
        "conflict_counts_equal": python["conflict"] == rust["conflict"],
        "manifest_depth_bounded_both": python["checks"]["manifest_depth_bounded"]
        and rust["checks"]["manifest_depth_bounded"],
        "no_plugin_lanes": True,
    }
    return {
        "checks": checks,
        "python_classes": canonical_classes(python),
        "rust_classes": canonical_classes(rust),
        "gate_pass": all(checks.values()),
    }


def canonical_classes(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "common": [
            {
                "name": item["name"],
                "op": item["op"],
                "outcome": canonical_outcome(item),
                "exit_code": item["response"].get("exit_code"),
                "exists": item["response"].get("exists"),
            }
            for item in result["samples"]
        ],
        "conflict": result["conflict"],
        "final_hash": result["final_view"]["hash"],
    }


def summarize_conflicts(responses: list[dict[str, Any]]) -> dict[str, int]:
    success_count = sum(1 for response in responses if response.get("success") is True)
    conflict_count = sum(1 for response in responses if response.get("conflict") is True)
    error_count = len(responses) - success_count - conflict_count
    return {
        "attempt_count": len(responses),
        "success_count": success_count,
        "conflict_count": conflict_count,
        "error_count": error_count,
        "loser_count": conflict_count + error_count,
    }


def expected_rejection_ok(item: dict[str, Any]) -> bool:
    return item.get("name") == "symlink" and canonical_outcome(item) == "rejected"


def canonical_outcome(item: dict[str, Any]) -> str:
    response = item.get("response", {})
    if item.get("ok"):
        return "ok"
    if response.get("status") == "rejected" or response.get("success") is False:
        return "rejected"
    if response.get("conflict") is True:
        return "conflict"
    return "error"


def write_payload(path: str, content: str) -> dict[str, Any]:
    return {"path": f"{WORKSPACE_ROOT}/{path}", "content": content, "overwrite": True}


def exec_payload(cmd: str) -> dict[str, Any]:
    return {"cmd": cmd, "yield_time_ms": 1000, "timeout": 30}


def trim_metrics(response: dict[str, Any]) -> dict[str, Any]:
    return {
        key: response.get(key)
        for key in (
            "success",
            "manifest_version",
            "manifest_depth",
            "layer_dirs",
            "referenced_layers",
            "orphan_layer_count",
            "missing_layer_count",
            "staging_dirs",
            "active_leases",
            "leased_layers",
            "storage_bytes",
        )
        if key in response
    }


def file_view(text: str) -> dict[str, str]:
    return {"kind": "file", "sha256": sha256_text(text), "text": text}


if __name__ == "__main__":
    raise SystemExit(main())
