"""Tests for durable typed team memory persistence."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from team.context.project import ProjectContext
from team.memory.model import TeamMemoryRecordModel  # noqa: F401
from team.memory.runtime import persist_memory_record
from team.memory.store import TeamMemoryRecord, TeamMemoryStore
from team.models import WorkItem, WorkItemStatus
from team.runtime.team_run import TeamRun


def _memory_store() -> TeamMemoryStore:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = TeamMemoryStore()
    store.initialize(factory)
    return store


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
    monkeypatch.setattr("team.memory.runtime.get_default_store", lambda: store)

    run = TeamRun(session_id="S1", user_request="hello", repo_root="/repo")
    run.project_context = ProjectContext(
        goal="g",
        user_request="u",
        project_key="P1",
        repo_root="/repo",
    )
    work_item = WorkItem(
        id="W1",
        team_run_id=run.id,
        agent_name="validator",
        status=WorkItemStatus.DONE,
        payload={"verify": ["src/runtime/dispatcher.py"]},
    )

    persisted = run.note_validator_outcome(
        work_item=work_item,
        summary="PASS: targeted pytest node passed",
        artifact={"target_paths": ["src/runtime/dispatcher.py"], "result": "pass"},
    )

    assert persisted is True
    results = store.query(
        project_key="P1",
        kinds=["validation_outcome"],
        scope_paths=["src/runtime/dispatcher.py"],
    )
    assert len(results) == 1
    assert results[0].content["summary"] == "PASS: targeted pytest node passed"


def test_team_run_persists_validator_outcome_with_non_mapping_artifact(monkeypatch) -> None:
    store = _memory_store()
    monkeypatch.setattr("team.memory.runtime.get_default_store", lambda: store)

    run = TeamRun(session_id="S1", user_request="hello", repo_root="/repo")
    run.project_context = ProjectContext(
        goal="g",
        user_request="u",
        project_key="P1",
        repo_root="/repo",
    )
    work_item = WorkItem(
        id="W1",
        team_run_id=run.id,
        agent_name="validator",
        status=WorkItemStatus.DONE,
        payload={"verify": ["src/runtime/dispatcher.py"]},
    )

    persisted = run.note_validator_outcome(
        work_item=work_item,
        summary="FAIL: command timed out",
        artifact="timeout while running pytest",
    )

    assert persisted is True
    results = store.query(
        project_key="P1",
        kinds=["validation_outcome"],
        scope_paths=["src/runtime/dispatcher.py"],
    )
    assert len(results) == 1
    assert results[0].content["artifact"] == "timeout while running pytest"


def test_team_run_persists_conflict_event(monkeypatch) -> None:
    store = _memory_store()
    monkeypatch.setattr("team.memory.runtime.get_default_store", lambda: store)

    run = TeamRun(session_id="S1", user_request="hello", repo_root="/repo")
    run.project_context = ProjectContext(
        goal="g",
        user_request="u",
        project_key="P1",
        repo_root="/repo",
    )

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


def test_team_run_explicit_memory_keeps_runtime_provenance(monkeypatch) -> None:
    store = _memory_store()
    monkeypatch.setattr("team.memory.runtime.get_default_store", lambda: store)

    run = TeamRun(session_id="S1", user_request="hello", repo_root="/repo")
    run.project_context = ProjectContext(
        goal="g",
        user_request="u",
        project_key="P1",
        repo_root="/repo",
    )
    work_item = WorkItem(
        id="W1",
        team_run_id=run.id,
        agent_name="developer",
        status=WorkItemStatus.DONE,
    )

    persisted = run.note_explicit_memory_artifacts(
        work_item=work_item,
        artifact={
            "memory_records": [
                {
                    "kind": "architecture_decision",
                    "scope": {"paths": ["src/runtime"]},
                    "content": {"decision": "publish from worker, not hook"},
                    "source": {
                        "team_run_id": "forged-run",
                        "work_item_id": "forged-work-item",
                        "agent": "forged-agent",
                        "note": "child metadata",
                    },
                }
            ]
        },
    )

    assert persisted == 1
    results = store.query(
        project_key="P1",
        kinds=["architecture_decision"],
        scope_paths=["src/runtime"],
    )
    assert len(results) == 1
    assert results[0].source["team_run_id"] == run.id
    assert results[0].source["work_item_id"] == "W1"
    assert results[0].source["agent"] == "developer"
    assert results[0].source["note"] == "child metadata"


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
