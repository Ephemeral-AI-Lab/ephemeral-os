# ruff: noqa
"""E2E test fixtures — in-memory DB, mock LLM, TestClient, and EvalAgent helpers."""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Stub heavy dependencies ONLY if they are genuinely not installed.
# ---------------------------------------------------------------------------


def _try_import_or_stub(mod_name: str, attrs: dict) -> None:
    """Import the real module if available; otherwise install a stub."""
    if mod_name in sys.modules:
        return
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
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from db.base import Base
from engine.testing.eval_agent import EvalAgent
from message import ConversationMessage, TextBlock, ThinkingBlock, ToolUseBlock
from providers import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    UsageSnapshot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Credential checks (powered by EvalAgent)
# ---------------------------------------------------------------------------

# Load .env BEFORE credential checks so env vars are available
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

# Suppress "Event loop is closed" warnings from httpx/anthropic async cleanup.
# The async client's __del__ tries to close the transport after the loop shuts down.
import warnings
import logging

logging.getLogger("asyncio").setLevel(logging.CRITICAL)

HAS_CREDENTIALS = EvalAgent.has_credentials()
HAS_DAYTONA = EvalAgent.has_daytona()
HAS_ALL = EvalAgent.has_all()


def create_eval_agent(
    *,
    system_prompt: str | None = None,
    sandbox_id: str | None = None,
    **kwargs,
) -> EvalAgent:
    """Create an EvalAgent for e2e tests.

    Uses the active model from the DB registry (which has the correct
    client class, auth, and base_url already configured).
    """
    _reset_runtime_store_singletons()
    return EvalAgent.create(
        system_prompt=system_prompt,
        sandbox_id=sandbox_id,
        **kwargs,
    )


def _reset_runtime_store_singletons() -> None:
    """Detach server store singletons from per-test DB schemas."""
    try:
        from server import app_factory as _af

        for store in (
            _af.task_center_store,
            _af.agent_run_store,
            _af.model_store,
        ):
            if hasattr(store, "_session_factory"):
                store._session_factory = None
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Backward-compat: credential constants used by tests not yet refactored.
# These will be removed as tests migrate to EvalAgent.create().
# ---------------------------------------------------------------------------

import os

_LIVE_SETTINGS = {}
_settings_path = Path.home() / ".ephemeralos" / "settings.json"
if _settings_path.exists():
    _LIVE_SETTINGS = json.loads(_settings_path.read_text())

# Load active model from DB registry for correct credentials.
# Falls back to settings.json if DB is unavailable.
_DB_MODEL_KWARGS: dict = {}
try:
    from config.settings import load_settings as _ls

    _s = _ls()
    if _s.database.url:
        from db.engine import initialize_db as _idb
        from server.app_factory import (
            agent_run_store as _ars,
            model_store as _ms,
            task_center_store as _tcs,
        )

        # Initialise *all* DB-backed singletons up front so EvalAgent-driven
        # tests (and the local factories that bypass EvalAgent.create()) get
        # the same persistence setup the production server bootstrap provides.
        if not _ms.is_available or not _ars.is_ready or not _tcs.is_ready:
            _sf = _idb(_s.database)
            if _sf:
                if not _ms.is_available:
                    _ms.initialize(_sf)
                if not _ars.is_ready:
                    _ars.initialize(_sf)
                if not _tcs.is_ready:
                    _tcs.initialize(_sf)
        if _ms.is_available:
            _active = _ms.get_active_resolved()
            if _active:
                _DB_MODEL_KWARGS = _active.get("kwargs", {})
except Exception:
    pass

MINIMAX_KEY = _DB_MODEL_KWARGS.get("api_key") or os.environ.get("MINIMAX_API_KEY") or ""
MINIMAX_MODEL = (
    _DB_MODEL_KWARGS.get("model")
    or os.environ.get("MINIMAX_MODEL")
    or "MiniMax-M2.7"
)
MINIMAX_BASE_URL = (
    _DB_MODEL_KWARGS.get("base_url") or os.environ.get("MINIMAX_BASE_URL") or ""
)
# All e2e tests use an Anthropic-compatible endpoint.
MINIMAX_FORMAT = "anthropic"

ANTHROPIC_MINIMAX_KEY = MINIMAX_KEY
ANTHROPIC_MINIMAX_MODEL = MINIMAX_MODEL
ANTHROPIC_MINIMAX_BASE_URL = MINIMAX_BASE_URL
ANTHROPIC_MINIMAX_FORMAT = "anthropic"

DAYTONA_KEY = os.environ.get("DAYTONA_API_KEY") or _LIVE_SETTINGS.get("daytona_api_key", "")
DAYTONA_URL = os.environ.get("DAYTONA_API_URL") or _LIVE_SETTINGS.get("daytona_api_url", "")
DAYTONA_TARGET = os.environ.get("DAYTONA_TARGET") or _LIVE_SETTINGS.get("daytona_target", "")

HAS_MINIMAX = bool(MINIMAX_KEY and MINIMAX_BASE_URL)
HAS_ANTHROPIC_MINIMAX = HAS_MINIMAX
HAS_BOTH = HAS_MINIMAX and HAS_DAYTONA
HAS_ANTHROPIC_AND_DAYTONA = HAS_MINIMAX and HAS_DAYTONA


def _postgres_test_database_url() -> str:
    raw_url = os.environ.get("EPHEMERALOS_TEST_DATABASE_URL") or os.environ.get(
        "EPHEMERALOS_DATABASE_URL"
    )
    if not raw_url:
        pytest.skip(
            "PostgreSQL e2e tests require EPHEMERALOS_TEST_DATABASE_URL "
            "or EPHEMERALOS_DATABASE_URL."
        )
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        pytest.skip("E2E database URL must use PostgreSQL.")
    if url.drivername in {"postgresql", "postgresql+psycopg2"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def _database_url_from_session_factory(factory) -> str:
    bind = factory.kw.get("bind")
    if bind is None:
        return _postgres_test_database_url()
    return bind.url.render_as_string(hide_password=False)


def _patch_server_database(monkeypatch, session_factory) -> None:
    def _ensure_runtime_stores_ready(*args, **kwargs):
        from server import app_factory as _af

        for store in (
            _af.task_center_store,
            _af.agent_run_store,
            _af.model_store,
        ):
            store.initialize(session_factory)
        return session_factory

    monkeypatch.setattr("db.engine.initialize_db", lambda *a, **kw: session_factory)
    monkeypatch.setattr("db.engine.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("server.app_factory.initialize_db", lambda *a, **kw: session_factory)
    monkeypatch.setattr("server.app_factory.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("server.app_factory.ensure_runtime_stores_ready", _ensure_runtime_stores_ready)


def make_live_client(
    db_session_factory,
    tmp_path,
    monkeypatch,
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
):
    """Create a TestClient configured with real API credentials (compat)."""
    from fastapi.testclient import TestClient
    from server.protocol import BackendHostConfig
    from server.app_factory import create_app

    api_key = api_key or MINIMAX_KEY
    model = model or MINIMAX_MODEL
    base_url = base_url or MINIMAX_BASE_URL

    for _var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]:
        monkeypatch.delenv(_var, raising=False)
    monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    if DAYTONA_KEY:
        monkeypatch.setenv("DAYTONA_API_KEY", DAYTONA_KEY)
    if DAYTONA_URL:
        monkeypatch.setenv("DAYTONA_API_URL", DAYTONA_URL)
    if DAYTONA_TARGET:
        monkeypatch.setenv("DAYTONA_TARGET", DAYTONA_TARGET)

    _patch_server_database(monkeypatch, db_session_factory)

    def _patched_load_settings(*a, **kw):
        from config.settings import Settings as _S, DatabaseSettings as _DS

        return _S(
            daytona_api_key=DAYTONA_KEY,
            daytona_api_url=DAYTONA_URL,
            daytona_target=DAYTONA_TARGET,
            database=_DS(url=_database_url_from_session_factory(db_session_factory)),
        )

    monkeypatch.setattr("config.load_settings", _patched_load_settings)
    monkeypatch.setattr("config.settings.load_settings", _patched_load_settings)
    monkeypatch.setattr("server.app_factory.load_settings", _patched_load_settings)

    # Seed active model registration for this test DB so DB-sourced model
    # resolution finds credentials.
    def _seed_model(sf):
        from db.stores.model_store import ModelStore as _MS

        _s = _MS()
        _s.initialize(sf)
        _s.register(
            key="test_minimax",
            label="test_minimax",
            class_path="anthropic",
            kwargs={
                "model": model,
                "api_key": api_key,
                "base_url": base_url or None,
                "max_tokens": 16384,
            },
            activate=True,
        )

    _seed_model(db_session_factory)

    config = BackendHostConfig()
    app = create_app(config)
    return TestClient(app)


def send_chat(
    client,
    line: str,
    *,
    sandbox_id: str | None = None,
    timeout: int = 180,
    verbose: bool = True,
) -> list[dict]:
    """Send a chat message and return parsed SSE events (compat)."""
    payload: dict[str, Any] = {"line": line}
    if sandbox_id:
        payload["sandbox_id"] = sandbox_id

    if verbose:
        print(f"  [send_chat] prompt: {line[:80]}", flush=True)

    resp = client.post("/api/chat", json=payload, timeout=timeout)
    assert resp.status_code == 200, f"Chat failed: {resp.status_code} {resp.text[:500]}"
    events = parse_sse_events(resp.text)

    if verbose:
        _print_sse_events(events)

    return events


def _print_sse_events(events: list[dict]) -> None:
    """Print parsed SSE events for real-time test visibility."""
    for evt in events:
        etype = evt.get("type", "")
        if etype == "assistant_delta":
            text = evt.get("message", evt.get("text", ""))
            if text:
                print(f"    [text] {text}", flush=True)
        elif etype == "tool_started":
            name = evt.get("tool_name", "?")
            inp = evt.get("tool_input", {})
            print(f"    -> tool_start: {name}({inp})", flush=True)
        elif etype == "tool_completed":
            name = evt.get("tool_name", "?")
            is_err = evt.get("is_error", False)
            output = evt.get("output", "")
            status = "ERROR" if is_err else "ok"
            print(f"    <- tool_done:  {name} [{status}] {output}", flush=True)
        elif etype == "assistant_complete":
            thinking = evt.get("thinking", "")
            if thinking:
                print(f"    [thinking] {thinking[:500]}", flush=True)
            print("    [assistant_complete]", flush=True)


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class MockApiClient:
    """Deterministic mock that captures what tools/system_prompt the engine sends."""

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

        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                yield ApiThinkingDeltaEvent(text=block.text)

        for block in msg.content:
            if isinstance(block, TextBlock):
                yield ApiTextDeltaEvent(text=block.text)

        yield ApiMessageCompleteEvent(
            message=msg,
            usage=UsageSnapshot(input_tokens=100, output_tokens=50),
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session_factory(tmp_path):
    """Create an isolated PostgreSQL schema with all tables."""
    base_url = _postgres_test_database_url()
    schema_name = f"ephemeralos_test_{uuid4().hex}"
    admin_engine = create_engine(base_url, echo=False)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    engine = create_engine(
        base_url,
        echo=False,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )

    import db.models  # noqa: F401

    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sf
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


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
# App + TestClient fixture (for mock tests)
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client(db_session_factory, mock_api_client, tmp_path, monkeypatch):
    """Create a FastAPI TestClient with real DB and mock LLM."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)

    _patch_server_database(monkeypatch, db_session_factory)
    monkeypatch.setattr("providers.provider.make_api_client", lambda *a, **kw: mock_api_client)
    monkeypatch.setattr(
        "prompt.build_runtime_system_prompt",
        lambda *a, **kw: "You are a test assistant.",
    )

    def _patched_load_settings(*a, **kw):
        from config.settings import Settings, DatabaseSettings

        return Settings(
            database=DatabaseSettings(url=_database_url_from_session_factory(db_session_factory)),
        )

    monkeypatch.setattr("config.load_settings", _patched_load_settings)
    monkeypatch.setattr("config.settings.load_settings", _patched_load_settings)
    monkeypatch.setattr("server.app_factory.load_settings", _patched_load_settings)

    # Seed active model registration so DB-based model resolution works.
    from db.stores.model_store import ModelStore as _MS

    _ms = _MS()
    _ms.initialize(db_session_factory)
    _ms.register(
        key="test_mock",
        label="test_mock",
        class_path="anthropic",
        kwargs={
            "model": "claude-sonnet-4-20250514",
            "api_key": "test-api-key",
            "base_url": None,
            "max_tokens": 16384,
        },
        activate=True,
    )

    from server.protocol import BackendHostConfig
    from server.app_factory import create_app

    config = BackendHostConfig(api_client=mock_api_client)
    app = create_app(config)

    with TestClient(app) as client:
        yield client, mock_api_client


from sandbox.testing import (
    EVAL_SANDBOX_FILES,
    create_test_sandbox,
    delete_test_sandbox,
    get_sandbox_service,
    populate_sandbox_files,
)


# ---------------------------------------------------------------------------
# SSE parsing helpers (for tests that still use TestClient)
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


def get_assistant_text(events: list[dict]) -> str:
    """Extract assistant text from SSE events."""
    import re

    parts: list[str] = []
    for evt in events_of_type(events, "assistant_complete"):
        msg = evt.get("message", "")
        if msg:
            parts.append(msg)

    if not parts:
        for evt in events_of_type(events, "assistant_delta"):
            msg = evt.get("message", "")
            if msg:
                parts.append(msg)

    text = "\n".join(parts)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return text


def get_event_types(events: list[dict]) -> set[str]:
    return {e["type"] for e in events}


def get_tool_started_events(events: list[dict]) -> list[dict]:
    return events_of_type(events, "tool_started")


def get_tool_completed_events(events: list[dict]) -> list[dict]:
    return events_of_type(events, "tool_completed")


def get_tool_cancelled_events(events: list[dict]) -> list[dict]:
    return events_of_type(events, "tool_cancelled")
