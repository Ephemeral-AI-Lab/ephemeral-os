"""SWE-EVO agent runner.

Runs an ephemeral agent against a SWE-EVO instance inside its Daytona
sandbox, streaming every ``StreamEvent`` (thinking, assistant text, tool
start/end, background dispatch, subagent spawn/return) through the shared
:class:`message.event_printer.MultiAgentEventPrinter`.
"""

from __future__ import annotations

import logging
from typing import Any

from benchmarks.sweevo.dataset import select_sweevo_instance, summarize_sweevo_instance
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


_DEFAULT_SWEEVO_PROMPT_TEMPLATE = """\
You are solving a SWE-EVO benchmark instance.

Repository: {repo}
Checked out in the sandbox at: {repo_dir}

Problem statement / changelog:
---
{problem_statement}
---

Your job: modify the code under {repo_dir} so the tests described in the
problem statement pass. Use the available bash/edit tools to explore the
repo, make changes, and iterate. The sandbox is already set up with the
correct Python environment (``conda activate testbed`` is required before
running Python commands). When you believe the fix is complete, stop and
return a short summary of the changes you made.
"""


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
    """Drive an agent against a SWE-EVO instance and stream its events.

    Returns a dict with ``instance``, ``sandbox``, ``agent_events`` (count),
    ``agent_name``, and ``test`` (required-test result).
    """
    # Lazy imports — pull the provider stack only when actually running.
    from engine.runtime.agent import spawn_agent
    from server.app_factory import build_session_config

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

    agent = spawn_agent(
        build_session_config(),
        messages=[],
        sandbox_id=sandbox_id,
    )

    prompt = _DEFAULT_SWEEVO_PROMPT_TEMPLATE.format(
        repo=instance.repo,
        repo_dir=repo_dir,
        problem_statement=instance.problem_statement,
    )

    event_count = 0
    try:
        async for event in agent.run(prompt):
            event_count += 1
            printer.emit(event)
    finally:
        printer.flush()

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
        "agent_name": agent.agent_name,
        "agent_events": event_count,
        "test": test_result,
    }
