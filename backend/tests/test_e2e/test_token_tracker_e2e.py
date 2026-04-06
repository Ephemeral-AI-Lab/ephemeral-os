"""E2E tests for token tracker usage API endpoints."""

from __future__ import annotations

import json

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
