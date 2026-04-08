"""Tests for the production ``build_query_context`` wiring (Step 2c)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from team.artifacts.store import InMemoryArtifactStore
from team.context.project import ProjectContext
from team.models import (
    Briefing,
    BudgetConfig,
    BudgetState,
    DependencyArtifact,
    WorkItem,
    WorkItemStatus,
)
from team.runtime.context_builder import (
    build_initial_user_message,
    build_query_context,
    default_base_prompt,
)


@dataclass
class _FakeDispatcher:
    artifact_store: InMemoryArtifactStore


def _fake_team_run(artifact_store: InMemoryArtifactStore) -> SimpleNamespace:
    return SimpleNamespace(
        id="T1",
        dispatcher=_FakeDispatcher(artifact_store=artifact_store),
        project_context=ProjectContext(goal="g", user_request="u"),
        budgets=BudgetConfig(),
    )


def _wi(**over) -> WorkItem:
    base = dict(id="W1", team_run_id="T1", agent_name="worker", status=WorkItemStatus.READY)
    base.update(over)
    return WorkItem(**base)


def test_default_base_prompt_uses_task_key():
    assert default_base_prompt(_wi(payload={"task": "do it"})) == "do it"


def test_default_base_prompt_fallback():
    out = default_base_prompt(_wi(payload={}))
    assert "W1" in out and "worker" in out


def test_build_initial_user_message_no_briefings():
    store = InMemoryArtifactStore(BudgetConfig(), BudgetState())
    tr = _fake_team_run(store)
    msg = build_initial_user_message(tr, _wi(), "base")
    assert msg == "base"


def test_build_initial_user_message_prepends_briefings():
    store = InMemoryArtifactStore(BudgetConfig(), BudgetState())
    store.save("A1", "brief body")
    tr = _fake_team_run(store)
    wi = _wi(briefings=[Briefing(name="ctx", source="artifact", ref="A1")])
    msg = build_initial_user_message(tr, wi, "task text")
    assert "brief body" in msg
    assert msg.endswith("task text")


def test_build_query_context_carries_team_metadata_and_briefings():
    store = InMemoryArtifactStore(BudgetConfig(), BudgetState())
    store.save("P", {"target_paths": ["src"], "summary": "scout report"})
    tr = _fake_team_run(store)
    wi = _wi(
        payload={"task": "implement"},
        dep_artifacts=[
            DependencyArtifact(source_wi_id="P", artifact_ref="P", display_name="scout_1")
        ],
    )
    defn = SimpleNamespace(name="worker")
    ctx = build_query_context(defn, tr, wi)
    assert "scout report" in ctx["user_message"]
    assert ctx["user_message"].endswith("implement")
    meta = ctx["tool_metadata"]
    assert meta == {"team_run_id": "T1", "work_item_id": "W1", "agent_run_id": None}


def test_shared_briefings_flow_into_query_context():
    store = InMemoryArtifactStore(BudgetConfig(), BudgetState())
    store.save("S1", {"target_paths": ["src/auth"], "summary": "shared scout"})
    tr = _fake_team_run(store)
    tr.project_context.shared_briefings = {
        "src/auth": Briefing(name="auth_map", source="artifact", ref="S1")
    }
    wi = _wi(payload={"task": "refactor auth"})
    defn = SimpleNamespace(name="worker")
    ctx = build_query_context(defn, tr, wi)
    assert "shared scout" in ctx["user_message"]
    assert "Shared context" in ctx["user_message"]
