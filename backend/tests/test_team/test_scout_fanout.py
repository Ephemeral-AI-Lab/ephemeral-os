"""Pattern B end-to-end: parallel scouts + chained planner replan.

Asserts the §8 Pattern B contract:

1. First planner emits N scout ``WorkItemSpec`` items (``kind=atomic``) plus
   a chained ``team_planner`` replanner item (``kind=expandable``) with
   ``deps`` pointing at every scout.
2. Scouts run concurrently, each producing a ``SubmittedSummary`` that
   wraps a brief artifact. Dispatcher's ``_promote_to_ready`` snapshots
   their artifact refs onto the replanner's ``dep_artifacts`` at the
   PENDING→READY transition.
3. The replanner (a fresh ``team_planner`` run) sees all three briefs in
   its rendered prompt preamble via ``render_briefings`` — specifically
   under the ``## From deps`` section with each scout's display name.
4. The replanner emits concrete worker WorkItems that reference the brief
   content; those workers then run and complete.

This covers the correctness-critical path that planners cannot write
worker payloads in the same plan as the scouts they depend on — the
replanner must run in its own turn after seeing the briefs.
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
# Harness
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


def _make_runner(scripts: dict[str, Any]):
    async def runner(defn, ctx):
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


def _make_executor_factory(runner, captured_preambles: dict[str, str]):
    def build_query_ctx(defn, team_run, wi):
        preamble = render_briefings(
            wi,
            artifact_store=team_run.artifacts,
            project_context=team_run.project_context,
            budgets=team_run.budgets,
        )
        captured_preambles[f"{defn.name}:{wi.id}"] = preamble
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
async def test_pattern_b_chained_planner_sees_all_scout_briefs_in_deps():
    """Pattern B e2e: scouts run in parallel, replanner sees briefs.

    Graph shape:
        first_planner (root, EXPANDABLE)
          └─ submits plan: [s_core, s_runtime, s_testing, replan]
                            ├─ s_core    (scout, ATOMIC)     ─┐
                            ├─ s_runtime (scout, ATOMIC)      ├─ parallel
                            ├─ s_testing (scout, ATOMIC)     ─┘
                            └─ replan    (team_planner, EXPANDABLE, deps=[s_*])
                                └─ submits plan: [impl1, impl2]
                                                  ├─ impl1 (file_editor, ATOMIC)
                                                  └─ impl2 (file_editor, ATOMIC)

    Asserts:
      - All three scouts complete and have artifacts.
      - replan becomes READY only after every scout is DONE, and its
        ``dep_artifacts`` is populated with one entry per scout.
      - replan's rendered prompt preamble contains the "## From deps"
        section and includes each scout's brief body.
      - impl1/impl2 complete successfully, producing the final DONE state.
    """
    first_planner_posthook = PosthookConfig(
        agent_name="submit_plan_agent", metadata_key="submitted_plan"
    )
    replan_posthook = PosthookConfig(
        agent_name="submit_plan_agent", metadata_key="submitted_plan"
    )

    _register("submit_plan_agent")
    _register("first_planner", posthook=first_planner_posthook)
    _register("replan_planner", posthook=replan_posthook)
    _register("scout")
    _register("file_editor")

    try:
        fanout_plan = Plan.from_dict(
            {
                "items": [
                    {
                        "agent_name": "scout",
                        "local_id": "s_core",
                        "kind": "atomic",
                        "payload": {"target_paths": ["backend/src/engine/core/"]},
                    },
                    {
                        "agent_name": "scout",
                        "local_id": "s_runtime",
                        "kind": "atomic",
                        "payload": {"target_paths": ["backend/src/engine/runtime/"]},
                    },
                    {
                        "agent_name": "scout",
                        "local_id": "s_testing",
                        "kind": "atomic",
                        "payload": {"target_paths": ["backend/src/engine/testing/"]},
                    },
                    {
                        "agent_name": "replan_planner",
                        "local_id": "replan",
                        "kind": "expandable",
                        "deps": ["s_core", "s_runtime", "s_testing"],
                    },
                ]
            }
        )

        worker_plan = Plan.from_dict(
            {
                "items": [
                    {
                        "agent_name": "file_editor",
                        "local_id": "impl1",
                        "kind": "atomic",
                        "payload": {"edit": "backend/src/engine/core/loop.py"},
                    },
                    {
                        "agent_name": "file_editor",
                        "local_id": "impl2",
                        "kind": "atomic",
                        "payload": {"edit": "backend/src/engine/runtime/dispatcher.py"},
                    },
                ]
            }
        )

        serializer_state = {"first_done": False}

        def submit_plan_phase(ctx):
            # The same serializer agent handles both the first planner and
            # the replanner; pick the right plan based on which fires first.
            if not serializer_state["first_done"]:
                ctx.tool_metadata["submitted_plan"] = fanout_plan
                serializer_state["first_done"] = True
            else:
                ctx.tool_metadata["submitted_plan"] = worker_plan

        scripts = {
            "first_planner": {
                "artifact": {"strategy": "fanout"},
                "summary": "fanning out to three scouts",
            },
            "replan_planner": {
                "artifact": {"strategy": "implement"},
                "summary": "writing concrete worker plan from brief set",
            },
            "submit_plan_agent": submit_plan_phase,
            "scout": {
                "artifact": {
                    "summary": "mini brief",
                    "target_paths": ["backend/src/engine/__placeholder__"],
                    "canonical_scope": "backend/src/engine/__placeholder__",
                    "files": [],
                    "entry_points": [],
                    "open_questions": [],
                    "scope_coverage": 1.0,
                    "gaps": "",
                    "suggested_subdivisions": [],
                },
                "summary": "scoped scout brief",
            },
            "file_editor": {"artifact": {"edited": True}, "summary": "file edited"},
        }

        captured_preambles: dict[str, str] = {}
        tr = TeamRun(session_id="S_pattern_b", user_request="refactor engine module")
        await tr.start(
            "first_planner",
            payload={"goal": "understand and refactor backend/src/engine"},
            executor_factory=_make_executor_factory(
                _make_runner(scripts), captured_preambles
            ),
            num_executors=3,
            root_kind=WorkItemKind.EXPANDABLE,
        )
        status = await tr.wait()

        assert status == TeamRunStatus.SUCCEEDED, (
            f"expected SUCCEEDED, got {status}; graph={[ (wi.agent_name, wi.status.value, wi.failure_reason) for wi in tr.dispatcher.graph.values()]}"
        )

        # Structural assertions: 1 root + 3 scouts + 1 replan + 2 workers = 7.
        assert len(tr.dispatcher.graph) == 7
        done = [
            wi
            for wi in tr.dispatcher.graph.values()
            if wi.status == WorkItemStatus.DONE
        ]
        assert len(done) == 7

        # Find the replanner work item by local_id.
        replan_items = [
            wi for wi in tr.dispatcher.graph.values() if wi.local_id == "replan"
        ]
        assert len(replan_items) == 1
        replan_wi = replan_items[0]

        # The replanner's dep_artifacts must hold one entry per scout, each
        # pointing at a stored artifact_ref. This is the snapshot-at-READY
        # invariant from `Dispatcher._promote_to_ready`.
        assert len(replan_wi.dep_artifacts) == 3
        dep_names = sorted(
            da.display_name or da.source_wi_id for da in replan_wi.dep_artifacts
        )
        assert dep_names == ["s_core", "s_runtime", "s_testing"]
        # Every dep_artifact must reference a non-None artifact_ref.
        assert all(da.artifact_ref for da in replan_wi.dep_artifacts)

        # The replanner's captured rendered preamble must contain the deps
        # section and reference every scout's brief (via their canonical
        # scope or display name). This is the core Pattern B invariant: the
        # replanner sees the briefs *before* writing the concrete plan.
        matching_keys = [
            k for k in captured_preambles if k.startswith("replan_planner:")
        ]
        assert len(matching_keys) == 1
        replan_preamble = captured_preambles[matching_keys[0]]
        assert "## From deps" in replan_preamble, (
            f"replanner should see a ## From deps section in its preamble; got:\n{replan_preamble!r}"
        )
        # All three scouts' briefs must be rendered somewhere in the preamble.
        # Exact format depends on dedupe (all three have the same canonical_scope
        # in the scripted test, so only one may survive dedup). Assert at least
        # one scout brief body is rendered.
        assert (
            "mini brief" in replan_preamble
            or "scoped scout brief" in replan_preamble
        ), (
            f"replanner preamble should contain scout brief content; got:\n{replan_preamble!r}"
        )

        # The first planner's preamble, by contrast, should be empty (it has
        # no deps, no explicit briefings, and shared_briefings starts empty).
        first_planner_keys = [
            k for k in captured_preambles if k.startswith("first_planner:")
        ]
        assert len(first_planner_keys) == 1
        assert captured_preambles[first_planner_keys[0]] == ""
    finally:
        _cleanup(
            "first_planner",
            "replan_planner",
            "submit_plan_agent",
            "scout",
            "file_editor",
        )


@pytest.mark.asyncio
async def test_pattern_b_replan_sees_distinct_scope_briefs():
    """Variant: three scouts with DIFFERENT canonical scopes coexist in deps.

    The primary test above has all three scouts carry the same placeholder
    scope (because the scripted runner returns a fixed artifact shape).
    This test gives each scout a distinct ``canonical_scope`` so we can
    assert that render_briefings produces three separate deps entries
    without collapsing them under the scope-dedup rule.
    """
    first_planner_posthook = PosthookConfig(
        agent_name="submit_plan_agent", metadata_key="submitted_plan"
    )
    replan_posthook = PosthookConfig(
        agent_name="submit_plan_agent", metadata_key="submitted_plan"
    )

    _register("submit_plan_agent")
    _register("first_planner_v2", posthook=first_planner_posthook)
    _register("replan_planner_v2", posthook=replan_posthook)
    _register("scout_core")
    _register("scout_runtime")
    _register("scout_testing")
    _register("file_editor_v2")

    try:
        fanout_plan = Plan.from_dict(
            {
                "items": [
                    {
                        "agent_name": "scout_core",
                        "local_id": "s_core",
                        "kind": "atomic",
                        "payload": {"target_paths": ["backend/src/engine/core/"]},
                    },
                    {
                        "agent_name": "scout_runtime",
                        "local_id": "s_runtime",
                        "kind": "atomic",
                        "payload": {"target_paths": ["backend/src/engine/runtime/"]},
                    },
                    {
                        "agent_name": "scout_testing",
                        "local_id": "s_testing",
                        "kind": "atomic",
                        "payload": {"target_paths": ["backend/src/engine/testing/"]},
                    },
                    {
                        "agent_name": "replan_planner_v2",
                        "local_id": "replan",
                        "kind": "expandable",
                        "deps": ["s_core", "s_runtime", "s_testing"],
                    },
                ]
            }
        )

        worker_plan = Plan.from_dict(
            {
                "items": [
                    {
                        "agent_name": "file_editor_v2",
                        "local_id": "impl1",
                        "kind": "atomic",
                        "payload": {"task": "ok"},
                    },
                ]
            }
        )

        state = {"first_done": False}

        def submit_plan_phase(ctx):
            if not state["first_done"]:
                ctx.tool_metadata["submitted_plan"] = fanout_plan
                state["first_done"] = True
            else:
                ctx.tool_metadata["submitted_plan"] = worker_plan

        scripts = {
            "first_planner_v2": {"artifact": {}, "summary": "fanout"},
            "replan_planner_v2": {"artifact": {}, "summary": "replan"},
            "submit_plan_agent": submit_plan_phase,
            "scout_core": {
                "artifact": {
                    "summary": "core brief narrative",
                    "target_paths": ["backend/src/engine/core/"],
                    "canonical_scope": "backend/src/engine/core",
                    "scope_coverage": 1.0,
                },
                "summary": "scout_core done",
            },
            "scout_runtime": {
                "artifact": {
                    "summary": "runtime brief narrative",
                    "target_paths": ["backend/src/engine/runtime/"],
                    "canonical_scope": "backend/src/engine/runtime",
                    "scope_coverage": 1.0,
                },
                "summary": "scout_runtime done",
            },
            "scout_testing": {
                "artifact": {
                    "summary": "testing brief narrative",
                    "target_paths": ["backend/src/engine/testing/"],
                    "canonical_scope": "backend/src/engine/testing",
                    "scope_coverage": 1.0,
                },
                "summary": "scout_testing done",
            },
            "file_editor_v2": {"artifact": {}, "summary": "edited"},
        }

        captured_preambles: dict[str, str] = {}
        tr = TeamRun(session_id="S_pattern_b_v2", user_request="refactor")
        await tr.start(
            "first_planner_v2",
            payload={},
            executor_factory=_make_executor_factory(
                _make_runner(scripts), captured_preambles
            ),
            num_executors=3,
            root_kind=WorkItemKind.EXPANDABLE,
        )
        status = await tr.wait()
        assert status == TeamRunStatus.SUCCEEDED

        replan_key = next(
            k for k in captured_preambles if k.startswith("replan_planner_v2:")
        )
        preamble = captured_preambles[replan_key]
        # All three distinct scope briefs must be rendered in the replan preamble.
        assert "core brief narrative" in preamble
        assert "runtime brief narrative" in preamble
        assert "testing brief narrative" in preamble
        # Each deps entry should carry its canonical scope in brackets.
        assert "backend/src/engine/core" in preamble
        assert "backend/src/engine/runtime" in preamble
        assert "backend/src/engine/testing" in preamble
    finally:
        _cleanup(
            "first_planner_v2",
            "replan_planner_v2",
            "submit_plan_agent",
            "scout_core",
            "scout_runtime",
            "scout_testing",
            "file_editor_v2",
        )
