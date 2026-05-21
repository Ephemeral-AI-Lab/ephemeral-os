"""Unit tests for LSP Pyright session freshness behavior."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plugins.catalog.lsp.runtime import server as lsp_server
from plugins.catalog.lsp.runtime import session_manager
from plugins.catalog.lsp.runtime.pyright_session import PyrightSession


@pytest.fixture(autouse=True)
def _clear_session_manager_cache() -> Iterator[None]:
    session_manager._sessions.clear()
    session_manager._locks.clear()
    for task in session_manager._event_tasks.values():
        task.cancel()
    session_manager._event_tasks.clear()
    session_manager._event_subscriptions.clear()
    yield
    session_manager._sessions.clear()
    session_manager._locks.clear()
    for task in session_manager._event_tasks.values():
        task.cancel()
    session_manager._event_tasks.clear()
    session_manager._event_subscriptions.clear()


class _Overlay:
    def __init__(self, *, workspace_root: str, manifest_key: str) -> None:
        self.workspace_root = workspace_root
        self.manifest_key = manifest_key
        self.ensure_count = 0
        self.reasons: list[str] = []

    async def ensure_current(self, *, reason: str = "ensure_current") -> str:
        self.ensure_count += 1
        self.reasons.append(reason)
        return self.manifest_key

    def active_manifest_key(self) -> str:
        return self.manifest_key


@dataclass(frozen=True)
class _Caller:
    agent_run_id: str = "run"
    agent_id: str = "agent"


@dataclass(frozen=True)
class _Ctx:
    layer_stack_root: str
    overlay: _Overlay
    caller: _Caller = _Caller()
    metadata: dict[str, Any] | None = None


class _FakeSession:
    def __init__(
        self,
        *,
        manifest_key: str,
        workspace_root: str,
    ) -> None:
        self.manifest_key = manifest_key
        self.workspace_root = workspace_root
        self.refresh_count = 0
        self.evict_count = 0

    async def refresh_manifest(self, *, manifest_key: str) -> None:
        self.refresh_count += 1
        self.manifest_key = manifest_key

    async def evict(self) -> None:
        self.evict_count += 1


class _StartableFakeSession(_FakeSession):
    def __init__(
        self,
        *,
        manifest_key: str,
        workspace_root: str,
    ) -> None:
        super().__init__(manifest_key=manifest_key, workspace_root=workspace_root)
        self.start_count = 0

    async def start(self) -> None:
        self.start_count += 1


@pytest.mark.asyncio
async def test_session_manager_ensures_overlay_current_on_every_tool_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(session_manager, "PyrightSession", _FakeSession)

    overlay = _Overlay(workspace_root="/testbed", manifest_key="hash-a@1")
    ctx = _Ctx(
        layer_stack_root=str(tmp_path / "layer-stack"),
        overlay=overlay,
        metadata={"op_name": "hover"},
    )

    session = await session_manager.get_session(ctx)
    overlay.manifest_key = "hash-b@2"
    refreshed = await session_manager.get_session(ctx)

    assert refreshed is session
    assert refreshed.manifest_key == "hash-b@2"
    assert refreshed.refresh_count == 1
    assert overlay.ensure_count == 2
    assert overlay.reasons == ["lsp:hover:enter", "lsp:hover:enter"]


@pytest.mark.asyncio
async def test_session_manager_restarts_when_workspace_root_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(session_manager, "PyrightSession", _FakeSession)

    first_overlay = _Overlay(workspace_root="/testbed", manifest_key="hash-a@1")
    second_overlay = _Overlay(workspace_root="/workspace", manifest_key="hash-a@1")
    first = await session_manager.get_session(
        _Ctx(layer_stack_root=str(tmp_path / "stack"), overlay=first_overlay)
    )
    second = await session_manager.get_session(
        _Ctx(layer_stack_root=str(tmp_path / "stack"), overlay=second_overlay)
    )

    assert second is not first
    assert first.evict_count == 1
    assert second.workspace_root == "/workspace"


@pytest.mark.asyncio
async def test_lsp_runtime_warm_hook_starts_cached_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(session_manager, "PyrightSession", _StartableFakeSession)

    overlay = _Overlay(workspace_root="/testbed", manifest_key="hash-a@1")
    ctx = _Ctx(
        layer_stack_root=str(tmp_path / "layer-stack"),
        overlay=overlay,
        metadata={"op_name": "__warm__"},
    )

    result = await lsp_server.warm_plugin_runtime({}, ctx)
    session = await session_manager.get_session(ctx)

    assert result == {"success": True, "manifest_key": "hash-a@1"}
    assert isinstance(session, _StartableFakeSession)
    assert session.start_count == 1


class _Client:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        self.notifications.append((method, params))


@pytest.mark.asyncio
async def test_pyright_session_refresh_notifies_open_docs(tmp_path: Path) -> None:
    workspace = tmp_path / "testbed"
    (workspace / "pkg").mkdir(parents=True)
    module = workspace / "pkg" / "mod.py"
    module.write_text("value = 1\n", encoding="utf-8")
    session = PyrightSession(
        manifest_key="hash-a@1",
        workspace_root=str(workspace),
    )
    client = _Client()
    session._client = client  # type: ignore[assignment]
    session._started = True
    uri = await session._open_document("pkg/mod.py")
    client.notifications.clear()

    module.write_text("value = 2\n", encoding="utf-8")
    await session.refresh_manifest(manifest_key="hash-b@2")

    assert session.manifest_key == "hash-b@2"
    assert ("workspace/didChangeWatchedFiles", {"changes": [{"uri": session._workspace_uri(), "type": 2}]}) in client.notifications
    did_change = [
        params
        for method, params in client.notifications
        if method == "textDocument/didChange"
    ]
    assert did_change
    assert did_change[-1]["contentChanges"] == [{"text": "value = 2\n"}]
    assert uri in session._opened


@pytest.mark.asyncio
async def test_pyright_session_diagnostics_pulls_current_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "testbed"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    session = PyrightSession(
        manifest_key="hash-a@1",
        workspace_root=str(workspace),
    )
    session._client = _Client()  # type: ignore[assignment]
    session._started = True
    pulled = [
        {
            "message": "Operator '+' not supported",
            "range": {"start": {"line": 1, "character": 11}},
        }
    ]

    async def _send_request(
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        assert method == "textDocument/diagnostic"
        assert params["textDocument"]["uri"].endswith("/pkg/mod.py")
        return {"items": pulled, "kind": "full", "resultId": "1"}

    monkeypatch.setattr(session, "_send_request", _send_request)

    result = await session.diagnostics({"file_path": "pkg/mod.py"})

    assert result == {
        "diagnostics": pulled,
        "kind": "full",
        "result_id": "1",
    }
    assert session._to_uri("pkg/mod.py") in session._opened
