"""Shell staleness semantics for sandbox-runtime snapshot layer stacks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from sandbox.layer_stack import LayerChange, LayerStackManager
from sandbox.occ.content.hashing import ContentHasher
from sandbox.overlay.capture.types import OverlayCapture
from sandbox.overlay.runner.runtime_invoker import RuntimeInvoker
from sandbox.overlay.runner.snapshot_overlay_runner import OverlayShellRequest
from sandbox.runtime import api_handlers


class _BlockingRuntimeInvoker:
    """Pause after snapshot lease acquisition so the test can advance active."""

    def __init__(self, storage_root: Path) -> None:
        self._inner = RuntimeInvoker(storage_root=storage_root)
        self.started = asyncio.Event()
        self.released = asyncio.Event()
        self.snapshot_version: int | None = None

    async def invoke(
        self,
        *,
        request: OverlayShellRequest,
        manifest,
    ) -> OverlayCapture:
        self.snapshot_version = manifest.version
        self.started.set()
        await asyncio.wait_for(self.released.wait(), timeout=10)
        return await self._inner.invoke(request=request, manifest=manifest)

    def release(self) -> None:
        self.released.set()


@dataclass(frozen=True)
class _StaleShellRun:
    manager: LayerStackManager
    result: dict[str, object]
    snapshot_version: int
    active_version_before_release: int

    @property
    def manifest_lag(self) -> int:
        return self.active_version_before_release - self.snapshot_version


@pytest.mark.parametrize("advance_count", (1, 2, 4, 5, 6, 10, 20))
async def test_shell_accepts_occ_clean_write_after_manifest_advances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    advance_count: int,
) -> None:
    run = await _run_occ_clean_stale_shell(
        tmp_path,
        monkeypatch=monkeypatch,
        advance_count=advance_count,
    )

    assert run.snapshot_version == 1
    assert run.manifest_lag == advance_count
    assert run.result["success"] is True
    assert run.result["status"] == "ok"
    assert run.result["changed_paths"] == ["generated/output.json"]
    assert run.result["stdout"] == "done\n"
    assert run.manager.read_text("generated/output.json") == ("value: v1\n", True)


async def _run_occ_clean_stale_shell(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    advance_count: int,
) -> _StaleShellRun:
    manager = LayerStackManager(tmp_path / f"stack-{uuid4().hex}")
    _publish(manager, tmp_path, "config.yaml", b"value: v1\n")
    invoker = _BlockingRuntimeInvoker(manager.storage_root)
    monkeypatch.setattr(api_handlers, "RuntimeInvoker", lambda **_kwargs: invoker)

    task = asyncio.create_task(
        api_handlers.shell(
            {
                "layer_stack_root": str(manager.storage_root),
                "command": (
                    "mkdir -p generated; "
                    "cp config.yaml generated/output.json; "
                    "printf 'done\\n'"
                ),
                "cwd": ".",
                "timeout_seconds": 10,
                "actor_id": "agent-staleness",
                "description": "staleness clean write",
                "ignored_paths": [],
            }
        )
    )
    try:
        await asyncio.wait_for(invoker.started.wait(), timeout=5)
        if invoker.snapshot_version is None:
            raise AssertionError("blocked invoker did not record snapshot version")
        for index in range(advance_count):
            _publish(
                manager,
                tmp_path,
                f"unrelated/{advance_count}/{index}.txt",
                f"unrelated-{index}\n".encode(),
            )
        active_version = manager.read_active_manifest().version
        invoker.release()
        result = await asyncio.wait_for(task, timeout=10)
        return _StaleShellRun(
            manager=manager,
            result=result,
            snapshot_version=invoker.snapshot_version,
            active_version_before_release=active_version,
        )
    finally:
        invoker.release()
        if not task.done():
            task.cancel()


def _source(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _publish(
    manager: LayerStackManager,
    tmp_path: Path,
    rel: str,
    content: bytes,
) -> None:
    source = _source(tmp_path, rel.replace("/", "-"), content)
    manager.publish_changes(
        [
            LayerChange(
                path=rel,
                kind="write",
                content_hash=ContentHasher().hash_bytes(content),
                source_path=str(source),
            )
        ]
    )
