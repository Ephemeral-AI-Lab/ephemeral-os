"""SWE-EVO benchmark CLI (slim entrypoint per plan §5).

Two flags:

- ``--real-agent --instance-id=<id>`` — drive a real LLM run of one SWE-EVO
  instance through :func:`task_center.start_task_center_entry_run`
  (``runner=None``) via :func:`live_e2e.real_agent_run.run_sweevo_real_agent`.
  Writes the canonical live-e2e audit tree plus a ``sweevo_result.json``
  carrying the F2P/P2P verdict.
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
import signal
import subprocess
import sys
import time
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


_RUN_T0 = time.monotonic()


def _step(msg: str) -> None:
    """Emit a timestamped progress line to stderr (always flushed)."""
    elapsed = time.monotonic() - _RUN_T0
    print(f"[sweevo +{elapsed:7.2f}s] {msg}", file=sys.stderr, flush=True)


def _bootstrap_sandbox_provider() -> None:
    from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider

    _step("bootstrap: importing daytona provider")
    bootstrap_daytona_provider()
    _step("bootstrap: daytona provider ready")


def _kill_other_sweevo_processes() -> None:
    """Terminate any other running ``benchmarks.sweevo`` processes.

    Ensures only one run executes at a time. Sends SIGTERM, waits briefly,
    then SIGKILL anything still alive. Self and parent PIDs are excluded.
    """
    self_pid = os.getpid()
    parent_pid = os.getppid()
    _step(f"single-run guard: scanning for siblings (self_pid={self_pid}, parent_pid={parent_pid})")

    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        _step(f"single-run guard: could not enumerate processes: {exc}")
        return

    targets: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, cmd = line.partition(" ")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid in (self_pid, parent_pid):
            continue
        if "benchmarks.sweevo" not in cmd:
            continue
        if "_kill_other_sweevo_processes" in cmd:
            continue
        targets.append(pid)

    if not targets:
        _step("single-run guard: no sibling processes found")
        return

    _step(f"single-run guard: SIGTERM-ing {len(targets)} sibling(s): {targets}")
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            _step(f"single-run guard: cannot SIGTERM {pid}: {exc}")

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        alive: list[int] = []
        for pid in targets:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            else:
                alive.append(pid)
        if not alive:
            _step("single-run guard: all siblings exited after SIGTERM")
            return
        time.sleep(0.2)
        targets = alive

    _step(f"single-run guard: SIGKILL-ing {len(targets)} survivor(s): {targets}")
    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            _step(f"single-run guard: cannot SIGKILL {pid}: {exc}")


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
        help="Run a real-LLM SWE-EVO instance through the task_center pipeline.",
    )
    parser.add_argument(
        "--csv-runner",
        action="store_true",
        help=(
            "Run an SWE-EVO instance through the task-center pipeline with "
            "entry_executor mocked; the goal is the raw CSV pr_description."
        ),
    )
    parser.add_argument(
        "--csv-path",
        default=None,
        help=(
            "Override the PR descriptions CSV path "
            "(defaults to SWEEVO_PR_DESCRIPTIONS_CSV env or the bundled CSV)."
        ),
    )
    parser.add_argument(
        "--max-duration-s",
        type=float,
        default=10800.0,
        help="Wall-clock cap for the real-agent task_center run (default 3h).",
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
    _step(f"scenario: starting (scenario={args.scenario!r}, instance_id={args.instance_id!r})")
    from benchmarks.sweevo.dataset import select_sweevo_instance
    from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox
    from live_e2e.scenarios import SCENARIO_REGISTRY
    from live_e2e.sweevo_adapter import run_sweevo_scenario

    _step("scenario: looking up scenario class in SCENARIO_REGISTRY")
    scenario_cls = SCENARIO_REGISTRY.get(args.scenario)
    if scenario_cls is None:
        _step(f"scenario: UNKNOWN — registered scenarios: {sorted(SCENARIO_REGISTRY)}")
        print(
            f"Unknown scenario: {args.scenario!r}. "
            f"Available: {sorted(SCENARIO_REGISTRY)}",
            file=sys.stderr,
        )
        return 2

    _bootstrap_sandbox_provider()
    _step(f"scenario: selecting instance (size={args.size}, target_bullets={args.target_bullets})")
    instance = select_sweevo_instance(
        source=args.source,
        instance_id=args.instance_id,
        size=args.size,
        target_bullets=args.target_bullets,
    )
    _step(f"scenario: instance selected — instance_id={instance.instance_id}")
    _step("scenario: creating sweevo test sandbox (register_snapshot=True)")
    sandbox_result = await create_sweevo_test_sandbox(
        instance,
        register_snapshot=True,
        repo_dir=args.repo_dir,
    )
    sandbox_id = str(sandbox_result["sandbox_id"])
    _step(f"scenario: sandbox ready — sandbox_id={sandbox_id}")
    audit_dir = (
        Path(args.audit_dir) if args.audit_dir
        else Path(os.getenv("EOS_SWEEVO_AUDIT_DIR", ".sweevo_runs")).resolve()
    )
    _step(f"scenario: audit_dir={audit_dir}")
    _step("scenario: invoking run_sweevo_scenario")
    report = await run_sweevo_scenario(
        scenario_cls(),
        instance=instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        repo_dir=args.repo_dir,
    )
    _step(
        f"scenario: completed — status={report.task_center_status} "
        f"duration_s={report.duration_s:.1f} run_dir={report.run_dir}"
    )
    print(
        f"scenario={report.scenario_name} "
        f"task_center_run_id={report.task_center_run_id} "
        f"status={report.task_center_status} "
        f"run_dir={report.run_dir} "
        f"duration_s={report.duration_s:.1f}"
    )
    return 0 if report.task_center_status == "done" else 1


async def _cmd_real_agent(args: argparse.Namespace) -> int:
    _step(f"real_agent: starting (instance_id={args.instance_id!r})")
    if not args.instance_id:
        _step("real_agent: missing --instance-id")
        print("--real-agent requires --instance-id=<id>", file=sys.stderr)
        return 2

    from benchmarks.sweevo.dataset import select_sweevo_instance
    from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox
    from live_e2e.real_agent_run import run_sweevo_real_agent

    _bootstrap_sandbox_provider()
    _step(f"real_agent: selecting instance (source={args.source})")
    instance = select_sweevo_instance(source=args.source, instance_id=args.instance_id)
    _step(f"real_agent: instance selected — instance_id={instance.instance_id}")
    _step("real_agent: creating sweevo test sandbox (register_snapshot=True)")
    sandbox = await create_sweevo_test_sandbox(
        instance, register_snapshot=True, repo_dir=args.repo_dir
    )
    sandbox_id = str(sandbox["sandbox_id"])
    _step(f"real_agent: sandbox ready — sandbox_id={sandbox_id}")
    audit_dir = (
        Path(args.audit_dir) if args.audit_dir
        else Path(os.getenv("EOS_SWEEVO_AUDIT_DIR", ".sweevo_runs")).resolve()
    )
    _step(f"real_agent: audit_dir={audit_dir} max_duration_s={args.max_duration_s}")
    _step("real_agent: invoking run_sweevo_real_agent")
    report = await run_sweevo_real_agent(
        instance=instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        repo_dir=args.repo_dir,
        max_duration_s=args.max_duration_s,
    )
    r = report.sweevo_result
    _step(
        f"real_agent: completed — status={report.task_center_status} "
        f"resolved={r.resolved} fix_rate={r.fix_rate:.2f} "
        f"f2p={r.fail_to_pass_passed}/{r.fail_to_pass_total} "
        f"p2p_broken={r.pass_to_pass_broken}/{r.pass_to_pass_total} "
        f"duration_s={r.duration_s:.1f} aborted_by_timeout={report.aborted_by_timeout}"
    )
    print(
        f"real_agent instance_id={instance.instance_id} "
        f"task_center_run_id={report.task_center_run_id} "
        f"status={report.task_center_status} "
        f"resolved={r.resolved} fix_rate={r.fix_rate:.2f} "
        f"f2p={r.fail_to_pass_passed}/{r.fail_to_pass_total} "
        f"p2p_broken={r.pass_to_pass_broken}/{r.pass_to_pass_total} "
        f"duration_s={r.duration_s:.1f} "
        f"aborted_by_timeout={report.aborted_by_timeout} "
        f"run_dir={report.run_dir}"
    )
    return 0 if r.resolved else 1


async def _cmd_csv_runner(args: argparse.Namespace) -> int:
    """Drive one SWE-EVO instance through the production pipeline with entry_executor mocked.

    R6: the ``try``/``finally`` opens immediately after
    ``create_sweevo_test_sandbox`` returns, NOT around ``run_pipeline``.
    LSP install runs inside ``SweevoProvisioner.provision`` (which
    ``run_pipeline`` invokes); wrapping only ``run_pipeline`` would leak
    an orphan sandbox on LSP-install failure.
    """
    _step(f"csv_runner: starting (instance_id={args.instance_id!r})")
    if not args.instance_id:
        _step("csv_runner: missing --instance-id")
        print("--csv-runner requires --instance-id=<id>", file=sys.stderr)
        return 2

    _step("csv_runner: importing pipeline modules")
    from runtime.app_factory import RuntimeConfig

    import sandbox.api as sandbox_api
    from benchmarks.sweevo.dataset import load_sweevo_instance
    from benchmarks.sweevo.prompt import load_pr_description
    from benchmarks.sweevo.sandbox import (
        SnapshotNotRegisteredError,
        create_sweevo_test_sandbox,
        verify_sweevo_snapshot_exists,
    )
    from benchmarks.sweevo.models import _has_explicit_sweevo_image_version
    from task_center_runner.benchmarks.sweevo.csv_runner import (
        build_selective_entry_mock_runner_factory,
    )
    from task_center_runner.benchmarks.sweevo.lifecycle import SweevoLifecycle
    from task_center_runner.benchmarks.sweevo.provisioner import SweevoProvisioner
    from task_center_runner.core.bootstrap import bootstrap_real_agent_runtime
    from task_center_runner.core.config import RunConfig
    from task_center_runner.core.engine import run_pipeline

    _step(f"csv_runner: loading PR description (csv_path={args.csv_path!r})")
    try:
        goal = load_pr_description(args.instance_id, csv_path=args.csv_path)
    except FileNotFoundError as exc:
        _step(f"csv_runner: CSV not found: {exc}")
        print(f"PR descriptions CSV not found: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        _step(f"csv_runner: unknown instance: {exc}")
        print(f"Unknown SWE-EVO instance: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        _step(f"csv_runner: empty pr_description: {exc}")
        print(f"Empty pr_description: {exc}", file=sys.stderr)
        return 2
    _step(f"csv_runner: PR description loaded ({len(goal)} chars)")

    _step("csv_runner: loading sweevo instance metadata")
    instance = load_sweevo_instance(source=args.source, instance_id=args.instance_id)
    _step(f"csv_runner: instance loaded — repo={instance.repo}")
    _bootstrap_sandbox_provider()

    snapshot_name = ""
    if _has_explicit_sweevo_image_version(instance.docker_image):
        _step("csv_runner: verifying sweevo snapshot is registered")
        try:
            snapshot_name = verify_sweevo_snapshot_exists(instance)
        except SnapshotNotRegisteredError as exc:
            _step(f"csv_runner: snapshot missing: {exc}")
            print(str(exc), file=sys.stderr)
            return 2
        _step(f"csv_runner: snapshot ok — snapshot_name={snapshot_name}")
    else:
        _step(
            "csv_runner: snapshot preflight skipped — image has no explicit "
            "non-latest version; using image directly"
        )

    _step("csv_runner: creating sweevo test sandbox")
    sandbox_result = await create_sweevo_test_sandbox(
        instance,
        sandbox_name="",
        snapshot_name=snapshot_name,
        register_snapshot=False,
        reuse_existing_auto=False,
        repo_dir=args.repo_dir,
    )
    sandbox_id = str(sandbox_result["sandbox_id"])
    _step(f"csv_runner: sandbox ready — sandbox_id={sandbox_id}")

    try:
        audit_dir = (
            Path(args.audit_dir) if args.audit_dir
            else Path(os.getenv("EOS_SWEEVO_AUDIT_DIR", ".sweevo_runs")).resolve()
        )
        _step(f"csv_runner: audit_dir={audit_dir} max_duration_s={args.max_duration_s}")
        _step("csv_runner: building RunConfig (provisioner, runner_factory, lifecycle)")
        runtime_cfg = RuntimeConfig(cwd=str(Path.cwd()), external_api_client=None)
        config = RunConfig(
            entry_prompt=goal,
            repo_dir=args.repo_dir,
            sandbox=SweevoProvisioner(
                instance,
                sandbox_id,
                repo_dir=args.repo_dir,
                install_lsp=True,
            ),
            runner_factory=build_selective_entry_mock_runner_factory(
                goal=goal, repo_dir=args.repo_dir
            ),
            lifecycle=SweevoLifecycle(
                instance,
                repo_dir=args.repo_dir,
                aggregate_jsonl_path=audit_dir / "aggregate.jsonl",
            ),
            bootstrap=bootstrap_real_agent_runtime,
            audit_dir=audit_dir,
            run_label=f"benchmark/sweevo_csv/{instance.instance_id}",
            instance_id=instance.instance_id,
            max_duration_s=args.max_duration_s,
            extras={"runtime_config": runtime_cfg},
        )
        _step("csv_runner: invoking run_pipeline (provision -> agent -> evaluate)")
        report = await run_pipeline(config)
        _step(
            f"csv_runner: run_pipeline returned — task_center_status={report.task_center_status} "
            f"task_center_run_id={report.task_center_run_id}"
        )
        sweevo_result = report.lifecycle_extras.get("sweevo_result")
        resolved = bool(getattr(sweevo_result, "resolved", False))
        fix_rate = float(getattr(sweevo_result, "fix_rate", 0.0))
        _step(f"csv_runner: verdict — resolved={resolved} fix_rate={fix_rate:.2f}")
        print(
            f"csv_runner instance_id={instance.instance_id} "
            f"task_center_run_id={report.task_center_run_id} "
            f"status={report.task_center_status} "
            f"resolved={resolved} fix_rate={fix_rate:.2f} "
            f"sandbox_id={sandbox_id} run_dir={report.run_dir}"
        )
        return 0 if resolved else 1
    finally:
        _step(f"csv_runner: cleanup — destroying sandbox {sandbox_id}")
        try:
            sandbox_api.delete_sandbox(sandbox_id)
            _step(f"csv_runner: sandbox {sandbox_id} destroyed")
        except Exception as exc:
            _step(f"csv_runner: failed to destroy sandbox {sandbox_id}: {exc}")
            print(
                f"Warning: failed to destroy sandbox {sandbox_id}: {exc}",
                file=sys.stderr,
            )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging()
    if args.list:
        return _cmd_list(args.source)
    _step(f"main: dispatch (pid={os.getpid()}, instance_id={args.instance_id!r})")
    _kill_other_sweevo_processes()
    if args.real_agent:
        try:
            rc = asyncio.run(_cmd_real_agent(args))
            _step(f"main: real_agent exit rc={rc}")
            return rc
        except KeyboardInterrupt:
            _step("main: KeyboardInterrupt in real_agent")
            print("\nInterrupted.", flush=True)
            return 130
    if args.csv_runner:
        try:
            rc = asyncio.run(_cmd_csv_runner(args))
            _step(f"main: csv_runner exit rc={rc}")
            return rc
        except KeyboardInterrupt:
            _step("main: KeyboardInterrupt in csv_runner")
            print("\nInterrupted.", flush=True)
            return 130
    if args.scenario:
        try:
            rc = asyncio.run(_cmd_scenario(args))
            _step(f"main: scenario exit rc={rc}")
            return rc
        except KeyboardInterrupt:
            _step("main: KeyboardInterrupt in scenario")
            print("\nInterrupted.", flush=True)
            return 130
    print(
        "Specify --list, --scenario <name>, --real-agent, or --csv-runner.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
