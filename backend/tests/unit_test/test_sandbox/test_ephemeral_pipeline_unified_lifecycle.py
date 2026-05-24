"""Unit contracts for the foreground ephemeral workspace pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sandbox._shared.models import Intent, ToolCallRequest
from sandbox.ephemeral_workspace.pipeline import EphemeralPipeline
from sandbox.occ.changeset import ChangesetResult, FileResult, FileStatus
from sandbox.overlay.path_change import OverlayPathChange, content_hash


class _Manifest:
    version = 1
    layers = ()


class _Snapshot:
    lease_id = "lease-1"
    manifest_version = 1
    root_hash = "root"
    manifest = _Manifest()

    def __init__(self, tmp_path: Path) -> None:
        self.layer_paths = ((tmp_path / "lower").as_posix(),)


class _LayerStack:
    storage_root: Path

    def __init__(self, tmp_path: Path, order: list[str]) -> None:
        self.storage_root = tmp_path
        self._tmp_path = tmp_path
        self._order = order
        (tmp_path / "lower").mkdir(exist_ok=True)

    def prepare_workspace_snapshot(self, *, request_id: str) -> _Snapshot:
        assert request_id.startswith("overlay:")
        self._order.append("acquire")
        return _Snapshot(self._tmp_path)

    def release_lease(self, *, lease_id: str) -> bool:
        self._order.append(f"release:{lease_id}")
        return True

    def read_active_manifest(self) -> _Manifest:
        return _Manifest()


class _Occ:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.sources: list[str] = []
        self._apply_count = 0

    async def apply_changeset(self, changes, **_kwargs) -> ChangesetResult:
        self.order.append("commit")
        self.sources.extend(change.source for change in changes)
        self._apply_count += 1
        if self._apply_count == 1:
            return ChangesetResult(
                files=(FileResult(path="shared.txt", status=FileStatus.COMMITTED),),
                published_manifest_version=2,
            )
        return ChangesetResult(
            files=(
                FileResult(
                    path="shared.txt",
                    status=FileStatus.ABORTED_VERSION,
                    message="base manifest is stale",
                ),
            ),
            published_manifest_version=None,
        )

    async def run_maintenance_after_publish(self, *_args, **_kwargs) -> dict[str, float]:
        self.order.append("maintenance")
        return {}


def _write_change(tmp_path: Path, path: str = "shared.txt") -> OverlayPathChange:
    content = tmp_path / f"{path}.content"
    content.parent.mkdir(parents=True, exist_ok=True)
    content.write_text("new\n", encoding="utf-8")
    return OverlayPathChange(
        path=path,
        kind="write",
        content_path=content.as_posix(),
        final_hash=content_hash(content),
    )


def _request(
    *,
    invocation_id: str,
    intent: Intent,
    verb: str = "write_file",
    path: str = "shared.txt",
) -> ToolCallRequest:
    args: dict[str, Any]
    if verb == "read_file":
        args = {"path": path}
    else:
        args = {"path": path, "content": "new\n"}
    return ToolCallRequest(
        invocation_id=invocation_id,
        agent_id="agent-a",
        verb=verb,
        intent=intent,
        args=args,
    )


@pytest.mark.asyncio
async def test_ephemeral_write_acquire_run_capture_commit_destroy_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    occ = _Occ(order)

    async def fake_run(_handle, req):
        assert req.intent is Intent.WRITE_ALLOWED
        order.append("run")
        return {"success": True, "status": "ok", "timings": {}}

    async def fake_capture(_handle):
        order.append("capture")
        return [_write_change(tmp_path)]

    monkeypatch.setattr(
        "sandbox.overlay.lifecycle.overlay_writable_root",
        lambda: tmp_path / "writable",
    )
    monkeypatch.setattr(
        "sandbox.ephemeral_workspace.pipeline.overlay_writable_root",
        lambda: tmp_path / "writable",
    )
    monkeypatch.setattr("sandbox.ephemeral_workspace.pipeline.run_in_namespace", fake_run)
    monkeypatch.setattr(
        "sandbox.ephemeral_workspace.pipeline.overlay_lifecycle.capture_changes",
        fake_capture,
    )
    pipeline = EphemeralPipeline(
        occ_client=occ,
        workspace_ref=tmp_path.as_posix(),
        layer_stack=_LayerStack(tmp_path, order),
    )

    result = await pipeline.run_tool_call(
        _request(invocation_id="req-write", intent=Intent.WRITE_ALLOWED)
    )

    assert result["success"] is True
    assert result["changed_paths"] == ["shared.txt"]
    assert occ.sources == ["api_write"]
    assert order == [
        "acquire",
        "run",
        "capture",
        "commit",
        "maintenance",
        "release:lease-1",
    ]


@pytest.mark.asyncio
async def test_ephemeral_read_skips_commit_but_still_destroys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def fake_run(_handle, req):
        assert req.intent is Intent.READ_ONLY
        order.append("run")
        return {"success": True, "content": "ok\n", "timings": {}}

    async def fail_capture(_handle):
        raise AssertionError("read-only requests must not capture upperdir")

    monkeypatch.setattr(
        "sandbox.overlay.lifecycle.overlay_writable_root",
        lambda: tmp_path / "writable",
    )
    monkeypatch.setattr(
        "sandbox.ephemeral_workspace.pipeline.overlay_writable_root",
        lambda: tmp_path / "writable",
    )
    monkeypatch.setattr("sandbox.ephemeral_workspace.pipeline.run_in_namespace", fake_run)
    monkeypatch.setattr(
        "sandbox.ephemeral_workspace.pipeline.overlay_lifecycle.capture_changes",
        fail_capture,
    )
    pipeline = EphemeralPipeline(
        occ_client=_Occ(order),
        workspace_ref=tmp_path.as_posix(),
        layer_stack=_LayerStack(tmp_path, order),
    )

    result = await pipeline.run_tool_call(
        _request(
            invocation_id="req-read",
            intent=Intent.READ_ONLY,
            verb="read_file",
        )
    )

    assert result["success"] is True
    assert result["content"] == "ok\n"
    assert order == ["acquire", "run", "release:lease-1"]


@pytest.mark.asyncio
async def test_ephemeral_same_path_concurrent_conflict_is_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def fake_run(_handle, _req):
        await asyncio.sleep(0)
        return {"success": True, "status": "ok", "timings": {}}

    async def fake_capture(_handle):
        return [_write_change(tmp_path)]

    monkeypatch.setattr(
        "sandbox.overlay.lifecycle.overlay_writable_root",
        lambda: tmp_path / "writable",
    )
    monkeypatch.setattr(
        "sandbox.ephemeral_workspace.pipeline.overlay_writable_root",
        lambda: tmp_path / "writable",
    )
    monkeypatch.setattr("sandbox.ephemeral_workspace.pipeline.run_in_namespace", fake_run)
    monkeypatch.setattr(
        "sandbox.ephemeral_workspace.pipeline.overlay_lifecycle.capture_changes",
        fake_capture,
    )
    pipeline = EphemeralPipeline(
        occ_client=_Occ(order),
        workspace_ref=tmp_path.as_posix(),
        layer_stack=_LayerStack(tmp_path, order),
    )

    first, second = await asyncio.gather(
        pipeline.run_tool_call(
            _request(invocation_id="req-1", intent=Intent.WRITE_ALLOWED)
        ),
        pipeline.run_tool_call(
            _request(invocation_id="req-2", intent=Intent.WRITE_ALLOWED)
        ),
    )

    results = sorted((first, second), key=lambda item: bool(item.get("success")))
    conflict = results[0]
    success = results[1]
    assert success["success"] is True
    assert conflict["success"] is False
    assert conflict["status"] == "aborted_version"
    assert conflict["conflict"] == {
        "reason": "aborted_version",
        "conflict_file": "shared.txt",
        "message": "base manifest is stale",
    }


@pytest.mark.asyncio
async def test_out_of_workspace_paths_use_same_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    async def fake_run(_handle, req):
        seen.append((req.verb, str(req.args.get("path"))))
        return {"success": True, "status": "ok", "timings": {}}

    async def fake_capture(_handle):
        return [_write_change(tmp_path, "tmp/scratch")]

    monkeypatch.setattr(
        "sandbox.overlay.lifecycle.overlay_writable_root",
        lambda: tmp_path / "writable",
    )
    monkeypatch.setattr(
        "sandbox.ephemeral_workspace.pipeline.overlay_writable_root",
        lambda: tmp_path / "writable",
    )
    monkeypatch.setattr("sandbox.ephemeral_workspace.pipeline.run_in_namespace", fake_run)
    monkeypatch.setattr(
        "sandbox.ephemeral_workspace.pipeline.overlay_lifecycle.capture_changes",
        fake_capture,
    )
    pipeline = EphemeralPipeline(
        occ_client=_Occ([]),
        workspace_ref=tmp_path.as_posix(),
        layer_stack=_LayerStack(tmp_path, []),
    )

    read_result = await pipeline.run_tool_call(
        _request(
            invocation_id="req-read-host",
            intent=Intent.READ_ONLY,
            verb="read_file",
            path="/etc/hosts",
        )
    )
    write_result = await pipeline.run_tool_call(
        _request(
            invocation_id="req-write-tmp",
            intent=Intent.WRITE_ALLOWED,
            path="/tmp/scratch",
        )
    )

    assert read_result["success"] is True
    assert write_result["success"] is True
    assert seen == [("read_file", "/etc/hosts"), ("write_file", "/tmp/scratch")]

    from sandbox.overlay.namespace_entrypoint import execute_tool_payload

    denied = execute_tool_payload(
        {
            "workspace_root": tmp_path.as_posix(),
            "tool_call": _request(
                invocation_id="req-deny",
                intent=Intent.WRITE_ALLOWED,
                path="/etc/hosts",
            ).to_payload(),
            "stdout_ref": (tmp_path / "stdout").as_posix(),
            "stderr_ref": (tmp_path / "stderr").as_posix(),
        }
    )
    assert denied["success"] is False
    assert denied["error"]["kind"] == "forbidden_host_path"
