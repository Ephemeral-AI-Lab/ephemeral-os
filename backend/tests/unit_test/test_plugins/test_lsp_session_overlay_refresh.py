"""LSP session overlay subscription and remount failure contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plugins.catalog.lsp.runtime import pyright_session as pyright_session_module
from plugins.catalog.lsp.runtime import session_manager
from plugins.catalog.lsp.runtime.pyright_session import (
    PyrightOverlayRefreshError,
    PyrightSession,
)


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


@dataclass(frozen=True)
class _Ctx:
    layer_stack_root: str
    overlay: Any
    metadata: dict[str, Any] | None = None


class _Overlay:
    workspace_root = "/testbed"

    def __init__(self, manifest_key: str = "root@1") -> None:
        self.manifest_key = manifest_key
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.queue: asyncio.Queue[Any] = asyncio.Queue()

    async def ensure_current(self, *, reason: str = "ensure_current") -> str:
        del reason
        return self.manifest_key

    def active_manifest_key(self) -> str:
        return self.manifest_key

    def acquire_operation_overlay(self, *, invocation_id: str, workspace_root: str | None = None) -> Any:
        del invocation_id
        return SimpleNamespace(
            manifest_key=self.manifest_key,
            workspace_root=workspace_root or self.workspace_root,
            layer_paths=("/layers/L1",),
            release=lambda: None,
        )

    def subscribe_workspace_changes(self, subscriber_id: str) -> asyncio.Queue[Any]:
        self.subscribed.append(subscriber_id)
        return self.queue

    def unsubscribe_workspace_changes(self, subscriber_id: str) -> None:
        self.unsubscribed.append(subscriber_id)


class _FakeSession:
    def __init__(
        self,
        *,
        manifest_key: str,
        workspace_root: str,
        overlay_handle: Any | None = None,
    ) -> None:
        self.manifest_key = manifest_key
        self.workspace_root = workspace_root
        self._overlay_handle = overlay_handle

    async def refresh_manifest(self, **_kwargs: Any) -> None:
        return None

    async def evict(self) -> None:
        release = getattr(self._overlay_handle, "release", None)
        if callable(release):
            release()


@pytest.mark.asyncio
async def test_lsp_subscribes_through_pipeline_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(session_manager, "PyrightSession", _FakeSession)
    first_overlay = _Overlay()
    second_overlay = _Overlay()
    root = str(tmp_path / "stack")

    first = await session_manager.get_session(
        _Ctx(layer_stack_root=root, overlay=first_overlay, metadata={"op_name": "hover"})
    )
    second = await session_manager.get_session(
        _Ctx(layer_stack_root=root, overlay=second_overlay, metadata={"op_name": "hover"})
    )

    assert second is first
    assert first_overlay.subscribed == [f"lsp:{root}"]
    assert first_overlay.unsubscribed == [f"lsp:{root}"]
    assert second_overlay.subscribed == [f"lsp:{root}"]
    assert second_overlay.unsubscribed == []


@pytest.mark.asyncio
async def test_namespace_remount_failure_is_loud(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old_handle = SimpleNamespace(
        layer_paths=("/layers/old",),
        release=lambda: None,
    )
    new_handle = SimpleNamespace(
        layer_paths=("/layers/new",),
        run_dir=(tmp_path / "run").as_posix(),
        upperdir=(tmp_path / "run" / "upper").as_posix(),
        workdir=(tmp_path / "run" / "work").as_posix(),
        release=lambda: None,
    )

    class _Proc:
        returncode = 126

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"failed to remount lsp namespace overlay: stuck mount"

    async def fake_subprocess(*_args: Any, **_kwargs: Any) -> _Proc:
        return _Proc()

    monkeypatch.setattr(
        pyright_session_module.asyncio,
        "create_subprocess_exec",
        fake_subprocess,
    )
    monkeypatch.setattr(
        pyright_session_module.shutil,
        "which",
        lambda name: "/usr/bin/nsenter" if name == "nsenter" else None,
    )
    session = PyrightSession(
        manifest_key="root@1",
        workspace_root="/testbed",
        overlay_handle=old_handle,
    )
    session._proc = SimpleNamespace(pid=4321)  # type: ignore[assignment]
    session._started = True

    with pytest.raises(PyrightOverlayRefreshError, match="stuck mount"):
        await session.refresh_manifest(
            manifest_key="root@2",
            overlay_handle=new_handle,
            workspace_root="/testbed",
        )

    assert session.manifest_key == "root@1"
    assert session._overlay_handle is old_handle
