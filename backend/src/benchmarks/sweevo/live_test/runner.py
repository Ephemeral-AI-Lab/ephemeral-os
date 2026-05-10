"""SWE-EVO compatibility shim — wires the SWE-EVO dataset into ``live_e2e.run_scenario``.

Existing callers import ``run_scenario`` and ``RunReport`` from
``benchmarks.sweevo.live_test.runner``. This module rebuilds the entry prompt
via :func:`build_sweevo_user_prompt` and delegates to :func:`live_e2e.run_scenario`.

The generic framework lives at ``backend/src/live_e2e/`` per
``docs/wiki/live-e2e-testing-framework-design.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR
from benchmarks.sweevo.prompt import build_sweevo_user_prompt
from live_e2e.hooks.registry import Hook
from live_e2e.runner import RunReport
from live_e2e.runner import run_scenario as _generic_run_scenario
from live_e2e.scenarios.base import Scenario
from live_e2e.stores import TaskCenterStoreBundle


async def run_scenario(
    scenario: Scenario,
    *,
    instance: SWEEvoInstance,
    sandbox_id: str,
    audit_dir: Path,
    stores: TaskCenterStoreBundle | None = None,
    repo_dir: str = _REPO_DIR,
    extra_hooks: Sequence[Hook] = (),
    user_prompt: str | None = None,
) -> RunReport:
    """Legacy SWE-EVO entry point — keeps the old call shape."""
    entry_prompt = (
        user_prompt
        if user_prompt is not None
        else build_sweevo_user_prompt(instance, repo_dir=repo_dir)
    )
    return await _generic_run_scenario(
        scenario,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        repo_dir=repo_dir,
        entry_prompt=entry_prompt,
        stores=stores,
        extra_hooks=extra_hooks,
        instance_id=instance.instance_id,
    )


__all__ = ["RunReport", "run_scenario"]
