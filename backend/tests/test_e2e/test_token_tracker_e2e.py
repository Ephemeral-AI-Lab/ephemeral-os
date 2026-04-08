"""E2E tests for token tracker usage API endpoints."""

from __future__ import annotations

from message.messages import ConversationMessage, TextBlock, ToolUseBlock
from tests.test_e2e.conftest import parse_sse_events


class TestUsageAPIEndpoints:
    """Test /api/db/usage endpoints."""

    def test_usage_endpoint_returns_200(self, app_client):
        client, _ = app_client
        resp = client.get("/api/db/usage")
        assert resp.status_code == 200

    def test_usage_endpoint_returns_by_model_when_no_session(self, app_client):
        client, _ = app_client
        resp = client.get("/api/db/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_model" in data

    def test_usage_endpoint_with_nonexistent_session_returns_empty(self, app_client):
        client, _ = app_client
        resp = client.get("/api/db/usage?session_id=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "nonexistent"
        assert data["prompt_tokens"] == 0
        assert data["completion_tokens"] == 0
        assert data["total_tokens"] == 0

    def test_session_usage_endpoint_returns_session_data(self, app_client):
        client, _ = app_client
        resp = client.get("/api/db/usage/test-session-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "test-session-123"

    def test_health_endpoint_returns_database_status(self, app_client):
        client, _ = app_client
        resp = client.get("/api/db/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "database" in data


class TestUsageRecordedDuringChat:
    """Test that usage is recorded when making chat requests."""

    def test_chat_completes_without_error(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "Hello"})
        assert resp.status_code == 200

        events = parse_sse_events(resp.text)
        types = [e.get("type") for e in events]
        assert "line_complete" in types

    def test_chat_records_run_linked_usage(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "Hello"})
        assert resp.status_code == 200

        from server.app_factory import agent_run_store, session_store, usage_store

        sessions = session_store.list_sessions(limit=10)
        assert sessions, "expected a persisted session"
        session_id = sessions[0]["session_id"]
        runs = agent_run_store.list_runs(session_id, limit=10)
        assert runs, "expected a persisted top-level run"
        usage = usage_store.get_run_usage(runs[0]["id"])
        assert usage is not None
        assert usage["run_id"] == runs[0]["id"]
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 50
        assert usage["total_tokens"] == 150

    def test_chat_persists_total_usage_across_tool_loop(self, app_client):
        client, mock = app_client
        mock.set_responses(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="toolu_missing", name="nonexistent_tool", input={})],
            ),
            ConversationMessage(
                role="assistant",
                content=[TextBlock(text="done")],
            ),
        )

        resp = client.post("/api/chat", json={"line": "Use a tool first"})
        assert resp.status_code == 200

        from server.app_factory import agent_run_store, session_store, usage_store

        sessions = session_store.list_sessions(limit=10)
        assert sessions, "expected a persisted session"
        session_id = sessions[0]["session_id"]
        runs = agent_run_store.list_runs(session_id, limit=10)
        assert runs, "expected a persisted top-level run"
        usage = usage_store.get_run_usage(runs[0]["id"])
        assert usage is not None
        # MockApiClient reports 150 tokens per model turn; this request takes
        # one tool-call turn plus a final answer turn, so persisted usage must
        # reflect the accumulated total rather than only the final turn.
        assert usage["total_tokens"] == 300
        assert usage["prompt_tokens"] == 200
        assert usage["completion_tokens"] == 100


class TestRunDetailUsageAPI:
    def test_run_detail_includes_parent_and_subagent_usage(self, app_client):
        client, _ = app_client
        from server.app_factory import agent_run_store, session_store, usage_store

        session_store.upsert(
            session_id="seed-session",
            cwd="/tmp",
            model="claude-seeded",
            message_count=0,
        )
        agent_run_store.create_run(
            run_id="parent-run",
            session_id="seed-session",
            agent_name="parent-agent",
            input_query="parent task",
        )
        agent_run_store.finish_run("parent-run", status="completed", response=[])
        agent_run_store.create_run(
            run_id="child-run",
            session_id="seed-session",
            agent_name="subagent",
            input_query="child task",
            parent_run_id="parent-run",
            parent_task_id="bg_1",
        )
        agent_run_store.finish_run("child-run", status="completed", response={"final_text": "ok"})
        usage_store.record(
            session_id="seed-session",
            run_id="parent-run",
            agent_name="parent-agent",
            model_id="claude-parent",
            prompt_tokens=40,
            completion_tokens=10,
        )
        usage_store.record(
            session_id="seed-session",
            run_id="child-run",
            agent_name="subagent",
            model_id="claude-child",
            prompt_tokens=15,
            completion_tokens=5,
        )

        resp = client.get("/api/db/runs/parent-run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["usage"]["model_id"] == "claude-parent"
        assert data["usage"]["total_tokens"] == 50
        assert len(data["subagent_runs"]) == 1
        child = data["subagent_runs"][0]
        assert child["id"] == "child-run"
        assert child["parent_task_id"] == "bg_1"
        assert child["usage"]["model_id"] == "claude-child"
        assert child["usage"]["total_tokens"] == 20

    def test_session_usage_includes_subagents_for_newly_tracked_runs(self, app_client):
        client, _ = app_client
        from server.app_factory import usage_store

        usage_store.record(
            session_id="agg-session",
            run_id="parent-run",
            agent_name="parent-agent",
            model_id="claude-parent",
            prompt_tokens=30,
            completion_tokens=10,
        )
        usage_store.record(
            session_id="agg-session",
            run_id="child-run",
            agent_name="subagent",
            model_id="claude-child",
            prompt_tokens=5,
            completion_tokens=5,
        )

        resp = client.get("/api/db/usage/agg-session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "agg-session"
        assert data["prompt_tokens"] == 35
        assert data["completion_tokens"] == 15
        assert data["total_tokens"] == 50
        assert data["call_count"] == 2

    def test_run_detail_returns_null_usage_for_legacy_run(self, app_client):
        client, _ = app_client
        from server.app_factory import agent_run_store, session_store

        session_store.upsert(
            session_id="legacy-session",
            cwd="/tmp",
            model="claude-seeded",
            message_count=0,
        )
        agent_run_store.create_run(
            run_id="legacy-run",
            session_id="legacy-session",
            agent_name="legacy-agent",
            input_query="legacy task",
        )
        agent_run_store.finish_run("legacy-run", status="completed", response=[])

        resp = client.get("/api/db/runs/legacy-run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["usage"] is None
        assert data["subagent_runs"] == []
