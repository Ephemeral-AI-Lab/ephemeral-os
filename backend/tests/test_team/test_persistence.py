"""Tests for team.persistence.

Three concerns:

1. ``JsonlTeamRunStore`` round-trips events and assigns monotonic seqs.
2. Sequence numbering is recovered when the store is reopened (crash
   simulation — new process, same directory).
3. A live Dispatcher wired to a JsonlTeamRunStore emits the expected
   event stream for a small two-node plan, and ``TeamRun.resume_from``
   rehydrates the graph from that stream.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.registry import get_definition, register_definition, unregister_definition
from agents.types import AgentDefinition
from hooks.agent_posthook import PosthookConfig
from team.artifacts.store import InMemoryArtifactStore
from team.models import (
    AgentResult,
    BudgetConfig,
    BudgetState,
    TeamRunStatus,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)
from team.persistence.events import (
    make_artifact_written,
    make_budget_update,
    make_team_run_created,
    make_team_run_status,
    make_work_item_added,
    make_work_item_status,
    work_item_to_dict,
)
from team.persistence.run_store import (
    JsonlTeamRunStore,
    NullTeamRunStore,
    build_default_store,
    replay,
)
from team.runtime.context_builder import TeamAgentContext
from team.runtime.dispatcher import Dispatcher
from team.runtime.executor import Executor
from team.runtime.team_run import TeamRun, build_team_runtime_services
from tools.posthook import SubmittedSummary


# ---------- JsonlTeamRunStore round-trip ----------------------------------


def test_jsonl_round_trip_assigns_monotonic_seqs(tmp_path: Path) -> None:
    store = JsonlTeamRunStore(tmp_path)
    rid = "run-1"

    store.append(
        make_team_run_created(
            rid,
            session_id="s1",
            user_request="do",
            goal=None,
            repo_root="/x",
            budgets={"max_work_items": 5},
        )
    )
    store.append(make_team_run_status(rid, "running"))
    store.append(make_work_item_added(rid, {"id": "w1", "status": "pending"}))
    store.append(make_work_item_status(rid, "w1", "done"))

    events = store.load_run(rid)
    assert [e.seq for e in events] == [1, 2, 3, 4]
    assert [e.kind for e in events] == [
        "team_run_created",
        "team_run_status",
        "work_item_added",
        "work_item_status",
    ]
    assert store.list_runs() == [rid]


def test_jsonl_replay_folds_events_into_view(tmp_path: Path) -> None:
    store = JsonlTeamRunStore(tmp_path)
    rid = "run-view"
    store.append(
        make_team_run_created(
            rid,
            session_id="s",
            user_request="go",
            goal=None,
            repo_root=None,
            budgets={},
        )
    )
    store.append(make_work_item_added(rid, {"id": "a", "status": "pending"}))
    store.append(make_work_item_status(rid, "a", "running", agent_run_id="ar1"))
    store.append(make_artifact_written(rid, wi_id="a", ref="a", size=11, payload={"k": 1}))
    store.append(make_work_item_status(rid, "a", "done"))
    store.append(make_budget_update(rid, work_items_used=1, artifact_bytes_used=11))
    store.append(make_team_run_status(rid, "succeeded"))

    view = replay(store.load_run(rid))
    assert view["team_run_id"] == rid
    assert view["status"] == "succeeded"
    assert view["work_items"]["a"]["status"] == "done"
    assert view["work_items"]["a"]["agent_run_id"] == "ar1"
    assert view["artifacts"]["a"]["size"] == 11
    assert view["budget"] == {"work_items_used": 1, "artifact_bytes_used": 11}


# ---------- Sequence recovery across reopen -------------------------------


def test_jsonl_seq_recovers_after_reopen(tmp_path: Path) -> None:
    rid = "run-reopen"
    first = JsonlTeamRunStore(tmp_path)
    for _ in range(3):
        first.append(make_team_run_status(rid, "running"))

    # New store instance — simulates a fresh process after crash.
    second = JsonlTeamRunStore(tmp_path)
    second.append(make_team_run_status(rid, "succeeded"))

    events = second.load_run(rid)
    assert [e.seq for e in events] == [1, 2, 3, 4]
    assert events[-1].data["status"] == "succeeded"


def test_null_store_is_silent() -> None:
    n = NullTeamRunStore()
    n.append(make_team_run_status("x", "y"))
    assert n.load_run("x") == []
    assert n.list_runs() == []


def test_build_default_store_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EPHEMERALOS_TEAM_RUN_DIR", str(tmp_path))
    store = build_default_store()
    assert isinstance(store, JsonlTeamRunStore)
    monkeypatch.delenv("EPHEMERALOS_TEAM_RUN_DIR")
    assert isinstance(build_default_store(), NullTeamRunStore)


# ---------- End-to-end Dispatcher event stream ----------------------------


def _make_dispatcher_with_store(store: JsonlTeamRunStore) -> Dispatcher:
    budgets = BudgetConfig()
    state = BudgetState()
    art = InMemoryArtifactStore(budgets, state)
    return Dispatcher(
        team_run_id="T-persist",
        budgets=budgets,
        budget_state=state,
        artifact_store=art,
        event_store=store,
    )


def _wi(id_: str, deps: list[str] | None = None) -> WorkItem:
    return WorkItem(
        id=id_,
        team_run_id="T-persist",
        agent_name="a",
        status=WorkItemStatus.PENDING,
        kind=WorkItemKind.ATOMIC,
        deps=deps or [],
        root_id=id_,
    )


@pytest.fixture(autouse=True)
def _patch_agent_exists(monkeypatch) -> None:
    from team.planning import validation

    monkeypatch.setattr(validation, "_agent_exists", lambda name: True)


@pytest.mark.asyncio
async def test_dispatcher_emits_expected_events_for_small_plan(tmp_path: Path) -> None:
    store = JsonlTeamRunStore(tmp_path)
    disp = _make_dispatcher_with_store(store)

    await disp.add_work_item(_wi("A"))
    await disp.add_work_item(_wi("B", deps=["A"]))

    # Execute A, which should promote B to READY.
    await disp.pop_ready()
    await disp.mark_running("A", "AR1")
    await disp.complete("A", AgentResult(artifact={"out": 1}, summary="ok"))

    await disp.pop_ready()
    await disp.mark_running("B", "AR2")
    await disp.complete("B", AgentResult(artifact={"out": 2}, summary="ok"))

    events = store.load_run("T-persist")
    kinds = [e.kind for e in events]

    # Every durable transition must be recorded, in order.
    assert "work_item_added" in kinds
    assert kinds.count("work_item_added") == 2
    assert kinds.count("artifact_written") == 2
    assert kinds.count("budget_update") >= 2

    # Statuses for A: ready -> running -> done
    a_statuses = [
        e.data["status"]
        for e in events
        if e.kind == "work_item_status" and e.data.get("wi_id") == "A"
    ]
    assert a_statuses == ["ready", "running", "done"]

    # Statuses for B: ready (auto-promoted after A done) -> running -> done
    b_statuses = [
        e.data["status"]
        for e in events
        if e.kind == "work_item_status" and e.data.get("wi_id") == "B"
    ]
    assert b_statuses == ["ready", "running", "done"]

    # Sequence numbers are strictly increasing.
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


# ---------- TeamRun.resume_from rehydration -------------------------------


@pytest.mark.asyncio
async def test_team_run_resume_from_rehydrates_graph(tmp_path: Path) -> None:
    store = JsonlTeamRunStore(tmp_path)

    # Build a fresh TeamRun wired to the store, dispatch two items, and
    # run them to DONE — emitting a full event log to disk.
    fixed_id = "tr-fixed-001"
    services = build_team_runtime_services(
        team_run_id=fixed_id,
        budgets=BudgetConfig(),
        budget_state=BudgetState(),
        user_request="do things",
        event_store=store,
    )
    run = TeamRun(
        session_id="sess-1",
        user_request="do things",
        services=services,
    )
    # Align TeamRun.id with the dispatcher's persisted id so the event
    # log uses a single team_run_id throughout.
    run.id = fixed_id

    # Manually emit a team_run_created header (normally done inside
    # TeamRun.start — but start() needs an executor factory we don't
    # want to wire up for this test).
    store.append(
        make_team_run_created(
            run.id,
            session_id=run.session_id,
            user_request=run.user_request,
            goal=None,
            repo_root=run.project_context.repo_root,
            budgets={
                "max_work_items": run.budgets.max_work_items,
                "max_depth": run.budgets.max_depth,
            },
        )
    )

    await run.dispatcher.add_work_item(_wi_for_run(run.id, "A"))
    await run.dispatcher.add_work_item(_wi_for_run(run.id, "B", deps=["A"]))
    await run.dispatcher.pop_ready()
    await run.dispatcher.mark_running("A", "AR1")
    await run.dispatcher.complete("A", AgentResult(artifact={"x": 1}, summary="ok"))
    await run.dispatcher.pop_ready()
    await run.dispatcher.mark_running("B", "AR2")
    await run.dispatcher.complete("B", AgentResult(artifact={"x": 2}, summary="ok"))
    store.append(make_team_run_status(run.id, "succeeded"))

    persisted_id = run.id

    # Now resume into a fresh runtime.
    revived = TeamRun.resume_from(store, persisted_id)
    assert revived.id == persisted_id
    assert revived.session_id == "sess-1"
    assert revived.user_request == "do things"
    assert set(revived.dispatcher.graph.keys()) == {"A", "B"}
    assert revived.dispatcher.graph["A"].status == WorkItemStatus.DONE
    assert revived.dispatcher.graph["B"].status == WorkItemStatus.DONE
    assert revived.budget_state.work_items_used == 2
    # Artifact bytes may differ slightly due to re-serialization, but
    # both artifacts must be present in the rehydrated store.
    assert revived.artifacts.load("A") == {"x": 1}
    assert revived.artifacts.load("B") == {"x": 2}


def _wi_for_run(run_id: str, id_: str, deps: list[str] | None = None) -> WorkItem:
    return WorkItem(
        id=id_,
        team_run_id=run_id,
        agent_name="a",
        status=WorkItemStatus.PENDING,
        kind=WorkItemKind.ATOMIC,
        deps=deps or [],
        root_id=id_,
    )


def _register_scripted(name: str) -> None:
    autopost_name = f"{name}__autopost"
    register_definition(
        AgentDefinition(
            name=autopost_name,
            description=f"autopost serializer for {name}",
            system_prompt="p",
            toolkits=[],
            skills=[],
            include_skills=False,
            source="builtin",
        )
    )
    register_definition(
        AgentDefinition(
            name=name,
            description=f"scripted {name}",
            system_prompt="p",
            toolkits=[],
            skills=[],
            include_skills=False,
            posthook=PosthookConfig(
                agent_name=autopost_name,
                metadata_key="submitted_summary",
            ),
            source="builtin",
        )
    )


def _cleanup_scripted(name: str) -> None:
    unregister_definition(name)
    unregister_definition(f"{name}__autopost")


def _resume_executor_factory(team_run: TeamRun) -> Executor:
    async def _runner(defn, ctx):
        if defn.name.endswith("__autopost"):
            ctx.tool_metadata["submitted_summary"] = SubmittedSummary(
                summary="done",
                artifact={"done": True},
            )
            return {"phase": defn.name}
        return {"phase": defn.name}

    def _build_query_ctx(defn, active_run, wi):
        return TeamAgentContext(
            tool_metadata={
                "team_run_id": active_run.id,
                "work_item_id": wi.id,
                "agent_run_id": wi.agent_run_id,
                "agent_name": defn.name,
            }
        )

    def _build_posthook_ctx(posthook_defn, work_result):
        return TeamAgentContext(
            tool_metadata={
                "agent_name": posthook_defn.name,
                "work_result": work_result,
            }
        )

    return Executor(
        team_run=team_run,
        runner=_runner,
        build_query_context=_build_query_ctx,
        build_posthook_context=_build_posthook_ctx,
        agent_lookup=get_definition,
    )


def test_resume_from_raises_on_missing_run(tmp_path: Path) -> None:
    store = JsonlTeamRunStore(tmp_path)
    with pytest.raises(ValueError, match="no events"):
        TeamRun.resume_from(store, "nope")


def test_resume_from_raises_on_missing_header(tmp_path: Path) -> None:
    store = JsonlTeamRunStore(tmp_path)
    store.append(make_team_run_status("r", "running"))  # no created event
    with pytest.raises(ValueError, match="missing team_run_created"):
        TeamRun.resume_from(store, "r")


@pytest.mark.asyncio
async def test_team_run_resume_replays_running_work_item(tmp_path: Path) -> None:
    store = JsonlTeamRunStore(tmp_path)
    run_id = "tr-resume-running"
    wi = WorkItem(
        id="A",
        team_run_id=run_id,
        agent_name="resume_worker",
        status=WorkItemStatus.PENDING,
        kind=WorkItemKind.ATOMIC,
        root_id="A",
    )
    store.append(
        make_team_run_created(
            run_id,
            session_id="sess-1",
            user_request="resume this run",
            goal=None,
            repo_root="/repo",
            sandbox_id="sbx-123",
            budgets={},
        )
    )
    store.append(make_work_item_added(run_id, work_item_to_dict(wi)))
    store.append(make_work_item_status(run_id, "A", "ready"))
    store.append(make_work_item_status(run_id, "A", "running", agent_run_id="AR-old"))
    store.append(make_team_run_status(run_id, "running"))

    revived = TeamRun.resume_from(store, run_id)
    assert revived.sandbox_id == "sbx-123"
    assert revived.dispatcher.graph["A"].status == WorkItemStatus.RUNNING

    _register_scripted("resume_worker")
    try:
        await revived.resume(
            executor_factory=_resume_executor_factory,
            num_executors=1,
        )
        status = await revived.wait()
    finally:
        _cleanup_scripted("resume_worker")

    assert status == TeamRunStatus.SUCCEEDED
    assert revived.dispatcher.graph["A"].status == WorkItemStatus.DONE
    assert revived.dispatcher.graph["A"].agent_run_id is not None
    assert revived.dispatcher.graph["A"].agent_run_id != "AR-old"
    assert revived.artifacts.load("A") == {"done": True}
