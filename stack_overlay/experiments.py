"""Command-line probes for the stack overlay experiment.

The unit tests exercise the model without mount privileges. This module also
contains a small Linux-only mount probe that can be executed inside Daytona:

    python -m stack_overlay.experiments mount-probe --depth 100
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from stack_overlay.experiment_suite import PROFILES, run_experiment_suite
from stack_overlay.layer_manager import LayerManager
from stack_overlay.mounts import (
    DEFAULT_MAX_DEPTH,
    build_mount_spec,
    mount_overlay_syscall,
    unmount_overlay_syscall,
)
from stack_overlay.occ import OccCommitter, content_hash
from stack_overlay.models import LayerChange, WriteChange


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("mount-probe", help="run a real overlay mount probe")
    probe.add_argument("--root", default="/dev/shm/stack-overlay-probe")
    probe.add_argument("--depth", type=int, default=DEFAULT_MAX_DEPTH)
    probe.add_argument("--iterations", type=int, default=100)
    probe.add_argument("--absolute", action="store_true")
    probe.add_argument(
        "--method",
        choices=("syscall", "mount8"),
        default="syscall",
        help="syscall uses libc mount(2); mount8 shells out to util-linux mount",
    )
    sub.add_parser("simulate", help="run a no-privilege OCC/layer simulation")
    suite = sub.add_parser("suite", help="run the synthetic E4-E14 suite")
    suite.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="standard",
        help="suite workload size",
    )
    suite.add_argument(
        "--quiet",
        action="store_true",
        help="suppress in-flight progress logs on stderr",
    )
    args = parser.parse_args()

    if args.command == "mount-probe":
        print(json.dumps(run_mount_probe(args), indent=2, sort_keys=True))
    elif args.command == "simulate":
        print(json.dumps(run_simulation(), indent=2, sort_keys=True))
    elif args.command == "suite":
        progress_log = None if args.quiet else _log_progress
        print(
            json.dumps(
                run_experiment_suite(
                    profile_name=args.profile,
                    progress_log=progress_log,
                ),
                indent=2,
                sort_keys=True,
            )
        )


def run_mount_probe(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root)
    manager = LayerManager.create(
        root / "session",
        {"shared.txt": "base\n"},
        max_depth=max(args.depth + 1, DEFAULT_MAX_DEPTH),
        squash_trigger=args.depth + 1,
        squash_target=max(2, min(40, args.depth)),
    )
    for index in range(1, args.depth):
        manager.commit([LayerChange("shared.txt", "write", f"layer-{index}\n")])
    manifest = manager.snapshot()
    timings: list[float] = []
    failures: list[str] = []
    for iteration in range(args.iterations):
        run_dir = root / f"run-{iteration}"
        for child in ("u", "w", "m"):
            (run_dir / child).mkdir(parents=True, exist_ok=True)
        spec = build_mount_spec(
            session_root=manager.session_root,
            manifest=manifest,
            run_dir=run_dir,
            relative_lowerdir=not args.absolute,
            max_depth=max(args.depth, 10),
        )
        started = time.perf_counter()
        completed = _mount_probe_once(args.method, spec)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if completed["returncode"] == 0:
            _unmount_probe_once(args.method, spec.merged)
            timings.append(elapsed_ms)
        else:
            failures.append(str(completed["stderr"])[:300])
        shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)
    timings.sort()
    return {
        "depth": args.depth,
        "iterations": args.iterations,
        "method": args.method,
        "relative_lowerdir": not args.absolute,
        "options_len": len(spec.options) if "spec" in locals() else 0,
        "failures": len(failures),
        "first_failure": failures[0] if failures else "",
        "p50_ms": _percentile(timings, 50),
        "p95_ms": _percentile(timings, 95),
        "p99_ms": _percentile(timings, 99),
        "pid": os.getpid(),
    }


def run_simulation() -> dict[str, object]:
    root = Path("/tmp/stack-overlay-sim")
    manager = LayerManager.create(
        root,
        {"a.txt": "v1\n", "config.yaml": "mode: old\n"},
        max_depth=DEFAULT_MAX_DEPTH,
        squash_trigger=80,
        squash_target=40,
    )
    occ = OccCommitter(manager)
    lease = manager.acquire()
    base_a, existed = manager.read_text("a.txt", lease.manifest)
    assert existed

    first = occ.apply(
        [
            WriteChange(
                "a.txt",
                "v2\n",
                base_existed=True,
                base_hash=content_hash(base_a),
            )
        ]
    )
    stale = occ.apply(
        [
            WriteChange(
                "a.txt",
                "stale\n",
                base_existed=True,
                base_hash=content_hash(base_a),
            ),
            WriteChange(
                "derived.json",
                "{}\n",
                base_existed=False,
            ),
        ]
    )
    manager.release(lease)
    shutil.rmtree(root, ignore_errors=True)
    return {
        "first_success": first.success,
        "stale_success": stale.success,
        "stale_files": [
            {"path": item.path, "status": item.status.value, "message": item.message}
            for item in stale.files
        ],
        "final_depth": stale.manifest.depth,
    }


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, int((len(values) - 1) * pct / 100)))
    return values[index]


def _mount_probe_once(method: str, spec: object) -> dict[str, object]:
    if method == "syscall":
        try:
            mount_overlay_syscall(spec)  # type: ignore[arg-type]
        except OSError as exc:
            return {"returncode": getattr(exc, "errno", 1) or 1, "stderr": str(exc)}
        return {"returncode": 0, "stderr": ""}

    completed = subprocess.run(
        ["mount", "-t", "overlay", "overlay", "-o", spec.options, spec.merged],
        cwd=spec.cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {"returncode": completed.returncode, "stderr": completed.stderr}


def _unmount_probe_once(method: str, merged: str) -> None:
    if method == "syscall":
        unmount_overlay_syscall(merged)
        return
    subprocess.run(
        ["umount", merged],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _log_progress(message: str) -> None:
    print(
        f"[stack_overlay][{time.strftime('%H:%M:%S')}] {message}",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
