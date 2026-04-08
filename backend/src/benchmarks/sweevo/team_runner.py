"""Wire a real team run over a provisioned SWE-EVO sandbox.

Drives :class:`team.runtime.team_run.TeamRun` with the builtin
``team_planner`` / ``developer`` / ``validator`` agents from
``team.builtins``. Each WorkItem's agent is spawned through
:func:`engine.runtime.agent.spawn_agent` so it runs with its full
production tool surface (``sandbox_operations``, ``code_intelligence``,
skills, posthook tools) against the Daytona sandbox that was already
prepared by :func:`benchmarks.sweevo.sandbox.create_sweevo_test_sandbox`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.registry import get_definition
from engine.runtime.agent import spawn_agent
from message.event_printer import MultiAgentEventPrinter
from team.builtins import TEAM_PLANNER, register_all as _register_team_builtins
from team.models import BudgetConfig, TeamRunStatus, WorkItemKind
from team.runtime.context_builder import (
    TeamAgentContext,
    build_initial_user_message,
    build_work_item_metadata,
)
from team.runtime.executor import Executor
from team.runtime.team_run import TeamRun

from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR

logger = logging.getLogger(__name__)


import sys as _sys

# No budget tracking — the sweevo benchmark runs the team until it
# finishes, never until a counter trips. All caps are set to their
# maximum possible values so the dispatcher's budget checks are no-ops.
_UNLIMITED_BUDGETS = BudgetConfig(
    max_work_items=_sys.maxsize,
    max_depth=_sys.maxsize,
    max_plan_size=_sys.maxsize,
    max_artifact_bytes=_sys.maxsize,
    max_total_artifact_bytes=_sys.maxsize,
    default_work_item_timeout=10**9,
    max_briefing_bytes=_sys.maxsize,
    max_shared_briefings=_sys.maxsize,
)

# Default pool size for the team's Executor workers. Not a cap — callers
# can still override.
_DEFAULT_NUM_EXECUTORS = 8


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_root_prompt(instance: SWEEvoInstance, repo_dir: str) -> str:
    return (
        f"You are leading a coding team on a SWE-EVO benchmark instance.\n"
        f"Repository: {instance.repo}\n"
        f"Working directory inside the sandbox: {repo_dir}\n"
        f"Base commit (already checked out): {instance.base_commit}\n\n"
        f"## Task (release changelog / problem statement)\n"
        f"{instance.problem_statement}\n\n"
        f"## Grading command\n"
        f"After your team finishes, this exact command will be executed in the sandbox "
        f"to grade the work:\n```\n{instance.test_cmds}\n```\n\n"
        f"## Instructions\n"
        f"- Decompose the work into concrete developer and validator WorkItems.\n"
        f"- Developers edit the repo in the sandbox via sandbox_operations tools.\n"
        f"- Stay inside {repo_dir}.\n"
        f"- Do NOT modify test files unless the task explicitly asks for it.\n"
        f"- Validators should run the grading command (or a tighter subset) and "
        f"report PASS/FAIL with evidence."
    )


def _work_item_base_prompt(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("prompt", "task", "description", "instructions"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val
        return "Execute the following WorkItem payload:\n" + json.dumps(
            payload, indent=2, default=str
        )
    if isinstance(payload, str):
        return payload
    return f"Payload: {payload!r}"


# ---------------------------------------------------------------------------
# Runner + executor factory
# ---------------------------------------------------------------------------


def _make_runner(
    session_config: Any,
    sandbox_id: str,
    printer: MultiAgentEventPrinter | None,
):
    async def _run(defn, ctx: TeamAgentContext):
        prompt = ctx.user_message or _work_item_base_prompt(None)

        agent = spawn_agent(
            session_config,
            messages=[],
            agent_def=defn,
            latest_user_prompt=prompt,
            sandbox_id=sandbox_id,
        )

        # Redirect the spawned agent's tool_metadata to the team ctx so
        # submit_plan / submit_summary tools write into the slot that
        # execute_with_posthook reads back. Preserve session_config and
        # sandbox_id that spawn_agent installed for subagent dispatch.
        spawned_meta = agent.query_context.tool_metadata
        if getattr(spawned_meta, "session_config", None) is not None:
            ctx.tool_metadata.session_config = spawned_meta.session_config
        sb = getattr(spawned_meta, "sandbox_id", None) or ""
        if sb:
            ctx.tool_metadata["sandbox_id"] = sb
        agent.query_context.tool_metadata = ctx.tool_metadata

        try:
            async for event in agent.run(prompt):
                if printer is None:
                    continue
                try:
                    object.__setattr__(event, "agent_name", defn.name)
                except Exception:
                    pass
                try:
                    printer.emit(event)
                except Exception:
                    logger.debug("printer.emit failed", exc_info=True)
        except Exception:
            logger.exception("sweevo team runner: agent %s crashed", defn.name)
            raise

        return {"agent": defn.name}

    return _run


def _make_executor_factory(
    session_config: Any,
    sandbox_id: str,
    printer: MultiAgentEventPrinter | None,
):
    runner = _make_runner(session_config, sandbox_id, printer)

    def build_query_ctx(defn, team_run, wi):
        base_prompt = _work_item_base_prompt(wi.payload)
        user_message = build_initial_user_message(team_run, wi, base_prompt)
        meta = build_work_item_metadata(team_run, wi)
        meta["sandbox_id"] = team_run.sandbox_id or sandbox_id
        return TeamAgentContext(user_message=user_message, tool_metadata=meta)

    def build_posthook_ctx(posthook_defn, work_result):
        return TeamAgentContext(
            tool_metadata={
                "agent_name": posthook_defn.name,
                "sandbox_id": sandbox_id,
            },
            work_result=work_result,
        )

    def factory(team_run):
        return Executor(
            team_run=team_run,
            runner=runner,
            build_query_context=build_query_ctx,
            build_posthook_context=build_posthook_ctx,
            agent_lookup=get_definition,
        )

    return factory


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_sweevo_team(
    instance: SWEEvoInstance,
    sandbox_id: str,
    *,
    repo_dir: str = _REPO_DIR,
    printer: MultiAgentEventPrinter | None = None,
    num_executors: int = _DEFAULT_NUM_EXECUTORS,
    work_item_timeout: float | None = None,  # noqa: ARG001 — kept for CLI compat; no-op
) -> tuple[TeamRunStatus, int]:
    """Run the builtin planner/developer/validator team against the sandbox.

    Returns ``(TeamRunStatus, work_items_executed)``. Does not raise on
    team failure — the caller grades the result via the sweevo test
    command.
    """
    from server.app_factory import build_session_config

    try:
        _register_team_builtins()
    except Exception:
        logger.debug("team builtins already registered", exc_info=True)

    session_config = build_session_config()
    session_config.cwd = repo_dir
    root_prompt = _build_root_prompt(instance, repo_dir)

    tr = TeamRun(
        session_id=getattr(session_config, "session_id", "sweevo"),
        user_request=root_prompt,
        budgets=_UNLIMITED_BUDGETS,
        sandbox_id=sandbox_id,
        repo_root=repo_dir,
    )

    await tr.start(
        agent_name=TEAM_PLANNER,
        payload={
            "prompt": root_prompt,
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "repo_dir": repo_dir,
            "test_cmds": instance.test_cmds,
            "fail_to_pass": instance.fail_to_pass,
            "pass_to_pass": instance.pass_to_pass,
        },
        executor_factory=_make_executor_factory(session_config, sandbox_id, printer),
        num_executors=num_executors,
        root_kind=WorkItemKind.EXPANDABLE,
    )

    status = await tr.wait()
    work_items = len(tr.dispatcher.graph)
    logger.info(
        "sweevo team run %s finished: status=%s work_items=%d",
        tr.id,
        getattr(status, "value", status),
        work_items,
    )
    return status, work_items
