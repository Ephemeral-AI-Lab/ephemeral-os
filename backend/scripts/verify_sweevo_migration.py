"""Happy-path verification for the sweevo layerstack migration.

Exercises the new pipeline building blocks WITHOUT running an LLM agent:

    preflight  →  provision_sandbox  →  apply_layerstack_to_repo (no-op)
                                    →  evaluate_sweevo_result

Expected verdict at the base commit (no agent fix applied):
- ``fail_to_pass_passed == 0``      (every F2P test still fails)
- ``pass_to_pass_passed == p2p_total``  (every P2P test still passes)

This is a real docker integration test, not a unit test. It pulls the
SWE-EVO image (~5 minutes first time), spins up a persistent container
named ``sweevo-<instance_id>``, and runs the configured pytest suite
inside the conda env. Re-runs reuse the container.

Usage:

    EOS_SANDBOX_PROVIDER=docker \\
    uv run python backend/scripts/verify_sweevo_migration.py \\
        --instance-id dask__dask_2023.3.2_2023.4.0 \\
        [--mode {smoke,full}] \\
        [--register-snapshot]
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

# Add backend/src to sys.path so `task_center_runner.benchmarks.sweevo` resolves.
_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_BACKEND_SRC))


def _step(msg: str) -> None:
    print(f"[verify {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--instance-id",
        default="dask__dask_2023.3.2_2023.4.0",
        help="SWE-EVO instance to verify against.",
    )
    parser.add_argument(
        "--mode",
        choices=("smoke", "full"),
        default="smoke",
        help="smoke = 5 F2P + 5 P2P (fast); full = entire suite.",
    )
    parser.add_argument(
        "--register-snapshot",
        action="store_true",
        help="Pre-pull and tag the SWE-EVO image before preflight.",
    )
    parser.add_argument("--repo-dir", default="/testbed")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    os.environ.setdefault("EOS_SANDBOX_PROVIDER", "docker")

    from task_center_runner.benchmarks.sweevo._snapshot import (
        register_sweevo_snapshot,
    )
    from task_center_runner.benchmarks.sweevo.eval import (
        apply_layerstack_to_repo,
        evaluate_sweevo_result,
    )
    from task_center_runner.benchmarks.sweevo.models import (
        PreContext,
        SWEEvoResult,
    )
    from task_center_runner.benchmarks.sweevo.setup import (
        bootstrap_sandbox_provider,
        load_sweevo_instance,
        provision_sandbox,
    )

    _step("loading sweevo instance metadata")
    instance = load_sweevo_instance(instance_id=args.instance_id)
    _step(
        f"instance: repo={instance.repo} base={instance.base_commit[:12]} "
        f"F2P={len(instance.fail_to_pass)} P2P={len(instance.pass_to_pass)}"
    )

    _step("bootstrapping sandbox provider")
    bootstrap_sandbox_provider()

    if args.register_snapshot:
        _step(f"registering snapshot (docker pull/tag) for {instance.docker_image}")
        register_sweevo_snapshot(instance)

    if args.mode == "smoke":
        # Slim the test surface so the verification finishes in minutes,
        # not hours. Keep enough to exercise both F2P and P2P code paths.
        instance.fail_to_pass = instance.fail_to_pass[:5]
        instance.pass_to_pass = instance.pass_to_pass[:5]
        _step(
            f"smoke mode: trimmed to F2P={len(instance.fail_to_pass)} "
            f"P2P={len(instance.pass_to_pass)}"
        )

    audit_dir = Path(".sweevo_runs/verification").resolve()
    audit_dir.mkdir(parents=True, exist_ok=True)
    ctx = PreContext(
        instance=instance,
        repo_dir=args.repo_dir,
        snapshot_name="",
        goal="(verification — no agent)",
        audit_dir=audit_dir,
        max_duration_s=600.0,
    )

    _step("provision_sandbox (create or resume + setup_sweevo_sandbox)")
    sandbox_id = await provision_sandbox(ctx)
    _step(f"sandbox ready: {sandbox_id}")

    _step("apply_layerstack_to_repo (no-op materialize — no agent edits)")
    await apply_layerstack_to_repo(sandbox_id, args.repo_dir)
    _step("materialize OK; .git postcondition passed")

    _step("evaluate_sweevo_result (apply test_patch + run F2P + run P2P)")
    result = SWEEvoResult(plan_id="verify", instance_id=instance.instance_id)
    t0 = time.monotonic()
    result = await evaluate_sweevo_result(instance, result, sandbox_id, args.repo_dir)
    elapsed = time.monotonic() - t0
    _step(f"evaluate complete in {elapsed:.1f}s")

    print()
    print("=== sweevo_result ===")
    print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    print()

    f2p_ok = result.fail_to_pass_passed == 0
    # In smoke mode some P2P tests may be skipped under -n0; allow a tiny
    # tolerance only in full mode by comparing passed-count == total.
    p2p_pass_count = result.pass_to_pass_total - result.pass_to_pass_broken
    p2p_ok = p2p_pass_count == result.pass_to_pass_total

    print("=== verdict ===")
    print(f"F2P expected 0 passed, got {result.fail_to_pass_passed}/"
          f"{result.fail_to_pass_total}  →  {'OK' if f2p_ok else 'FAIL'}")
    print(f"P2P expected {result.pass_to_pass_total} passed, got {p2p_pass_count}/"
          f"{result.pass_to_pass_total}  →  {'OK' if p2p_ok else 'FAIL'}")
    print(f"resolved={result.resolved} fix_rate={result.fix_rate:.4f}")

    out_path = audit_dir / "verify_sweevo_result.json"
    out_path.write_text(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    _step(f"wrote {out_path}")

    return 0 if (f2p_ok and p2p_ok) else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        _step("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
