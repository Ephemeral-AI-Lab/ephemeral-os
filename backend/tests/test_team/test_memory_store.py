"""Tests for durable typed team memory persistence."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from team.context.project import ProjectContext
from team.memory.model import TeamMemoryRecordModel  # noqa: F401
from team.memory.runtime import persist_memory_record
from team.memory.store import TeamMemoryRecord, TeamMemoryStore
from team.models import BudgetConfig, BudgetState, Task, TaskStatus
from team.persistence.run_store import NullTeamRunStore
from team.runtime.services import TeamRuntimeServices
from team.runtime.team_run import TeamRun


def _memory_store() -> TeamMemoryStore:
    engine = create_engine("sqlite:///:memory:", echo=False)
    # Only create the tables this test needs (ARRAY columns in other
    # models are incompatible with SQLite).
    TeamMemoryRecordModel.__table__.create(engine, checkfirst=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = TeamMemoryStore()
    store.initialize(factory)
    return store


class _FakeDispatcher:
    def __init__(self) -> None:
        self.budgets = BudgetConfig()
        self.budget_state = BudgetState()
        self.task_center = None


def _fake_services() -> TeamRuntimeServices:
    return TeamRuntimeServices(
        project_context=ProjectContext(goal="", user_request="", project_key="", repo_root=""),
        dispatcher=_FakeDispatcher(),  # type: ignore[arg-type]
        event_store=NullTeamRunStore(),
    )


def _make_run_with_context(
    monkeypatch, store: TeamMemoryStore
) -> tuple[TeamRun, Task]:
    """Return a (run, task) pair wired to *store* via monkeypatch."""
    monkeypatch.setattr("team.memory.runtime.get_default_store", lambda: store)
    run = TeamRun(session_id="S1", user_request="hello", repo_root="/repo", services=_fake_services())
    run.project_context = ProjectContext(
        goal="g", user_request="u", project_key="P1", repo_root="/repo"
    )
    task = Task(
        id="W1",
        team_run_id=run.id,
        agent_name="validator",
        status=TaskStatus.DONE,
        task="verify src/runtime/dispatcher.py",
        scope_paths=["src/runtime/dispatcher.py"],
    )
    return run, task


def test_team_memory_store_roundtrip_and_query(monkeypatch) -> None:
    store = _memory_store()
    monkeypatch.setattr("team.memory.runtime.get_default_store", lambda: store)

    persisted = persist_memory_record(
        project_key="P1",
        repo_root="/repo",
        kind="architecture_decision",
        scope={"paths": ["src/runtime"]},
        content={"decision": "publish from worker, not posthook"},
        source={"team_run_id": "T1", "agent": "planner"},
    )

    assert persisted is True
    results = store.query(
        project_key="P1",
        kinds=["architecture_decision"],
        scope_paths=["src/runtime"],
    )
    assert len(results) == 1
    assert results[0].content["decision"] == "publish from worker, not posthook"


def test_team_run_persists_validator_outcome(monkeypatch) -> None:
    store = _memory_store()
    run, task = _make_run_with_context(monkeypatch, store)

    persisted = run.note_validator_outcome(task=task, summary="PASS: targeted pytest node passed")

    assert persisted is True
    results = store.query(
        project_key="P1",
        kinds=["validation_outcome"],
        scope_paths=["src/runtime/dispatcher.py"],
    )
    assert len(results) == 1
    assert results[0].content["summary"] == "PASS: targeted pytest node passed"


def test_team_run_persists_validator_outcome_with_failure_summary(monkeypatch) -> None:
    store = _memory_store()
    run, task = _make_run_with_context(monkeypatch, store)

    persisted = run.note_validator_outcome(task=task, summary="FAIL: command timed out")

    assert persisted is True
    results = store.query(
        project_key="P1",
        kinds=["validation_outcome"],
        scope_paths=["src/runtime/dispatcher.py"],
    )
    assert len(results) == 1
    assert results[0].content["summary"] == "FAIL: command timed out"


def test_team_run_persists_conflict_event(monkeypatch) -> None:
    store = _memory_store()
    run, _ = _make_run_with_context(monkeypatch, store)

    persisted = run.note_conflict_event(
        file_path="src/runtime/dispatcher.py",
        reason="Scope coherence changed since the work item started.",
        work_item_id="W2",
        agent_name="developer",
    )

    assert persisted is True
    results = store.query(
        project_key="P1",
        kinds=["conflict_event"],
        scope_paths=["src/runtime/dispatcher.py"],
    )
    assert len(results) == 1
    assert results[0].content["reason"].startswith("Scope coherence changed")


def test_team_memory_query_applies_scope_filter_before_limit() -> None:
    store = _memory_store()
    store.append_many(
        [
            TeamMemoryRecord(
                project_key="P1",
                repo_root="/repo",
                kind="conflict_event",
                scope={"paths": ["src/other.py"]},
                content={"n": idx},
                observed_at=100.0 - idx,
            )
            for idx in range(5)
        ]
    )
    store.append(
        TeamMemoryRecord(
            project_key="P1",
            repo_root="/repo",
            kind="conflict_event",
            scope={"paths": ["src/target.py"]},
            content={"n": 999},
            observed_at=1.0,
        )
    )

    results = store.query(
        project_key="P1",
        kinds=["conflict_event"],
        scope_paths=["src/target.py"],
        limit=3,
    )

    assert len(results) == 1
    assert results[0].content["n"] == 999
