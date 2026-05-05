"""Shell staleness semantics for per-call snapshot layer stacks."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from sandbox.api import SandboxCaller, ShellRequest, ShellResult
from sandbox.api.tool.shell import shell
from sandbox.layer_stack import LayerChange, LayerStackManager
from sandbox.occ.client import dispose_occ_service, register_occ_service
from sandbox.occ.content.hashing import ContentHasher
from sandbox.occ.service import OccService
from sandbox.overlay.client import (
    OverlayClient,
    dispose_overlay_client,
    register_overlay_client,
)
from sandbox.overlay.runner.runtime_invoker import RuntimeInvoker
from sandbox.overlay.runner.snapshot_overlay_runner import (
    OverlayShellRequest,
    SnapshotOverlayRunner,
)
from sandbox.overlay.capture.types import OverlayCapture


class _Gitignore:
    def is_ignored(self, path: str) -> bool:
        del path
        return False


class _BlockingInvoker:
    """Pause after snapshot lease acquisition so the test can advance active."""

    def __init__(self, storage_root: Path) -> None:
        self._inner = RuntimeInvoker(storage_root=storage_root)
        self.started = threading.Event()
        self.released = threading.Event()
        self.snapshot_version: int | None = None

    def invoke_sync(
        self,
        *,
        request: OverlayShellRequest,
        manifest,
    ) -> OverlayCapture:
        self.snapshot_version = manifest.version
        self.started.set()
        if not self.released.wait(timeout=10):
            raise TimeoutError("test did not release blocked shell invoker")
        return self._inner.invoke_sync(request=request, manifest=manifest)

    def release(self) -> None:
        self.released.set()


@dataclass(frozen=True)
class _StaleShellRun:
    manager: LayerStackManager
    result: ShellResult
    snapshot_version: int
    active_version_before_release: int

    @property
    def manifest_lag(self) -> int:
        return self.active_version_before_release - self.snapshot_version


@pytest.mark.parametrize("advance_count", (1, 2, 4, 5, 6, 10, 20))
async def test_shell_accepts_occ_clean_write_after_manifest_advances(
    tmp_path: Path,
    advance_count: int,
) -> None:
    run = await _run_occ_clean_stale_shell(tmp_path, advance_count=advance_count)

    assert run.snapshot_version == 1
    assert run.manifest_lag == advance_count
    assert run.result.success is True
    assert run.result.status == "ok"
    assert run.result.changed_paths == ("generated/output.json",)
    assert run.result.stdout == "done\n"
    assert run.manager.read_text("generated/output.json") == ("value: v1\n", True)

async def _run_occ_clean_stale_shell(
    tmp_path: Path,
    *,
    advance_count: int,
) -> _StaleShellRun:
    manager = LayerStackManager(tmp_path / f"stack-{uuid4().hex}")
    _publish(manager, tmp_path, "config.yaml", b"value: v1\n")
    invoker = _BlockingInvoker(manager.storage_root)
    sandbox_id = f"sb-staleness-{uuid4().hex}"
    register_occ_service(
        sandbox_id,
        OccService(gitignore=_Gitignore(), layer_stack=manager),
    )
    register_overlay_client(
        sandbox_id,
        OverlayClient(runner=SnapshotOverlayRunner(manager, invoker=invoker)),
    )
    task: asyncio.Task[ShellResult] | None = None
    try:
        task = asyncio.create_task(
            shell(
                sandbox_id,
                ShellRequest(
                    command=(
                        "mkdir -p generated; "
                        "cp config.yaml generated/output.json; "
                        "printf 'done\\n'"
                    ),
                    cwd=".",
                    timeout=10,
                    caller=SandboxCaller(agent_id="agent-staleness"),
                    description="staleness clean write",
                ),
            )
        )
        await _wait_for_started(invoker)
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
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        dispose_overlay_client(sandbox_id)
        dispose_occ_service(sandbox_id)


async def _wait_for_started(invoker: _BlockingInvoker) -> None:
    deadline = time.perf_counter() + 5
    while time.perf_counter() < deadline:
        if invoker.started.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("shell invoker did not start")


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
