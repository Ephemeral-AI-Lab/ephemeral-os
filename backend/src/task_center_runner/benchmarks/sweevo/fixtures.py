"""SWE-EVO adapter for the project-wide live e2e framework.

The framework itself is dataset-agnostic. This module keeps the SWE-EVO entry
prompt, sandbox provisioning, and pytest fixtures in one explicit adapter while
the dataset glue remains under ``benchmarks.sweevo``.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import pytest

from config import get_central_config
from benchmarks.sweevo.dataset import select_sweevo_instance
from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR
from benchmarks.sweevo.prompt import build_sweevo_user_prompt
from task_center_runner.hooks.registry import Hook
from task_center_runner.core.runner import RunReport
from task_center_runner.core.runner import run_scenario as _generic_run_scenario
from task_center_runner.scenarios.base import Scenario
from task_center_runner.core.stores import TaskCenterStoreBundle

_DEFAULT_INSTANCE_ID = "dask__dask_2023.3.2_2023.4.0"
_SESSION_WORKSPACE_USED_ATTR = "_ephemeralos_sweevo_workspace_used_sandboxes"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCK_DIR = _REPO_ROOT / ".sweevo_runs" / "locks"
_HELD_SWEEVO_LOCKS: dict[Path, tuple[IO[str], int]] = {}


@dataclass(frozen=True, slots=True)
class _SweevoSessionLock:
    path: Path


async def run_sweevo_scenario(
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
    """Run a live e2e scenario with SWE-EVO prompt semantics."""
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


@pytest.fixture(scope="session")
def sweevo_instance() -> SWEEvoInstance:
    instance_id = os.getenv("EOS_SWEEVO_INSTANCE", _DEFAULT_INSTANCE_ID)
    return select_sweevo_instance(instance_id=instance_id)


@pytest.fixture(scope="session")
async def sweevo_sandbox(
    sweevo_instance: SWEEvoInstance,
) -> AsyncIterator[dict[str, object]]:
    """Provision a real Daytona sandbox for the configured SWE-EVO instance."""
    from sandbox.provider.bootstrap import bootstrap_sandbox_provider

    from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox
    from task_center_runner.tests.sweevo._sandbox_health import (
        require_sandbox_provider_healthy,
    )

    lock = _acquire_sweevo_session_lock(sweevo_instance.instance_id)
    try:
        bootstrap_sandbox_provider()
        require_sandbox_provider_healthy(sweevo_instance)
        yield await create_sweevo_test_sandbox(
            sweevo_instance,
            register_snapshot=True,
            reuse_existing_auto=_reuse_existing_auto_enabled(),
        )
    finally:
        _release_sweevo_session_lock(lock)


@pytest.fixture
async def workspace(
    sweevo_sandbox: dict[str, object],
    request: pytest.FixtureRequest,
) -> dict[str, object]:
    """Return a SWE-EVO workspace with per-test reset isolation."""
    sandbox_id = str(sweevo_sandbox["sandbox_id"])
    used_sandboxes = _session_workspace_used_sandboxes(request.session)
    first_use = sandbox_id not in used_sandboxes
    should_reset = (not first_use) or bool(sweevo_sandbox.get("reused_existing"))
    if should_reset:
        from benchmarks.sweevo.sandbox import reset_sweevo_workspace

        await reset_sweevo_workspace(sandbox_id)
    used_sandboxes.add(sandbox_id)
    return sweevo_sandbox


def _reuse_existing_auto_enabled() -> bool:
    """Return whether SWE-EVO tests may attach to an existing auto sandbox."""
    reuse_mode = get_central_config().runner.sandbox_reuse_mode
    if reuse_mode == "force_fresh":
        return False
    return reuse_mode == "reuse"


def _acquire_sweevo_session_lock(instance_id: str) -> _SweevoSessionLock:
    """Serialize live SWE-EVO runs that may reuse the same Daytona sandbox.

    Several scenarios intentionally rebind the public-tool workspace root
    during execution. Running two live pytest sessions for the same SWE-EVO
    instance against a reusable sandbox can make one session observe the
    other's binding. A host-side flock keeps setup and the test session
    isolated without adding a dependency.
    """
    import fcntl

    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _LOCK_DIR / f"sweevo-{_lock_slug(instance_id)}.lock"
    held = _HELD_SWEEVO_LOCKS.get(lock_path)
    if held is not None:
        handle, count = held
        _HELD_SWEEVO_LOCKS[lock_path] = (handle, count + 1)
        return _SweevoSessionLock(lock_path)

    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\ninstance={instance_id}\n")
    handle.flush()
    _HELD_SWEEVO_LOCKS[lock_path] = (handle, 1)
    return _SweevoSessionLock(lock_path)


def _release_sweevo_session_lock(lock: _SweevoSessionLock) -> None:
    import fcntl

    held = _HELD_SWEEVO_LOCKS.get(lock.path)
    if held is None:
        return
    handle, count = held
    if count > 1:
        _HELD_SWEEVO_LOCKS[lock.path] = (handle, count - 1)
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        _HELD_SWEEVO_LOCKS.pop(lock.path, None)
        handle.close()


def _lock_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "default"


def _session_workspace_used_sandboxes(session: object) -> set[str]:
    """Track workspace use in the current pytest process only."""
    current = getattr(session, _SESSION_WORKSPACE_USED_ATTR, None)
    if isinstance(current, set):
        return current
    used: set[str] = set()
    setattr(session, _SESSION_WORKSPACE_USED_ATTR, used)
    return used


__all__ = [
    "RunReport",
    "run_sweevo_scenario",
    "sweevo_instance",
    "sweevo_sandbox",
    "workspace",
]
