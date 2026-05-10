"""SWE-EVO benchmark CLI (slim entrypoint per plan §5).

Two flags:

- ``--real-agent`` — placeholder for the real-agent grading loop. Real-agent
  CLI work is out of scope for the live-e2e framework phase and is deferred to
  a follow-up; today this prints a deferred-notice and exits non-zero.
- ``--scenario <name>`` — drives the mock framework via
  :func:`live_e2e.sweevo_adapter.run_sweevo_scenario` against a live
  Daytona sandbox. The scenario must be registered in ``SCENARIO_REGISTRY``.

Pytest is the canonical entry point for the mock framework — see
``backend/src/live_e2e/tests/sweevo/`` for the regression tests.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from benchmarks.sweevo.dataset import (
    load_sweevo_dataset,
    summarize_sweevo_instance,
)
from benchmarks.sweevo.models import (
    _DEFAULT_DATASET_SOURCE,
    _DEFAULT_TARGET_BULLETS,
    _REPO_DIR,
)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.disable(logging.WARNING)


def _bootstrap_sandbox_provider() -> None:
    from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider

    bootstrap_daytona_provider()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.sweevo",
        description="SWE-EVO benchmark CLI — list / scenario / real-agent.",
    )
    parser.add_argument("--source", default=_DEFAULT_DATASET_SOURCE)
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available SWE-EVO instances and exit.",
    )
    parser.add_argument(
        "--instance-id",
        default=None,
        help="Exact SWE-EVO instance_id to run.",
    )
    parser.add_argument("--size", default="medium", choices=["small", "medium", "large", "any"])
    parser.add_argument("--target-bullets", type=int, default=_DEFAULT_TARGET_BULLETS)
    parser.add_argument("--repo-dir", default=_REPO_DIR)
    parser.add_argument(
        "--scenario",
        default=None,
        help="Run the named live-e2e scenario from SCENARIO_REGISTRY.",
    )
    parser.add_argument(
        "--real-agent",
        action="store_true",
        help="Real-agent grading path — DEFERRED to a follow-up phase.",
    )
    parser.add_argument(
        "--audit-dir",
        default=None,
        help="Override audit base dir (defaults to .sweevo_runs/).",
    )
    return parser


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


async def _cmd_scenario(args: argparse.Namespace) -> int:
    from benchmarks.sweevo.dataset import select_sweevo_instance
    from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox
    from live_e2e.scenarios import SCENARIO_REGISTRY
    from live_e2e.sweevo_adapter import run_sweevo_scenario

    scenario_cls = SCENARIO_REGISTRY.get(args.scenario)
    if scenario_cls is None:
        print(
            f"Unknown scenario: {args.scenario!r}. "
            f"Available: {sorted(SCENARIO_REGISTRY)}",
            file=sys.stderr,
        )
        return 2

    _bootstrap_sandbox_provider()
    instance = select_sweevo_instance(
        source=args.source,
        instance_id=args.instance_id,
        size=args.size,
        target_bullets=args.target_bullets,
    )
    sandbox_result = await create_sweevo_test_sandbox(
        instance,
        register_snapshot=True,
        repo_dir=args.repo_dir,
    )
    audit_dir = (
        Path(args.audit_dir) if args.audit_dir
        else Path(os.getenv("EOS_SWEEVO_AUDIT_DIR", ".sweevo_runs")).resolve()
    )
    report = await run_sweevo_scenario(
        scenario_cls(),
        instance=instance,
        sandbox_id=str(sandbox_result["sandbox_id"]),
        audit_dir=audit_dir,
        repo_dir=args.repo_dir,
    )
    print(
        f"scenario={report.scenario_name} "
        f"task_center_run_id={report.task_center_run_id} "
        f"status={report.task_center_status} "
        f"run_dir={report.run_dir} "
        f"duration_s={report.duration_s:.1f}"
    )
    return 0 if report.task_center_status == "done" else 1


def _cmd_real_agent() -> int:
    print(
        "--real-agent is deferred to a follow-up phase. "
        "Use --scenario <name> for the mock framework, "
        "or run pytest under backend/src/live_e2e/tests/sweevo/.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging()
    if args.list:
        return _cmd_list(args.source)
    if args.real_agent:
        return _cmd_real_agent()
    if args.scenario:
        try:
            return asyncio.run(_cmd_scenario(args))
        except KeyboardInterrupt:
            print("\nInterrupted.", flush=True)
            return 130
    print("Specify --list, --scenario <name>, or --real-agent.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
