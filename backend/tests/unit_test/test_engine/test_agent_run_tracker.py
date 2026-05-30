"""Tests for engine.agent.run_tracker."""

from __future__ import annotations

from engine.agent.run_tracker import AgentRunTracker


def test_create_does_not_retry_on_duplicate_auto_run_id(monkeypatch):
    class DuplicateKeyError(Exception):
        pass

    calls: list[str] = []

    class FakeStore:
        def create_run(self, **kwargs):
            calls.append(kwargs["agent_run_id"])
            if len(calls) == 1:
                raise DuplicateKeyError("duplicate key value violates unique constraint")
            return None

    monkeypatch.setattr(
        "engine.agent.run_tracker._get_agent_run_store",
        lambda: FakeStore(),
    )
    monkeypatch.setattr(
        "engine.agent.run_tracker.uuid4",
        lambda: type("UUID", (), {"hex": "duplicate000000"})(),
    )

    tracker = AgentRunTracker.create(
        task_id="run-1:t1",
        agent_name="developer",
    )

    # Always-mint: the id is returned even when the insert fails (only the
    # durable ``agent_runs`` row, gated by ``_persisted``, is skipped).
    assert tracker.agent_run_id == "duplicate000000"[:16]
    assert tracker._persisted is False
    assert calls == ["duplicate000000"[:16]]
