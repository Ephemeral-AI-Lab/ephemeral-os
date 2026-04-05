"""E2E test fixtures — in-memory DB, mock LLM, TestClient."""

from __future__ import annotations

import os
import sys
import types
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies not installed in test env
# ---------------------------------------------------------------------------

_STUB_MODULES = [
    "anthropic", "anthropic.types",
    "openai", "openai.types", "openai.types.chat",
    "httpx",
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
            stub.__dict__.setdefault("AsyncOpenAI", MagicMock)
            stub.__dict__.setdefault("Daytona", MagicMock)
            stub.__dict__.setdefault("DaytonaConfig", MagicMock)
            stub.__dict__.setdefault("CreateSandboxParams", MagicMock)
            sys.modules[mod_name] = stub


_install_stubs()

# Now safe to import project code
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from db.base import Base
from engine.messages import ConversationMessage, TextBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class MockApiClient:
    """Deterministic mock that captures what tools/system_prompt the engine sends."""

    def __init__(self) -> None:
        self.last_request: Any = None
        self.responses: list[ConversationMessage] = []
        self._call_count = 0

    def set_responses(self, *msgs: ConversationMessage) -> None:
        self.responses = list(msgs)

    async def stream_message(self, request: Any) -> AsyncIterator:
        """Capture the request and yield a deterministic response."""
        from models.types import ApiMessageCompleteEvent, UsageSnapshot

        self.last_request = request
        idx = min(self._call_count, len(self.responses) - 1)
        msg = self.responses[idx] if self.responses else ConversationMessage(
            role="assistant", content=[TextBlock(text="I have no tools.")]
        )
        self._call_count += 1

        yield ApiMessageCompleteEvent(
            message=msg,
            usage=UsageSnapshot(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session_factory(tmp_path):
    """Create an in-memory SQLite DB with all tables."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    # Import all models so Base.metadata knows about them
    import db.models  # noqa: F401
    import agents.db.model  # noqa: F401
    import skills.db  # noqa: F401

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
    from fastapi.testclient import TestClient

    # Prevent env vars from leaking into test
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)

    # Monkey-patch initialize_db to return our test session factory
    monkeypatch.setattr(
        "db.engine.initialize_db",
        lambda *a, **kw: db_session_factory,
    )

    # Monkey-patch model_store.seed_from_json to use a test registry
    import json
    test_registry = {
        "models": [
            {
                "key": "minimax",
                "label": "MiniMax M2.7 Highspeed",
                "factory": {
                    "class_path": "openai",
                    "kwargs": {
                        "model": "MiniMax-M2.7-highspeed",
                        "api_key": "test-api-key",
                        "base_url": "https://api.minimax.chat/v1",
                    },
                },
            },
            {
                "key": "claude-sonnet",
                "label": "Claude Sonnet 4",
                "factory": {
                    "class_path": "anthropic",
                    "kwargs": {
                        "model": "claude-sonnet-4-20250514",
                        "api_key": "test-anthropic-key",
                    },
                },
            },
        ],
        "active": "minimax",
    }
    test_registry_path = tmp_path / "registry.json"
    test_registry_path.write_text(json.dumps(test_registry))

    # Patch the registry path lookup
    monkeypatch.setattr(
        "server.app_factory.Path.__truediv__",
        lambda self, other: test_registry_path if other == "registry.json" else self.__class__.__truediv__(self, other),
    )

    # Monkey-patch make_api_client to return our mock
    monkeypatch.setattr(
        "engine.agent.make_api_client",
        lambda *a, **kw: mock_api_client,
    )

    # Monkey-patch make_hook_executor
    monkeypatch.setattr(
        "engine.agent.make_hook_executor",
        lambda *a, **kw: None,
    )

    # Monkey-patch build_runtime_system_prompt
    monkeypatch.setattr(
        "engine.agent.build_runtime_system_prompt",
        lambda *a, **kw: "You are a test assistant.",
    )

    # Monkey-patch settings to include api_key so resolve_api_key doesn't raise
    original_load = None
    try:
        from config.settings import load_settings as _orig_load
        original_load = _orig_load
    except Exception:
        pass

    def _patched_load_settings(*a, **kw):
        from config.settings import Settings, DatabaseSettings
        return Settings(
            api_key="test-api-key",
            model="claude-sonnet-4-20250514",
            database=DatabaseSettings(url=f"sqlite:///{tmp_path / 'test.db'}"),
        )

    monkeypatch.setattr("config.load_settings", _patched_load_settings)
    monkeypatch.setattr("config.settings.load_settings", _patched_load_settings)
    monkeypatch.setattr("server.app_factory.load_settings", _patched_load_settings)

    from server.protocol import BackendHostConfig
    from server.app_factory import create_app

    config = BackendHostConfig(
        api_key="test-api-key",
        model="claude-sonnet-4-20250514",
        api_client=mock_api_client,
    )
    app = create_app(config)
    client = TestClient(app)

    # Yield both client and mock for assertions
    yield client, mock_api_client
