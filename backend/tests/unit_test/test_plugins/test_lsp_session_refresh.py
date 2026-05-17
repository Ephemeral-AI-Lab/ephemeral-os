"""Unit tests for LSP Pyright session refresh behavior."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plugins.catalog.lsp.runtime import session_manager
from plugins.catalog.lsp.runtime import server as lsp_server
from plugins.catalog.lsp.runtime.pyright_session import (
    PyrightSession,
    PyrightSpawnError,
)


@pytest.fixture(autouse=True)
def _clear_session_manager_cache() -> Iterator[None]:
    session_manager._sessions.clear()
    session_manager._locks.clear()
    yield
    session_manager._sessions.clear()
    session_manager._locks.clear()


@dataclass
class _Handle:
    manifest_key: str
    lowerdir: str
    lease_id: str = "lease"
    release_count: int = 0

    def release(self) -> None:
        self.release_count += 1


class _Projection:
    def __init__(self, handle: _Handle) -> None:
        self.handle = handle
        self.acquire_count = 0

    def active_manifest_key(self) -> str:
        return self.handle.manifest_key

    def acquire(self, owner_request_id: str) -> _Handle:
        del owner_request_id
        self.acquire_count += 1
        return self.handle


@dataclass(frozen=True)
class _Caller:
    agent_run_id: str = "run"
    agent_id: str = "agent"


@dataclass(frozen=True)
class _Ctx:
    layer_stack_root: str
    projection: _Projection
    caller: _Caller = _Caller()
    metadata: dict[str, Any] | None = None


class _FakeSession:
    def __init__(
        self,
        *,
        manifest_key: str,
        lowerdir: str,
        workspace_root: str,
        projection_handle: _Handle,
        stable_root: str,
    ) -> None:
        del stable_root
        self.manifest_key = manifest_key
        self.lowerdir = lowerdir
        self.workspace_root = workspace_root
        self.projection_handle = projection_handle
        self.refresh_count = 0
        self.evict_count = 0

    async def refresh_manifest(
        self,
        *,
        manifest_key: str,
        lowerdir: str,
        projection_handle: _Handle,
    ) -> None:
        self.refresh_count += 1
        self.manifest_key = manifest_key
        self.lowerdir = lowerdir
        self.projection_handle = projection_handle

    async def evict(self) -> None:
        self.evict_count += 1


class _StartableFakeSession(_FakeSession):
    def __init__(
        self,
        *,
        manifest_key: str,
        lowerdir: str,
        workspace_root: str,
        projection_handle: _Handle,
        stable_root: str,
    ) -> None:
        super().__init__(
            manifest_key=manifest_key,
            lowerdir=lowerdir,
            workspace_root=workspace_root,
            projection_handle=projection_handle,
            stable_root=stable_root,
        )
        self.start_count = 0

    async def start(self) -> None:
        self.start_count += 1


@pytest.mark.asyncio
async def test_session_manager_refreshes_on_manifest_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(session_manager, "PyrightSession", _FakeSession)

    first_handle = _Handle("hash-a@1", str(tmp_path / "lower-a"))
    projection = _Projection(first_handle)
    ctx = _Ctx(
        layer_stack_root=str(tmp_path / "layer-stack"),
        projection=projection,
        metadata={"workspace_root": "/testbed"},
    )

    session = await session_manager.get_session(ctx)
    second_handle = _Handle("hash-b@2", str(tmp_path / "lower-b"))
    projection.handle = second_handle

    refreshed = await session_manager.get_session(ctx)

    assert refreshed is session
    assert refreshed.manifest_key == "hash-b@2"
    assert refreshed.lowerdir == str(tmp_path / "lower-b")
    assert refreshed.refresh_count == 1
    assert refreshed.evict_count == 0
    assert projection.acquire_count == 2


@pytest.mark.asyncio
async def test_session_manager_restarts_when_workspace_root_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(session_manager, "PyrightSession", _FakeSession)

    handle = _Handle("hash-a@1", str(tmp_path / "lower-a"))
    projection = _Projection(handle)
    ctx = _Ctx(
        layer_stack_root=str(tmp_path / "layer-stack"),
        projection=projection,
        metadata={"workspace_root": ""},
    )

    first = await session_manager.get_session(ctx)
    second = await session_manager.get_session(
        _Ctx(
            layer_stack_root=ctx.layer_stack_root,
            projection=projection,
            metadata={"workspace_root": "/ephemeral-os"},
        )
    )

    assert second is not first
    assert first.evict_count == 1
    assert second.workspace_root == "/ephemeral-os"
    assert second.manifest_key == "hash-a@1"
    assert projection.acquire_count == 2


@pytest.mark.asyncio
async def test_lsp_runtime_warm_hook_starts_cached_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(session_manager, "PyrightSession", _StartableFakeSession)

    handle = _Handle("hash-a@1", str(tmp_path / "lower-a"))
    ctx = _Ctx(
        layer_stack_root=str(tmp_path / "layer-stack"),
        projection=_Projection(handle),
        metadata={"workspace_root": "/testbed"},
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
async def test_pyright_session_retargets_stable_root_and_notifies_open_docs(
    tmp_path: Path,
) -> None:
    lower_a = tmp_path / "lower-a"
    lower_b = tmp_path / "lower-b"
    (lower_a / "pkg").mkdir(parents=True)
    (lower_b / "pkg").mkdir(parents=True)
    (lower_a / "pkg" / "mod.py").write_text("value = 1\n", encoding="utf-8")
    (lower_b / "pkg" / "mod.py").write_text("value = 2\n", encoding="utf-8")

    handle_a = _Handle("hash-a@1", str(lower_a))
    handle_b = _Handle("hash-b@2", str(lower_b))
    stable_root = tmp_path / "stable" / "root"
    session = PyrightSession(
        manifest_key=handle_a.manifest_key,
        lowerdir=handle_a.lowerdir,
        workspace_root="/testbed",
        projection_handle=handle_a,
        stable_root=str(stable_root),
    )
    client = _Client()
    session._client = client  # type: ignore[assignment]
    session._started = True
    uri = session._mapper.to_snapshot_uri("pkg/mod.py")
    session._opened.add(uri)

    await session.refresh_manifest(
        manifest_key=handle_b.manifest_key,
        lowerdir=handle_b.lowerdir,
        projection_handle=handle_b,
    )

    assert session.manifest_key == "hash-b@2"
    assert os.readlink(stable_root) == str(lower_b)
    assert handle_a.release_count == 1
    assert handle_b.release_count == 0
    assert session._read_document_text("pkg/mod.py") == "value = 2\n"
    assert ("workspace/didChangeWatchedFiles", {"changes": [{"uri": f"file://{stable_root}", "type": 2}]}) in client.notifications
    did_change = [
        params
        for method, params in client.notifications
        if method == "textDocument/didChange"
    ]
    assert did_change
    assert did_change[-1]["contentChanges"] == [{"text": "value = 2\n"}]


@pytest.mark.asyncio
async def test_pyright_session_retarget_keeps_unchanged_open_doc_cached(
    tmp_path: Path,
) -> None:
    lower_a = tmp_path / "lower-a"
    lower_b = tmp_path / "lower-b"
    (lower_a / "pkg").mkdir(parents=True)
    (lower_b / "pkg").mkdir(parents=True)
    (lower_a / "pkg" / "mod.py").write_text("value = 1\n", encoding="utf-8")
    (lower_b / "pkg" / "mod.py").write_text("value = 1\n", encoding="utf-8")

    handle_a = _Handle("hash-a@1", str(lower_a))
    handle_b = _Handle("hash-b@2", str(lower_b))
    session = PyrightSession(
        manifest_key=handle_a.manifest_key,
        lowerdir=handle_a.lowerdir,
        workspace_root="/testbed",
        projection_handle=handle_a,
        stable_root=str(tmp_path / "stable" / "root"),
    )
    client = _Client()
    session._client = client  # type: ignore[assignment]
    session._started = True
    uri = await session._open_document("pkg/mod.py")
    client.notifications.clear()

    await session.refresh_manifest(
        manifest_key=handle_b.manifest_key,
        lowerdir=handle_b.lowerdir,
        projection_handle=handle_b,
    )

    assert [
        method
        for method, _params in client.notifications
    ] == ["workspace/didChangeWatchedFiles"]
    assert uri in session._opened


@pytest.mark.asyncio
async def test_pyright_session_diagnostics_returns_pull_response_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower = tmp_path / "lower"
    lower.mkdir()
    session = PyrightSession(
        manifest_key="hash-a@1",
        lowerdir=str(lower),
        workspace_root="/testbed",
        projection_handle=_Handle("hash-a@1", str(lower)),
        stable_root=str(tmp_path / "stable" / "root"),
    )
    uri = session._mapper.to_snapshot_uri("pkg/mod.py")

    async def fake_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "textDocument/diagnostic"
        assert params == {"textDocument": {"uri": uri}}
        return {"kind": "full", "items": []}

    monkeypatch.setattr(session, "_send_request", fake_request)

    assert await session._pull_diagnostics(uri) == {
        "diagnostics": [],
        "kind": "full",
    }


@pytest.mark.asyncio
async def test_pyright_session_diagnostics_pulls_current_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lower = tmp_path / "lower"
    (lower / "pkg").mkdir(parents=True)
    (lower / "pkg" / "mod.py").write_text(
        "def f(x: int) -> int:\n"
        "    return x + 'bad'\n",
        encoding="utf-8",
    )
    session = PyrightSession(
        manifest_key="hash-a@1",
        lowerdir=str(lower),
        workspace_root="/testbed",
        projection_handle=_Handle("hash-a@1", str(lower)),
        stable_root=str(tmp_path / "stable" / "root"),
    )
    client = _Client()
    session._client = client  # type: ignore[assignment]
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

    result = await session.diagnostics({"file_path": "/testbed/pkg/mod.py"})

    assert result == {
        "diagnostics": pulled,
        "kind": "full",
        "result_id": "1",
    }
    assert session._mapper.to_snapshot_uri("pkg/mod.py") in session._opened


@pytest.mark.asyncio
async def test_pyright_session_fails_closed_when_spawn_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lower = tmp_path / "lower"
    (lower / "pkg").mkdir(parents=True)
    (lower / "pkg" / "model.py").write_text(
        "class UserProfile:\n"
        "    pass\n\n"
        "def display_name(profile: UserProfile) -> str:\n"
        "    return missing_value\n",
        encoding="utf-8",
    )
    session = PyrightSession(
        manifest_key="hash-a@1",
        lowerdir=str(lower),
        workspace_root="/testbed",
        projection_handle=_Handle("hash-a@1", str(lower)),
        stable_root=str(tmp_path / "stable" / "root"),
    )

    async def _fail_spawn() -> None:
        raise PyrightSpawnError("missing pyright")

    monkeypatch.setattr(session, "_spawn", _fail_spawn)

    with pytest.raises(PyrightSpawnError, match="missing pyright"):
        await session.hover(
            {"file_path": "/testbed/pkg/model.py", "line": 3, "character": 4}
        )
    assert session._started is False
