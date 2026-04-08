"""SWE-EVO team runner.

Drives a full builtin team (planner → developer → validator) against a
SWE-EVO instance inside its Daytona sandbox. Each WorkItem spawned by
the team dispatcher runs through :func:`engine.runtime.agent.spawn_agent`
with its full production tool surface, and every ``StreamEvent`` is
forwarded to the shared :class:`MultiAgentEventPrinter` so the CLI shows
all agents in the same multi-column log.
"""

from __future__ import annotations

import logging
from typing import Any

from benchmarks.sweevo.dataset import select_sweevo_instance, summarize_sweevo_instance
from benchmarks.sweevo.evaluation import _extract_combined_patch
from benchmarks.sweevo.models import (
    _DEFAULT_DATASET_SOURCE,
    _DEFAULT_SWEEVO_TEST_TIMEOUT,
    _DEFAULT_TARGET_BULLETS,
    _REPO_DIR,
)
from benchmarks.sweevo.sandbox import (
    create_sweevo_test_sandbox,
    run_sweevo_required_test,
)

logger = logging.getLogger(__name__)


async def run_sweevo_with_agent(
    *,
    printer: "Any",
    source: str = _DEFAULT_DATASET_SOURCE,
    instance_id: str | None = None,
    size: str = "medium",
    target_bullets: int = _DEFAULT_TARGET_BULLETS,
    snapshot_name: str = "",
    sandbox_name: str = "",
    register_snapshot: bool = True,
    cpu: int = 2,
    disk: int = 10,
    repo_dir: str = _REPO_DIR,
    test_command: str | None = None,
    test_timeout: int = _DEFAULT_SWEEVO_TEST_TIMEOUT,
    on_line: "Any" = None,
) -> dict[str, Any]:
    """Drive a team against a SWE-EVO instance and grade it.

    Provisions the sandbox, runs the builtin team (planner/developer/
    validator DAG) against it through :func:`run_sweevo_team`, then
    executes the instance's required test command as the grader.

    Returns a dict with ``instance``, ``sandbox``, ``team_status``,
    ``team_work_items`` (count), ``agent_patch`` (combined git diff),
    and ``test`` (required-test result).
    """
    from benchmarks.sweevo.team_runner import run_sweevo_team

    try:
        from sandbox.lifecycle import shutdown_cached_client

        instance = select_sweevo_instance(
            source=source,
            instance_id=instance_id,
            size=size,
            target_bullets=target_bullets,
        )

        sandbox_result = await create_sweevo_test_sandbox(
            instance,
            snapshot_name=snapshot_name,
            sandbox_name=sandbox_name,
            register_snapshot=register_snapshot,
            cpu=cpu,
            disk=disk,
            repo_dir=repo_dir,
        )
        sandbox_id = sandbox_result["sandbox_id"]

        try:
            team_status, team_work_items = await run_sweevo_team(
                instance,
                sandbox_id,
                repo_dir=repo_dir,
                printer=printer,
            )
        finally:
            try:
                printer.flush()
            except Exception:
                pass

        agent_patch = await _extract_combined_patch(sandbox_id, repo_dir)

        test_result = await run_sweevo_required_test(
            instance,
            sandbox_id,
            repo_dir=repo_dir,
            test_command=test_command,
            timeout=test_timeout,
            on_line=on_line,
        )

        return {
            "instance": summarize_sweevo_instance(instance),
            "snapshot_name": sandbox_result["snapshot_name"],
            "sandbox": sandbox_result["sandbox"],
            "repo_dir": repo_dir,
            "agent_patch": agent_patch,
            "team_status": (
                team_status.value if hasattr(team_status, "value") else team_status
            ),
            "team_work_items": team_work_items,
            # Legacy fields kept so existing CLI banners (``agent_events``)
            # still render without KeyErrors.
            "agent_name": "team",
            "agent_events": team_work_items,
            "test": test_result,
        }
    finally:
        try:
            shutdown_cached_client()
        except Exception:
            logger.debug("Failed to close cached AsyncDaytona client", exc_info=True)
