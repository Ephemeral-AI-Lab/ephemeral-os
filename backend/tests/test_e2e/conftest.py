# ruff: noqa
"""E2E test fixtures — in-memory DB, mock LLM, TestClient."""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Stub heavy dependencies ONLY if they are genuinely not installed.
# If the real package is available, use it (needed for live API tests).
# IMPORTANT: do NOT stub httpx — FastAPI TestClient needs it.
# ---------------------------------------------------------------------------


def _try_import_or_stub(mod_name: str, attrs: dict) -> None:
    """Import the real module if available; otherwise install a stub."""
    if mod_name in sys.modules:
        return  # already loaded
    try:
        __import__(mod_name)
    except ImportError:
        _stub = types.ModuleType(mod_name)
        for k, v in attrs.items():
            _stub.__dict__.setdefault(k, v)
        sys.modules[mod_name] = _stub


_try_import_or_stub(
    "anthropic",
    {
        "APIError": type("APIError", (Exception,), {}),
        "APIStatusError": type("APIStatusError", (Exception,), {}),
        "AsyncAnthropic": MagicMock,
    },
)
_try_import_or_stub("anthropic.types", {})
_try_import_or_stub(
    "daytona_sdk",
    {
        "Daytona": MagicMock,
        "DaytonaConfig": MagicMock,
        "CreateSandboxParams": MagicMock,
    },
)
_try_import_or_stub(
    "daytona_sdk.daytona",
    {
        "Daytona": MagicMock,
        "DaytonaConfig": MagicMock,
        "CreateSandboxParams": MagicMock,
    },
)

# Now safe to import project code
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from engine.messages import ConversationMessage, TextBlock, ThinkingBlock, ToolUseBlock
from models.types import (
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
        msg = (
            self.responses[idx]
            if self.responses
            else ConversationMessage(role="assistant", content=[TextBlock(text="I have no tools.")])
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
    import db.models  # noqa: F401
    import agents.db.model  # noqa: F401

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
        "db.engine.initialize_db",
        lambda *a, **kw: db_session_factory,
    )

    # Monkey-patch make_api_client to return our mock.
    # Must patch BOTH the module attribute AND the local binding created by
    # `from models.provider import make_api_client` in engine.agent, because
    # once engine.agent is imported the local name is cached.
    monkeypatch.setattr(
        "models.provider.make_api_client",
        lambda *a, **kw: mock_api_client,
    )
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
    def _patched_load_settings(*a, **kw):
        from config.settings import Settings, DatabaseSettings  # noqa: PLC0415

        return Settings(
            api_key="test-api-key",
            model="claude-sonnet-4-20250514",
            database=DatabaseSettings(url=f"sqlite:///{tmp_path / 'test.db'}"),
        )

    monkeypatch.setattr("config.load_settings", _patched_load_settings)
    monkeypatch.setattr("config.settings.load_settings", _patched_load_settings)
    monkeypatch.setattr("server.app_factory.load_settings", _patched_load_settings)

    from server.protocol import BackendHostConfig  # noqa: PLC0415
    from server.app_factory import create_app  # noqa: PLC0415

    config = BackendHostConfig(
        api_key="test-api-key",
        model="claude-sonnet-4-20250514",
        api_client=mock_api_client,
    )
    app = create_app(config)

    # Use context manager so lifespan runs (initializes _session, DB stores, etc.)
    with TestClient(app) as client:
        yield client, mock_api_client


# ---------------------------------------------------------------------------
# Live E2E shared helpers — credential loading, client factory, sandbox mgmt
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")


def _load_live_settings() -> dict:
    """Load settings from ~/.ephemeralos/settings.json for live tests."""
    settings_path = Path.home() / ".ephemeralos" / "settings.json"
    if settings_path.exists():
        return json.loads(settings_path.read_text())
    return {}


_LIVE_SETTINGS = _load_live_settings()

MINIMAX_KEY = os.environ.get("MINIMAX_API_KEY") or _LIVE_SETTINGS.get("api_key", "")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL") or _LIVE_SETTINGS.get("model", "MiniMax-M2.7-highspeed")
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL") or _LIVE_SETTINGS.get("base_url", "")
MINIMAX_FORMAT = os.environ.get("MINIMAX_API_FORMAT") or _LIVE_SETTINGS.get("api_format", "openai")

# Anthropic-format MiniMax credentials (Anthropic-native client)
ANTHROPIC_MINIMAX_KEY = (
    os.environ.get("ANTHROPIC_MINIMAX_API_KEY")
    or _LIVE_SETTINGS.get("anthropic_api_key", "")
    or "sk-cp-Ril2d0sHwI7gagi0S5s9XWFvfPpe6Y8Ms0N7FxpILv93jZCXJDmEiWGRjVALI4VKvSr2XhJfYs5_wLYfhB4QPKWKd4IJHkfZBLhRXQR5tAnjwKiItvcYg-o"
)
ANTHROPIC_MINIMAX_MODEL = os.environ.get("ANTHROPIC_MINIMAX_MODEL") or "MiniMax-M2.7-highspeed"
ANTHROPIC_MINIMAX_BASE_URL = os.environ.get("ANTHROPIC_MINIMAX_BASE_URL") or "https://api.minimax.io/anthropic"
ANTHROPIC_MINIMAX_FORMAT = "anthropic"

DAYTONA_KEY = os.environ.get("DAYTONA_API_KEY") or _LIVE_SETTINGS.get("daytona_api_key", "")
DAYTONA_URL = os.environ.get("DAYTONA_API_URL") or _LIVE_SETTINGS.get("daytona_api_url", "")
DAYTONA_TARGET = os.environ.get("DAYTONA_TARGET") or _LIVE_SETTINGS.get("daytona_target", "")

HAS_MINIMAX = bool(MINIMAX_KEY and MINIMAX_BASE_URL)
HAS_ANTHROPIC_MINIMAX = bool(ANTHROPIC_MINIMAX_KEY and ANTHROPIC_MINIMAX_BASE_URL)
HAS_DAYTONA = bool(DAYTONA_KEY and DAYTONA_URL)
HAS_BOTH = HAS_MINIMAX and HAS_DAYTONA
HAS_ANTHROPIC_AND_DAYTONA = HAS_ANTHROPIC_MINIMAX and HAS_DAYTONA


def make_live_client(
    db_session_factory,
    tmp_path,
    monkeypatch,
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    api_format: str = "",
):
    """Create a TestClient configured with real API credentials.

    Critical: sets DAYTONA env vars via monkeypatch so that the async Daytona
    client (which reads os.environ directly) can find the credentials.
    """
    from fastapi.testclient import TestClient
    from server.protocol import BackendHostConfig
    from server.app_factory import create_app

    api_key = api_key or MINIMAX_KEY
    model = model or MINIMAX_MODEL
    base_url = base_url or MINIMAX_BASE_URL
    api_format = api_format or MINIMAX_FORMAT

    # Clear proxy env vars to prevent httpx routing through localhost proxy
    for _var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]:
        monkeypatch.delenv(_var, raising=False)
    monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # CRITICAL: Set Daytona env vars so async_client._require_settings() finds them.
    # The async client reads os.environ directly, not the Settings object.
    if DAYTONA_KEY:
        monkeypatch.setenv("DAYTONA_API_KEY", DAYTONA_KEY)
    if DAYTONA_URL:
        monkeypatch.setenv("DAYTONA_API_URL", DAYTONA_URL)
    if DAYTONA_TARGET:
        monkeypatch.setenv("DAYTONA_TARGET", DAYTONA_TARGET)

    monkeypatch.setattr("db.engine.initialize_db", lambda *a, **kw: db_session_factory)
    monkeypatch.setattr("engine.agent.make_hook_executor", lambda *a, **kw: None)

    def _patched_load_settings(*a, **kw):
        from config.settings import Settings, DatabaseSettings

        return Settings(
            api_key=api_key,
            model=model,
            api_format=api_format,
            base_url=base_url or None,
            daytona_api_key=DAYTONA_KEY,
            daytona_api_url=DAYTONA_URL,
            daytona_target=DAYTONA_TARGET,
            database=DatabaseSettings(url=f"sqlite:///{tmp_path / 'test.db'}"),
        )

    monkeypatch.setattr("config.load_settings", _patched_load_settings)
    monkeypatch.setattr("config.settings.load_settings", _patched_load_settings)
    monkeypatch.setattr("server.app_factory.load_settings", _patched_load_settings)

    config = BackendHostConfig(
        api_key=api_key,
        model=model,
        api_format=api_format,
        base_url=base_url or None,
    )
    app = create_app(config)
    return TestClient(app)


def get_sandbox_service():
    """Return a SandboxService instance."""
    from sandbox.service import SandboxService

    return SandboxService()


def create_test_sandbox(name: str = "e2e-test") -> dict:
    """Create a test sandbox and return its serialized dict."""
    svc = get_sandbox_service()
    sandbox = svc.create_sandbox(
        name=f"{name}-{int(time.time())}",
        language="python",
        labels={"purpose": f"e2e-{name}"},
    )
    return sandbox


def delete_test_sandbox(sandbox_id: str) -> None:
    """Delete a sandbox, swallowing errors."""
    try:
        svc = get_sandbox_service()
        svc.delete_sandbox(sandbox_id)
    except Exception:
        pass


def send_chat(
    client,
    line: str,
    *,
    agent_name: str | None = None,
    sandbox_id: str | None = None,
    timeout: int = 180,
) -> list[dict]:
    """Send a chat message and return parsed SSE events."""
    payload: dict[str, Any] = {"line": line}
    if agent_name:
        payload["agent_name"] = agent_name
    if sandbox_id:
        payload["sandbox_id"] = sandbox_id

    resp = client.post("/api/chat", json=payload, timeout=timeout)
    assert resp.status_code == 200, f"Chat failed: {resp.status_code} {resp.text[:500]}"
    return parse_sse_events(resp.text)


def get_assistant_text(events: list[dict]) -> str:
    """Extract ALL assistant message text from events across all turns.

    In a multi-turn agent loop the final formatted answer may come in any
    turn, so we concatenate text from every ``assistant_complete`` event.
    We also strip ``<think>...</think>`` blocks that some models (e.g.
    MiniMax) emit as regular text content rather than reasoning_content.
    """
    import re

    parts: list[str] = []
    for evt in events_of_type(events, "assistant_complete"):
        msg = evt.get("message", "")
        if msg:
            parts.append(msg)

    # Fall back to assistant_delta events when no assistant_complete exists
    if not parts:
        for evt in events_of_type(events, "assistant_delta"):
            msg = evt.get("message", "")
            if msg:
                parts.append(msg)

    text = "\n".join(parts)
    # Strip <think>...</think> blocks (some models inline thinking as text)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return text


def get_event_types(events: list[dict]) -> set[str]:
    """Get unique event types."""
    return {e["type"] for e in events}


def get_tool_started_events(events: list[dict]) -> list[dict]:
    """Get all tool_started events."""
    return events_of_type(events, "tool_started")


def get_tool_completed_events(events: list[dict]) -> list[dict]:
    """Get all tool_completed events."""
    return events_of_type(events, "tool_completed")


def get_tool_cancelled_events(events: list[dict]) -> list[dict]:
    """Get all tool_cancelled events."""
    return events_of_type(events, "tool_cancelled")


def create_test_agent(
    client,
    name: str,
    *,
    toolkits: list[str] | None = None,
    skills: list[str] | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
) -> dict:
    """Create an agent and return its data, handling duplicates."""
    payload: dict[str, Any] = {
        "name": name,
        "description": f"E2E test agent: {name}",
        "model": model or MINIMAX_MODEL,
    }
    if toolkits:
        payload["toolkits"] = toolkits
    if skills:
        payload["skills"] = skills
    if system_prompt:
        payload["system_prompt"] = system_prompt

    resp = client.post("/api/agents/", json=payload)
    if resp.status_code == 201:
        return resp.json()
    # Agent may already exist — fetch it
    get_resp = client.get(f"/api/agents/{name}")
    if get_resp.status_code == 200:
        return get_resp.json()
    assert False, f"Failed to create or get agent '{name}': {resp.status_code} {resp.text}"
