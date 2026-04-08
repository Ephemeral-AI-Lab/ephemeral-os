"""Greenfield / empty-workspace team run.

Asserts the Phase 1 §13 "empty-area rule" and §9 "Pattern 0" invariants:

- A team run against an empty workspace completes successfully without
  ever spawning a scout subagent and without ever populating
  ``ProjectContext.shared_briefings``.
- Workers created from scratch see no "Briefing from parent" preamble
  because all three briefing tiers are empty (Step 2b empty-tier guard).

The scripted runner lets us observe exactly which agents executed: any
scout script invocation would register itself on ``executed_agents`` and
cause the test to fail. This is the e2e counterpart to the unit tests in
``test_briefings.py`` that assert ``render_briefings`` returns an empty
string when nothing is briefed.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.registry import register_definition, unregister_definition
from agents.types import AgentDefinition
from hooks.agent_posthook import PosthookConfig
from team.context.briefings import render_briefings
from team.models import Plan, TeamRunStatus, WorkItemKind, WorkItemStatus
from team.runtime.context_builder import TeamAgentContext
from team.runtime.executor import Executor
from team.runtime.team_run import TeamRun
from tools.posthook import SubmittedSummary

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Test harness: track executed agents so the test can assert "no scout ran"
# ---------------------------------------------------------------------------


def _register(name: str, posthook: PosthookConfig | None = None) -> None:
    if posthook is None:
        autopost = f"{name}__autopost"
        register_definition(
            AgentDefinition(
                name=autopost,
                description=f"autopost for {name}",
                system_prompt="p",
                toolkits=[],
                skills=[],
                include_skills=False,
                source="builtin",
            )
        )
        posthook = PosthookConfig(
            agent_name=autopost, metadata_key="submitted_summary"
        )
    register_definition(
        AgentDefinition(
            name=name,
            description=f"scripted {name}",
            system_prompt="p",
            toolkits=[],
            skills=[],
            include_skills=False,
            posthook=posthook,
            source="builtin",
        )
    )


def _cleanup(*names: str) -> None:
    for n in names:
        try:
            unregister_definition(n)
        except Exception:
            pass
        try:
            unregister_definition(f"{n}__autopost")
        except Exception:
            pass


def _make_runner(
    scripts: dict[str, Any], executed_agents: list[str]
):
    async def runner(defn, ctx):
        executed_agents.append(defn.name)
        if defn.name.endswith("__autopost"):
            owner = defn.name[: -len("__autopost")]
            owner_script = scripts.get(owner) or {}
            ctx.tool_metadata["submitted_summary"] = SubmittedSummary(
                summary=owner_script.get("summary", ""),
                artifact=owner_script.get("artifact"),
            )
            return {"phase": defn.name}
        script = scripts.get(defn.name)
        if callable(script):
            script(ctx)
            return {"phase": defn.name}
        if isinstance(script, dict):
            return {
                "artifact": script.get("artifact", {}),
                "summary": script.get("summary", ""),
            }
        return {"artifact": {}, "summary": ""}

    return runner


def _make_executor_factory(runner, captured_prompts: dict[str, str]):
    def build_query_ctx(defn, team_run, wi):
        preamble = render_briefings(
            wi,
            artifact_store=team_run.artifacts,
            project_context=team_run.project_context,
            budgets=team_run.budgets,
        )
        captured_prompts[f"{defn.name}:{wi.id}"] = preamble
        return TeamAgentContext(
            tool_metadata={
                "team_run_id": team_run.id,
                "work_item_id": wi.id,
                "agent_name": defn.name,
                "preamble": preamble,
            }
        )

    def build_posthook_ctx(posthook_defn, work_result):
        return TeamAgentContext(
            tool_metadata={
                "agent_name": posthook_defn.name,
                "work_result": work_result,
            }
        )

    def factory(team_run):
        from agents.registry import get_definition

        return Executor(
            team_run=team_run,
            runner=runner,
            build_query_context=build_query_ctx,
            build_posthook_context=build_posthook_ctx,
            agent_lookup=get_definition,
        )

    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_greenfield_run_spawns_no_scouts_and_shared_context_stays_empty():
    """Planner on empty workspace directly emits worker WorkItems.

    Asserts:
      - No scout / subagent is ever executed.
      - ``ProjectContext.shared_briefings`` is empty at the start and end.
      - Each executor's rendered preamble is the empty string (no
        ``## Briefing from parent`` header) because all three tiers are
        empty.
      - The run reaches ``SUCCEEDED``.
    """
    planner_posthook = PosthookConfig(
        agent_name="submit_plan_agent", metadata_key="submitted_plan"
    )
    _register("submit_plan_agent")
    _register("greenfield_planner", posthook=planner_posthook)
    _register("file_creator")
    # Register a scout-shaped agent and confirm it is NEVER invoked.
    _register("scout")

    try:
        # Greenfield plan: create two files from scratch, no scouts.
        worker_plan = Plan.from_dict(
            {
                "items": [
                    {
                        "agent_name": "file_creator",
                        "local_id": "f1",
                        "payload": {"create": "src/main.py"},
                    },
                    {
                        "agent_name": "file_creator",
                        "local_id": "f2",
                        "payload": {"create": "src/config.py"},
                    },
                ]
            }
        )

        def planner_posthook_phase(ctx):
            ctx.tool_metadata["submitted_plan"] = worker_plan

        scripts = {
            "greenfield_planner": {
                "artifact": {"strategy": "greenfield"},
                "summary": "no scouting required; workspace is empty",
            },
            "submit_plan_agent": planner_posthook_phase,
            "file_creator": {"artifact": {"created": True}, "summary": "file created"},
            # Scout script is present but must never fire.
            "scout": {"artifact": {"should": "not be called"}, "summary": "unreachable"},
        }

        executed_agents: list[str] = []
        captured_prompts: dict[str, str] = {}

        tr = TeamRun(
            session_id="S_greenfield",
            user_request="create a minimal Python project from scratch",
        )

        # Sanity check: shared_briefings starts empty.
        assert tr.project_context.shared_briefings == {}

        await tr.start(
            "greenfield_planner",
            payload={"goal": "greenfield"},
            executor_factory=_make_executor_factory(
                _make_runner(scripts, executed_agents), captured_prompts
            ),
            num_executors=2,
            root_kind=WorkItemKind.EXPANDABLE,
        )
        status = await tr.wait()

        assert status == TeamRunStatus.SUCCEEDED

        # 1 planner + 2 file_creator WorkItems = 3 items total.
        assert len(tr.dispatcher.graph) == 3
        done = [
            wi
            for wi in tr.dispatcher.graph.values()
            if wi.status == WorkItemStatus.DONE
        ]
        assert len(done) == 3

        # No scout WorkItem was ever scheduled.
        scheduled_agents = [wi.agent_name for wi in tr.dispatcher.graph.values()]
        assert "scout" not in scheduled_agents, (
            f"scout must not run on a greenfield team run; scheduled={scheduled_agents}"
        )
        # The scout agent's runner script was never invoked.
        assert "scout" not in executed_agents, (
            f"scout runner must never fire on greenfield; executed={executed_agents}"
        )

        # shared_briefings stayed empty through the whole run.
        assert tr.project_context.shared_briefings == {}

        # Every captured executor preamble is empty — no "Briefing from parent"
        # header because all three briefing tiers are empty.
        for key, preamble in captured_prompts.items():
            assert preamble == "", (
                f"executor {key} should have an empty briefing preamble on a "
                f"greenfield run; got: {preamble!r}"
            )
    finally:
        _cleanup(
            "greenfield_planner",
            "submit_plan_agent",
            "file_creator",
            "scout",
        )


@pytest.mark.asyncio
async def test_greenfield_run_preamble_is_empty_string_exactly():
    """render_briefings returns exactly ``""`` on an empty-tier executor run.

    Unit-level backstop for the e2e assertion above: even if a future
    harness change accidentally emits a bare header, this test catches
    the regression directly against ``render_briefings`` itself.
    """
    from team.context.briefings import render_briefings
    from team.context.project import ProjectContext
    from team.models import BudgetConfig, WorkItem, WorkItemStatus

    class _FakeStore:
        def load(self, ref):  # noqa: ARG002
            return None

    wi = WorkItem(
        id="wi_empty",
        team_run_id="tr",
        agent_name="greenfield_worker",
        status=WorkItemStatus.RUNNING,
    )
    result = render_briefings(
        wi,
        artifact_store=_FakeStore(),
        project_context=ProjectContext(goal="g", user_request="u"),
        budgets=BudgetConfig(),
    )
    assert result == ""
