"""Unit tests for token_tracker module."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from token_tracker import TokenTracker, TokenUsageRecord, UsageStore


@pytest.fixture
def session_factory():
    db_path = "sqlite:///:memory:"
    engine = create_engine(db_path, echo=False)
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return sf


@pytest.fixture
def tracker(session_factory):
    t = TokenTracker()
    t.initialize(session_factory)
    return t


@pytest.fixture
def store(session_factory):
    s = UsageStore()
    s.initialize(session_factory)
    return s


class TestTokenUsageRecord:
    def test_record_fields(self, session_factory):
        with session_factory() as db:
            rec = TokenUsageRecord(
                session_id="sess-123",
                run_id="run-123",
                agent_name="test-agent",
                model_id="claude-3-5-sonnet",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )
            db.add(rec)
            db.commit()
            db.refresh(rec)

            assert rec.id is not None
            assert rec.session_id == "sess-123"
            assert rec.run_id == "run-123"
            assert rec.agent_name == "test-agent"
            assert rec.model_id == "claude-3-5-sonnet"
            assert rec.prompt_tokens == 100
            assert rec.completion_tokens == 50
            assert rec.total_tokens == 150
            assert rec.timestamp is not None

    def test_repr(self, session_factory):
        with session_factory() as db:
            rec = TokenUsageRecord(
                session_id="sess-456",
                run_id="run-456",
                agent_name="agent",
                model_id="gpt-4o",
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            )
            db.add(rec)
            db.commit()

            assert "gpt-4o" in repr(rec)
            assert "30" in repr(rec)


class TestUsageStore:
    def test_record_creates_usage_record(self, store, session_factory):
        rec = store.record(
            session_id="sess-001",
            run_id="run-001",
            agent_name="my-agent",
            model_id="claude-3",
            prompt_tokens=100,
            completion_tokens=50,
        )

        assert rec.session_id == "sess-001"
        assert rec.run_id == "run-001"
        assert rec.agent_name == "my-agent"
        assert rec.model_id == "claude-3"
        assert rec.prompt_tokens == 100
        assert rec.completion_tokens == 50
        assert rec.total_tokens == 150

    def test_record_defaults_to_zero(self, store):
        rec = store.record(
            session_id="sess-002",
            agent_name="agent",
            model_id="model",
        )

        assert rec.prompt_tokens == 0
        assert rec.completion_tokens == 0
        assert rec.total_tokens == 0

    def test_get_session_usage_single_session(self, store):
        store.record(
            session_id="sess-100",
            agent_name="a",
            model_id="m",
            prompt_tokens=10,
            completion_tokens=5,
        )
        store.record(
            session_id="sess-100",
            agent_name="a",
            model_id="m",
            prompt_tokens=20,
            completion_tokens=10,
        )

        usage = store.get_session_usage("sess-100")

        assert usage["session_id"] == "sess-100"
        assert usage["prompt_tokens"] == 30
        assert usage["completion_tokens"] == 15
        assert usage["total_tokens"] == 45
        assert usage["call_count"] == 2

    def test_get_session_usage_empty_session(self, store):
        usage = store.get_session_usage("nonexistent-session")

        assert usage["session_id"] == "nonexistent-session"
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0
        assert usage["total_tokens"] == 0
        assert usage["call_count"] == 0

    def test_get_usage_by_model_all_sessions(self, store):
        store.record(
            session_id="s1",
            agent_name="a",
            model_id="claude-3",
            prompt_tokens=100,
            completion_tokens=50,
        )
        store.record(
            session_id="s2",
            agent_name="a",
            model_id="claude-3",
            prompt_tokens=200,
            completion_tokens=100,
        )
        store.record(
            session_id="s3",
            agent_name="a",
            model_id="gpt-4",
            prompt_tokens=50,
            completion_tokens=25,
        )

        usage = store.get_usage_by_model()

        by_model = {u["model_id"]: u for u in usage}
        assert "claude-3" in by_model
        assert "gpt-4" in by_model

        assert by_model["claude-3"]["prompt_tokens"] == 300
        assert by_model["claude-3"]["completion_tokens"] == 150
        assert by_model["claude-3"]["call_count"] == 2

        assert by_model["gpt-4"]["prompt_tokens"] == 50
        assert by_model["gpt-4"]["completion_tokens"] == 25
        assert by_model["gpt-4"]["call_count"] == 1

    def test_get_usage_by_model_filtered_by_session(self, store):
        store.record(
            session_id="sess-A",
            agent_name="a",
            model_id="claude",
            prompt_tokens=100,
            completion_tokens=50,
        )
        store.record(
            session_id="sess-B",
            agent_name="a",
            model_id="claude",
            prompt_tokens=200,
            completion_tokens=100,
        )

        usage = store.get_usage_by_model(session_id="sess-A")

        assert len(usage) == 1
        assert usage[0]["model_id"] == "claude"
        assert usage[0]["prompt_tokens"] == 100
        assert usage[0]["completion_tokens"] == 50
        assert usage[0]["call_count"] == 1

    def test_get_run_usage_returns_summary(self, store):
        store.record(
            session_id="sess-run",
            run_id="run-123",
            agent_name="agent",
            model_id="claude-3",
            prompt_tokens=40,
            completion_tokens=10,
        )

        usage = store.get_run_usage("run-123")

        assert usage is not None
        assert usage["run_id"] == "run-123"
        assert usage["model_id"] == "claude-3"
        assert usage["prompt_tokens"] == 40
        assert usage["completion_tokens"] == 10
        assert usage["total_tokens"] == 50

    def test_get_usage_for_runs_returns_map(self, store):
        store.record(
            session_id="sess-map",
            run_id="run-a",
            agent_name="agent",
            model_id="claude-3",
            prompt_tokens=10,
            completion_tokens=5,
        )
        store.record(
            session_id="sess-map",
            run_id="run-b",
            agent_name="agent",
            model_id="gpt-4",
            prompt_tokens=20,
            completion_tokens=10,
        )
        store.record(
            session_id="sess-map",
            agent_name="legacy",
            model_id="claude-3",
            prompt_tokens=99,
            completion_tokens=1,
        )

        usage_map = store.get_usage_for_runs(["run-a", "run-b", "missing"])

        assert set(usage_map) == {"run-a", "run-b"}
        assert usage_map["run-a"]["total_tokens"] == 15
        assert usage_map["run-b"]["total_tokens"] == 30

    def test_session_usage_ignores_missing_run_id_for_aggregate(self, store):
        store.record(
            session_id="sess-mixed",
            run_id="run-linked",
            agent_name="agent",
            model_id="claude-3",
            prompt_tokens=25,
            completion_tokens=5,
        )
        store.record(
            session_id="sess-mixed",
            agent_name="legacy",
            model_id="claude-3",
            prompt_tokens=10,
            completion_tokens=5,
        )

        usage = store.get_session_usage("sess-mixed")

        assert usage["prompt_tokens"] == 35
        assert usage["completion_tokens"] == 10
        assert usage["total_tokens"] == 45
        assert usage["call_count"] == 2


class TestTokenTracker:
    def test_record_delegates_to_store(self, tracker):
        rec = tracker.record(
            session_id="sess-T1",
            run_id="run-T1",
            agent_name="tracker-agent",
            model_id="test-model",
            prompt_tokens=75,
            completion_tokens=25,
        )

        assert rec.session_id == "sess-T1"
        assert rec.run_id == "run-T1"
        assert rec.prompt_tokens == 75
        assert rec.completion_tokens == 25
        assert rec.total_tokens == 100

    def test_get_session_usage_delegates(self, tracker):
        tracker.record(
            session_id="sess-T2",
            agent_name="a",
            model_id="m",
            prompt_tokens=10,
            completion_tokens=5,
        )

        usage = tracker.get_session_usage("sess-T2")

        assert usage["session_id"] == "sess-T2"
        assert usage["prompt_tokens"] == 10
        assert usage["total_tokens"] == 15

    def test_get_usage_by_model_delegates(self, tracker):
        tracker.record(
            session_id="s1",
            agent_name="a",
            model_id="model-X",
            prompt_tokens=50,
            completion_tokens=25,
        )

        usage = tracker.get_usage_by_model()

        assert len(usage) == 1
        assert usage[0]["model_id"] == "model-X"
        assert usage[0]["total_tokens"] == 75

    def test_store_property_provides_direct_access(self, tracker):
        assert tracker.store is tracker._store

    def test_multiple_sessions_isolated(self, tracker):
        tracker.record(
            session_id="sess-A",
            agent_name="a",
            model_id="m",
            prompt_tokens=100,
            completion_tokens=0,
        )
        tracker.record(
            session_id="sess-B",
            agent_name="a",
            model_id="m",
            prompt_tokens=200,
            completion_tokens=0,
        )

        usage_a = tracker.get_session_usage("sess-A")
        usage_b = tracker.get_session_usage("sess-B")

        assert usage_a["prompt_tokens"] == 100
        assert usage_b["prompt_tokens"] == 200
