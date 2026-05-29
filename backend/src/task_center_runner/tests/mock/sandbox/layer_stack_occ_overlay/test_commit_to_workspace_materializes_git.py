"""Live regression for the opt-in ``commit_to_workspace`` projection.

This is the consumer that justifies the opt-in: it asserts that a scenario's
layer-stack overlay edits are materialized onto the on-disk ``/testbed`` git repo
when ``run_scenario_on_sweevo_image(..., commit_to_workspace=True)`` is used —
mirroring sweevo's ``SweevoLifecycle.after_run`` -> ``apply_layerstack_to_repo``
contract (``benchmarks/sweevo/eval.py``).

Contract asserted:

- ``report.task_center_status == 'done'``.
- ``/testbed/.git`` survives the projection (``apply_layerstack_to_repo``
  postcondition — the overlay opaque-dir marker must not shadow the repo).
- A *raw* (non-daemon) read of the mock agent's known write sees it on the base
  ``/testbed`` disk. A raw read only sees it because ``commit_to_workspace``
  projected the overlay down to the base; without the opt-in the write lives
  only in the layer-stack overlay and host-side git cannot see it.
- Host-side ``git`` on ``/testbed`` lists the materialized path (the ``git
  diff``/``git log`` consumer), mirroring sweevo's
  ``_extract_combined_patch`` (``git add -A && git diff HEAD``).

The peer test ``test_auto_squash_commit_resume`` already covers the squash/OCC
internals through the daemon overlay; this test covers only the post-run
workspace projection, which that test path does not exercise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sandbox.api as sandbox_api
from task_center_runner.benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.environments.sweevo_image.fixtures import (
    run_scenario_on_sweevo_image,
)
from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.tests._live_config import database_configured


pytestmark = pytest.mark.asyncio

_REPO_DIR = "/testbed"
# Deterministic write produced by the ``sandbox.auto_squash_commit_resume``
# scenario probe (asserted from the overlay side in test_auto_squash_commit_resume).
_REL_EDIT_TARGET = ".ephemeralos/sweevo-mock/auto_squash_commit_resume/edit-target.txt"
_EDIT_TARGET = f"{_REPO_DIR}/{_REL_EDIT_TARGET}"
_EXPECTED_CONTENT = "alpha=new\nbeta=new\n"


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
async def test_commit_to_workspace_materializes_layerstack_edits_into_testbed_git(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario = SCENARIO_REGISTRY["sandbox.auto_squash_commit_resume"]()
    sandbox_id = str(workspace["sandbox_id"])

    try:
        report = await run_scenario_on_sweevo_image(
            scenario,
            instance=sweevo_image_instance,
            sandbox_id=sandbox_id,
            audit_dir=audit_dir,
            stores=stores,
            commit_to_workspace=True,
        )
        assert report.task_center_status == "done", report.metrics

        # 1. .git survives the projection.
        git_present = await sandbox_api.raw_exec(
            sandbox_id,
            f"test -d {_REPO_DIR}/.git && echo OK",
            cwd="/",
            timeout=60,
        )
        assert (getattr(git_present, "stdout", "") or "").strip() == "OK", (
            getattr(git_present, "stderr", "")
        )

        # 2. The agent's overlay write is now on the *base* /testbed disk: a raw
        #    read (not the daemon overlay) sees it only because the projection ran.
        materialized = await sandbox_api.raw_exec(
            sandbox_id,
            f"cat {_EDIT_TARGET}",
            cwd="/",
            timeout=60,
        )
        assert materialized.exit_code == 0, getattr(materialized, "stderr", "")
        assert materialized.stdout == _EXPECTED_CONTENT

        # 3. Host-side git on /testbed sees the materialized changeset. ``-f``
        #    keeps the assertion robust regardless of the repo's .gitignore; the
        #    materialization itself is proved gitignore-free by assertion #2.
        diff = await sandbox_api.raw_exec(
            sandbox_id,
            (
                f"cd {_REPO_DIR} && git add -f -- {_REL_EDIT_TARGET} "
                f"&& git diff --cached --name-only -- {_REL_EDIT_TARGET}"
            ),
            cwd="/",
            timeout=120,
        )
        assert diff.exit_code == 0, getattr(diff, "stderr", "")
        assert _REL_EDIT_TARGET in (diff.stdout or ""), diff.stdout
    finally:
        # Defensive teardown: drop the materialized scratch dir from the base
        # disk so it cannot leak into the next test through the .gitignore-aware
        # ``git clean -fd`` in setup_sweevo_sandbox.
        await sandbox_api.raw_exec(
            sandbox_id,
            f"rm -rf {_REPO_DIR}/.ephemeralos",
            cwd="/",
            timeout=30,
        )
