"""Identify which P2P tests fail at base commit for an instance.

Uses the production ``_run_test_set`` path (chunked-base64 ID upload +
pytest.main + retry-drop) BUT monkey-patches the inner ``_exec`` to also
preserve the FAILED-summary stdout instead of discarding it.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


async def main_async() -> int:
    os.environ.setdefault("EOS_SANDBOX_PROVIDER", "docker")

    from task_center_runner.benchmarks.sweevo import eval as eval_mod
    from task_center_runner.benchmarks.sweevo._provision import _service
    from task_center_runner.benchmarks.sweevo.models import _sweevo_sandbox_name
    from task_center_runner.benchmarks.sweevo.setup import (
        bootstrap_sandbox_provider,
        load_sweevo_instance,
    )

    instance = load_sweevo_instance(instance_id="dask__dask_2023.3.2_2023.4.0")
    bootstrap_sandbox_provider()

    name = _sweevo_sandbox_name(instance)
    service = _service()
    existing = next((s for s in service.list_sandboxes() if s.get("name") == name), None)
    if existing is None:
        print(f"FATAL: container {name} not present", file=sys.stderr)
        return 2
    sandbox_id = str(existing["id"])

    # Capture every exec stdout the eval makes
    captured: list[str] = []
    real_exec = eval_mod._exec

    async def wrapped_exec(*args, **kwargs):
        out = await real_exec(*args, **kwargs)
        captured.append(out)
        return out

    eval_mod._exec = wrapped_exec  # type: ignore[attr-defined]

    # Run JUST the P2P set; assume test_patch is already applied or unnecessary
    # (we want the same scoring conditions the eval used).
    print(f"running {len(instance.pass_to_pass)} P2P IDs against base", flush=True)
    passed = await eval_mod._run_test_set(
        sandbox_id,
        "/testbed",
        instance.pass_to_pass,
        instance.test_cmds,
        timeout=900,
    )
    print(f"passed={passed} of {len(instance.pass_to_pass)}")

    # Mine the captured exec stdouts for FAILED lines (pytest -rA emits these)
    failed: set[str] = set()
    for chunk in captured:
        for m in re.finditer(r"^FAILED\s+(\S+)", chunk, re.MULTILINE):
            failed.add(m.group(1).strip())

    print(f"=== {len(failed)} unique FAILED test IDs ===")
    for fid in sorted(failed):
        print(fid)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
