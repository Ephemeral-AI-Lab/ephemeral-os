"""SWE-EVO benchmark CLI.

Canonical entry point:

``python -m benchmarks.sweevo --instance-id=<id>`` runs the full
``benchmark_sweevo`` lifecycle through the task-center runner pipeline. The
entry prompt is the raw SWE-EVO ``pr_description`` CSV value and the lifecycle
evaluates F2P/P2P at the end.
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

from benchmarks.sweevo.models import (
    _DEFAULT_DATASET_SOURCE,
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
    from sandbox.provider.bootstrap import bootstrap_sandbox_provider

    _step("bootstrap: selecting sandbox provider via EOS_SANDBOX_PROVIDER")
    bootstrap_sandbox_provider()
    _step("bootstrap: sandbox provider ready")


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
        description="Run one SWE-EVO instance through the benchmark_sweevo lifecycle.",
    )
    parser.add_argument("--source", default=_DEFAULT_DATASET_SOURCE)
    parser.add_argument(
        "--instance-id",
        required=True,
        help="Exact SWE-EVO instance_id to run.",
    )
    parser.add_argument("--repo-dir", default=_REPO_DIR)
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


async def _cmd_benchmark_sweevo(args: argparse.Namespace) -> int:
    """Drive one SWE-EVO instance through the production benchmark lifecycle.

    R6: the ``try``/``finally`` opens immediately after
    ``create_sweevo_test_sandbox`` returns, NOT around ``run_pipeline``.
    LSP install runs inside ``SweevoProvisioner.provision`` (which
    ``run_pipeline`` invokes); wrapping only ``run_pipeline`` would leak
    an orphan sandbox on LSP-install failure.
    """
    _step(f"benchmark_sweevo: starting (instance_id={args.instance_id!r})")
    if not args.instance_id:
        _step("benchmark_sweevo: missing --instance-id")
        print("benchmark_sweevo requires --instance-id=<id>", file=sys.stderr)
        return 2

    _step("benchmark_sweevo: importing pipeline modules")
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
    from task_center_runner.benchmarks.sweevo.agent_runner import (
        build_benchmark_sweevo_delegate_factory,
    )
    from task_center_runner.benchmarks.sweevo.lifecycle import SweevoLifecycle
    from task_center_runner.benchmarks.sweevo.provisioner import SweevoProvisioner
    from task_center_runner.core.bootstrap import bootstrap_real_agent_runtime
    from task_center_runner.core.config import RunConfig
    from task_center_runner.core.engine import run_pipeline

    _step(f"benchmark_sweevo: loading PR description (csv_path={args.csv_path!r})")
    try:
        goal = load_pr_description(args.instance_id, csv_path=args.csv_path)
    except FileNotFoundError as exc:
        _step(f"benchmark_sweevo: CSV not found: {exc}")
        print(f"PR descriptions CSV not found: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        _step(f"benchmark_sweevo: unknown instance: {exc}")
        print(f"Unknown SWE-EVO instance: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        _step(f"benchmark_sweevo: empty pr_description: {exc}")
        print(f"Empty pr_description: {exc}", file=sys.stderr)
        return 2
    _step(f"benchmark_sweevo: PR description loaded ({len(goal)} chars)")

    _step("benchmark_sweevo: loading sweevo instance metadata")
    instance = load_sweevo_instance(source=args.source, instance_id=args.instance_id)
    _step(f"benchmark_sweevo: instance loaded — repo={instance.repo}")
    _bootstrap_sandbox_provider()

    snapshot_name = ""
    if _has_explicit_sweevo_image_version(instance.docker_image):
        _step("benchmark_sweevo: verifying sweevo snapshot is registered")
        try:
            snapshot_name = verify_sweevo_snapshot_exists(instance)
        except SnapshotNotRegisteredError as exc:
            _step(f"benchmark_sweevo: snapshot missing: {exc}")
            print(str(exc), file=sys.stderr)
            return 2
        _step(f"benchmark_sweevo: snapshot ok — snapshot_name={snapshot_name}")
    else:
        _step(
            "benchmark_sweevo: snapshot preflight skipped — image has no explicit "
            "non-latest version; using image directly"
        )

    _step("benchmark_sweevo: creating sweevo test sandbox")
    sandbox_result = await create_sweevo_test_sandbox(
        instance,
        sandbox_name="",
        snapshot_name=snapshot_name,
        register_snapshot=False,
        reuse_existing_auto=False,
        repo_dir=args.repo_dir,
    )
    sandbox_id = str(sandbox_result["sandbox_id"])
    _step(f"benchmark_sweevo: sandbox ready — sandbox_id={sandbox_id}")

    try:
        audit_dir = (
            Path(args.audit_dir) if args.audit_dir
            else Path(os.getenv("EOS_SWEEVO_AUDIT_DIR", ".sweevo_runs")).resolve()
        )
        _step(f"benchmark_sweevo: audit_dir={audit_dir} max_duration_s={args.max_duration_s}")
        _step("benchmark_sweevo: building RunConfig (provisioner, runner_factory, lifecycle)")
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
            runner_factory=build_benchmark_sweevo_delegate_factory(repo_dir=args.repo_dir),
            lifecycle=SweevoLifecycle(
                instance,
                repo_dir=args.repo_dir,
                aggregate_jsonl_path=audit_dir / "aggregate.jsonl",
            ),
            bootstrap=bootstrap_real_agent_runtime,
            audit_dir=audit_dir,
            run_label=f"benchmark/sweevo/{instance.instance_id}",
            instance_id=instance.instance_id,
            max_duration_s=args.max_duration_s,
            extras={"runtime_config": runtime_cfg},
        )
        _step("benchmark_sweevo: invoking run_pipeline (provision -> agent -> evaluate)")
        report = await run_pipeline(config)
        _step(
            f"benchmark_sweevo: run_pipeline returned — task_center_status={report.task_center_status} "
            f"task_center_run_id={report.task_center_run_id}"
        )
        sweevo_result = report.lifecycle_extras.get("sweevo_result")
        resolved = bool(getattr(sweevo_result, "resolved", False))
        fix_rate = float(getattr(sweevo_result, "fix_rate", 0.0))
        _step(f"benchmark_sweevo: verdict — resolved={resolved} fix_rate={fix_rate:.2f}")
        print(
            f"benchmark_sweevo instance_id={instance.instance_id} "
            f"task_center_run_id={report.task_center_run_id} "
            f"status={report.task_center_status} "
            f"resolved={resolved} fix_rate={fix_rate:.2f} "
            f"sandbox_id={sandbox_id} run_dir={report.run_dir}"
        )
        return 0 if resolved else 1
    finally:
        _step(f"benchmark_sweevo: cleanup — destroying sandbox {sandbox_id}")
        try:
            sandbox_api.delete_sandbox(sandbox_id)
            _step(f"benchmark_sweevo: sandbox {sandbox_id} destroyed")
        except Exception as exc:
            _step(f"benchmark_sweevo: failed to destroy sandbox {sandbox_id}: {exc}")
            print(
                f"Warning: failed to destroy sandbox {sandbox_id}: {exc}",
                file=sys.stderr,
            )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging()
    _step(f"main: dispatch (pid={os.getpid()}, instance_id={args.instance_id!r})")
    _kill_other_sweevo_processes()
    try:
        rc = asyncio.run(_cmd_benchmark_sweevo(args))
        _step(f"main: benchmark_sweevo exit rc={rc}")
        return rc
    except KeyboardInterrupt:
        _step("main: KeyboardInterrupt in benchmark_sweevo")
        print("\nInterrupted.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
