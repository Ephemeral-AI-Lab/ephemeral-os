# ruff: noqa: E402
"""E2E test fixtures — in-memory DB, mock LLM, TestClient."""

from __future__ import annotations

import sys
import types
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies not installed in test env
# Only stub modules that are genuinely missing — httpx is installed.
# ---------------------------------------------------------------------------

_STUB_MODULES = [
    "anthropic", "anthropic.types",
    "daytona_sdk", "daytona_sdk.daytona",
]

_originals: dict[str, Any] = {}


def _install_stubs() -> None:
    for mod_name in _STUB_MODULES:
        if mod_name not in sys.modules:
            _originals[mod_name] = None
            stub = types.ModuleType(mod_name)
            stub.__dict__.setdefault("APIError", type("APIError", (Exception,), {}))
            stub.__dict__.setdefault("APIStatusError", type("APIStatusError", (Exception,), {}))
            stub.__dict__.setdefault("AsyncAnthropic", MagicMock)
            stub.__dict__.setdefault("Daytona", MagicMock)
            stub.__dict__.setdefault("DaytonaConfig", MagicMock)
            stub.__dict__.setdefault("CreateSandboxParams", MagicMock)
            sys.modules[mod_name] = stub


_install_stubs()

# Now safe to import project code
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import json

from ephemeralos.db.base import Base  # noqa: E402
from ephemeralos.engine.messages import ConversationMessage, TextBlock, ThinkingBlock, ToolUseBlock  # noqa: E402
from ephemeralos.models.types import (  # noqa: E402
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    UsageSnapshot,
)


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class MockApiClient:
    """Deterministic mock that captures what tools/system_prompt the engine sends.

    Supports text-only, thinking+text, and tool-call responses.
    Streams ThinkingDelta and TextDelta events before the final message.
    """

    def __init__(self) -> None:
        self.last_request: Any = None
        self.all_requests: list[Any] = []
        self.responses: list[ConversationMessage] = []
        self._call_count = 0

    def set_responses(self, *msgs: ConversationMessage) -> None:
        self.responses = list(msgs)

    async def stream_message(self, request: Any) -> AsyncIterator:
        """Capture the request and yield streaming events + final message."""
        self.last_request = request
        self.all_requests.append(request)
        idx = min(self._call_count, len(self.responses) - 1) if self.responses else 0
        msg = self.responses[idx] if self.responses else ConversationMessage(
            role="assistant", content=[TextBlock(text="I have no tools.")]
        )
        self._call_count += 1

        # Stream thinking deltas
        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                yield ApiThinkingDeltaEvent(text=block.text)

        # Stream text deltas
        for block in msg.content:
            if isinstance(block, TextBlock):
                yield ApiTextDeltaEvent(text=block.text)

        yield ApiMessageCompleteEvent(
            message=msg,
            usage=UsageSnapshot(input_tokens=100, output_tokens=50),
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# SSE parsing helpers
# ---------------------------------------------------------------------------


def parse_sse_events(raw: str) -> list[dict[str, Any]]:
    """Parse SSE text into a list of JSON-decoded BackendEvent dicts."""
    events = []
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            payload = line[6:]
            if payload == "[DONE]":
                continue
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass
    return events


def events_of_type(events: list[dict], event_type: str) -> list[dict]:
    """Filter parsed SSE events by their 'type' field."""
    return [e for e in events if e.get("type") == event_type]


# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session_factory(tmp_path):
    """Create a file-based SQLite DB with all tables."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    # Import all models so Base.metadata knows about them
    import ephemeralos.db.models  # noqa: F401
    import ephemeralos.agents.db.model  # noqa: F401
    import ephemeralos.skills.db  # noqa: F401

    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return sf


@pytest.fixture()
def mock_api_client():
    """Return a fresh MockApiClient."""
    client = MockApiClient()
    client.set_responses(
        ConversationMessage(
            role="assistant",
            content=[TextBlock(text="Hello! I can see my tools.")],
        )
    )
    return client


# ---------------------------------------------------------------------------
# App + TestClient fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client(db_session_factory, mock_api_client, tmp_path, monkeypatch):
    """Create a FastAPI TestClient with real DB and mock LLM."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    # Prevent env vars from leaking into test
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)

    # Monkey-patch initialize_db to return our test session factory
    monkeypatch.setattr(
        "ephemeralos.db.engine.initialize_db",
        lambda *a, **kw: db_session_factory,
    )

    # Monkey-patch make_api_client to return our mock
    monkeypatch.setattr(
        "ephemeralos.engine.agent.make_api_client",
        lambda *a, **kw: mock_api_client,
    )

    # Monkey-patch make_hook_executor
    monkeypatch.setattr(
        "ephemeralos.engine.agent.make_hook_executor",
        lambda *a, **kw: None,
    )

    # Monkey-patch build_runtime_system_prompt
    monkeypatch.setattr(
        "ephemeralos.engine.agent.build_runtime_system_prompt",
        lambda *a, **kw: "You are a test assistant.",
    )

    # Monkey-patch settings to include api_key so resolve_api_key doesn't raise
    def _patched_load_settings(*a, **kw):
        from ephemeralos.config.settings import Settings, DatabaseSettings  # noqa: PLC0415
        return Settings(
            api_key="test-api-key",
            model="claude-sonnet-4-20250514",
            database=DatabaseSettings(url=f"sqlite:///{tmp_path / 'test.db'}"),
        )

    monkeypatch.setattr("ephemeralos.config.load_settings", _patched_load_settings)
    monkeypatch.setattr("ephemeralos.config.settings.load_settings", _patched_load_settings)
    monkeypatch.setattr("ephemeralos.server.app_factory.load_settings", _patched_load_settings)

    from ephemeralos.server.protocol import BackendHostConfig  # noqa: PLC0415
    from ephemeralos.server.app_factory import create_app  # noqa: PLC0415

    config = BackendHostConfig(
        api_key="test-api-key",
        model="claude-sonnet-4-20250514",
        api_client=mock_api_client,
    )
    app = create_app(config)
    client = TestClient(app)

    # Yield both client and mock for assertions
    yield client, mock_api_client
