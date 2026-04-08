"""CLI entrypoint for the SWE-EVO benchmark.

Examples:
    # List available instances
    python -m benchmarks.sweevo --list

    # Run a specific instance end-to-end (provision sandbox + required test)
    python -m benchmarks.sweevo --instance-id iterative__dvc_1.0.0a1_1.0.0a2

    # Auto-pick a medium-sized instance near target bullet count
    python -m benchmarks.sweevo --size medium --target-bullets 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from benchmarks.sweevo.dataset import load_sweevo_dataset, summarize_sweevo_instance
from benchmarks.sweevo.models import (
    _DEFAULT_DATASET_SOURCE,
    _DEFAULT_SWEEVO_TEST_TIMEOUT,
    _DEFAULT_TARGET_BULLETS,
    _REPO_DIR,
)
from benchmarks.sweevo.sandbox import prepare_sweevo_test_run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m benchmarks.sweevo",
        description="Run the SWE-EVO benchmark on a selected instance.",
    )
    p.add_argument("--source", default=_DEFAULT_DATASET_SOURCE, help="HF dataset id or .parquet path")
    p.add_argument("--instance-id", default=None, help="Exact instance_id to run")
    p.add_argument("--size", default="medium", choices=["small", "medium", "large", "any"])
    p.add_argument("--target-bullets", type=int, default=_DEFAULT_TARGET_BULLETS)
    p.add_argument("--list", action="store_true", help="List available instances and exit")
    p.add_argument("--repo-dir", default=_REPO_DIR)
    p.add_argument("--snapshot-name", default="")
    p.add_argument("--sandbox-name", default="")
    p.add_argument("--no-register-snapshot", action="store_true")
    p.add_argument("--cpu", type=int, default=2)
    p.add_argument("--disk", type=int, default=10)
    p.add_argument("--test-command", default=None, help="Override instance.test_cmds")
    p.add_argument("--test-timeout", type=int, default=_DEFAULT_SWEEVO_TEST_TIMEOUT)
    p.add_argument("--no-stream", action="store_true", help="Disable live line streaming")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


_ANSI = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
}


def _make_line_printer(*, color: bool) -> "callable":
    import time as _t

    start = _t.monotonic()
    passed = 0
    failed = 0
    errors = 0

    def c(code: str, text: str) -> str:
        return f"{_ANSI[code]}{text}{_ANSI['reset']}" if color else text

    def _p(line: str) -> None:
        nonlocal passed, failed, errors
        t = _t.monotonic() - start
        stamp = c("dim", f"[{t:7.1f}s]")
        tag = "     "
        stripped = line.strip()
        if stripped.startswith("PASSED") or " PASSED" in stripped:
            passed += 1
            tag = c("green", " PASS")
        elif stripped.startswith("FAILED") or " FAILED" in stripped:
            failed += 1
            tag = c("red", " FAIL")
        elif stripped.startswith("ERROR") or " ERROR" in stripped:
            errors += 1
            tag = c("red", " ERR ")
        elif stripped.startswith("===") or stripped.startswith("---"):
            tag = c("cyan", " ----")
        elif stripped.startswith("collected") or "test session starts" in stripped:
            tag = c("magenta", " INFO")
        print(f"{stamp} {tag} {line}", flush=True)

    def summary() -> dict:
        return {"passed": passed, "failed": failed, "errors": errors}

    _p.summary = summary  # type: ignore[attr-defined]
    return _p


def _cmd_list(source: str) -> int:
    instances = load_sweevo_dataset(source)
    for inst in instances:
        summary = summarize_sweevo_instance(inst)
        print(
            f"{summary['instance_id']}\t"
            f"size={summary['size']}\t"
            f"bullets={summary['bullet_count']}\t"
            f"repo={summary['repo']}"
        )
    print(f"\nTotal: {len(instances)} instances", file=sys.stderr)
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    use_color = (not args.no_color) and sys.stdout.isatty()
    on_line = None if args.no_stream else _make_line_printer(color=use_color)

    if not args.no_stream:
        header = "=" * 72
        print(header, flush=True)
        print(f"  SWE-EVO run  instance={args.instance_id or f'<auto size={args.size}>'}", flush=True)
        print(header, flush=True)

    result = await prepare_sweevo_test_run(
        source=args.source,
        instance_id=args.instance_id,
        size=args.size,
        target_bullets=args.target_bullets,
        snapshot_name=args.snapshot_name,
        sandbox_name=args.sandbox_name,
        register_snapshot=not args.no_register_snapshot,
        cpu=args.cpu,
        disk=args.disk,
        repo_dir=args.repo_dir,
        test_command=args.test_command,
        test_timeout=args.test_timeout,
        on_line=on_line,
    )

    test = result.get("test", {})
    exit_code = test.get("exit_code")

    if on_line is not None:
        summary = on_line.summary()  # type: ignore[attr-defined]
        print("=" * 72, flush=True)
        print(
            f"  exit_code={exit_code}  "
            f"passed={summary['passed']}  failed={summary['failed']}  "
            f"errors={summary['errors']}",
            flush=True,
        )
        print("=" * 72, flush=True)
    else:
        # sandbox objects may not be JSON-serializable; coerce via str fallback.
        print(json.dumps(result, indent=2, default=str))

    return 0 if exit_code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.list:
        return _cmd_list(args.source)
    return asyncio.run(_cmd_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
