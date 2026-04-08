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

# MultiAgentEventPrinter and run_sweevo_with_agent are imported lazily inside
# _cmd_run so that ``--help`` / ``--list`` still work in minimal envs without
# the full providers dependency tree.


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


_RED = "\033[31m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_RESET = "\033[0m"


def _make_pytest_line_forwarder(printer: "Any", *, color: bool) -> "callable":
    """Return an ``on_line`` callback that forwards pytest stdout through the
    shared :class:`MultiAgentEventPrinter` via ``raw_line`` under the agent
    tag ``pytest``. Tracks pass/fail counters for the summary banner.
    """
    counts = {"passed": 0, "failed": 0, "errors": 0}

    def _tag(label: str, code: str) -> str:
        return f"{code}{label}{_RESET}" if color else label

    def _p(line: str) -> None:
        stripped = line.strip()
        label = "[test]"
        if stripped.startswith("PASSED") or " PASSED" in stripped:
            counts["passed"] += 1
            label = _tag("[pass]", _GREEN)
        elif stripped.startswith("FAILED") or " FAILED" in stripped:
            counts["failed"] += 1
            label = _tag("[fail]", _RED)
        elif stripped.startswith("ERROR") or " ERROR" in stripped:
            counts["errors"] += 1
            label = _tag("[error]", _RED)
        elif stripped.startswith("===") or stripped.startswith("---"):
            label = _tag("[info]", _CYAN)
        elif stripped.startswith("collected") or "test session starts" in stripped:
            label = _tag("[info]", _MAGENTA)
        printer.raw_line("pytest", f"{label} {line}")

    _p.counts = counts  # type: ignore[attr-defined]
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
    from message.event_printer import MultiAgentEventPrinter
    from benchmarks.sweevo.runner import run_sweevo_with_agent

    use_color = (not args.no_color) and sys.stdout.isatty()
    quiet = args.no_stream
    printer = MultiAgentEventPrinter(
        color=use_color and not quiet,
        timestamps=True,
        sink=(lambda _line: None) if quiet else None,
    )
    on_line = _make_pytest_line_forwarder(printer, color=use_color and not quiet)

    if not quiet:
        header = "=" * 72
        print(header, flush=True)
        print(f"  SWE-EVO run  instance={args.instance_id or f'<auto size={args.size}>'}", flush=True)
        print(header, flush=True)

    result = await run_sweevo_with_agent(
        printer=printer,
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
    counts = on_line.counts  # type: ignore[attr-defined]

    if not quiet:
        print("=" * 72, flush=True)
        print(
            f"  agent_events={result.get('agent_events', 0)}  "
            f"exit_code={exit_code}  "
            f"passed={counts['passed']}  failed={counts['failed']}  "
            f"errors={counts['errors']}",
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
