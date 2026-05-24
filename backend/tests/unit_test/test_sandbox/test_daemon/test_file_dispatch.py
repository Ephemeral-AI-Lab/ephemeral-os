"""Daemon dispatch contracts for direct layer-stack file verbs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sandbox._shared.models import Intent
from sandbox.daemon import dispatch, occ_backend
from sandbox.layer_stack import LayerStack
from sandbox.layer_stack.workspace_base import build_workspace_base


@pytest.mark.asyncio
async def test_ephemeral_file_verbs_use_direct_occ_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("alpha\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    occ_backend.clear_backend_cache()

    async def fail_overlay(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("file verbs should not mount an ephemeral overlay")

    monkeypatch.setattr(dispatch, "get_sandbox_overlay", fail_overlay)

    common: dict[str, object] = {
        "agent_id": "agent",
        "caller": {"agent_id": "agent"},
        "layer_stack_root": stack.as_posix(),
    }
    write = await dispatch.run_tool_handler(
        {
            **common,
            "path": (workspace / "created.txt").as_posix(),
            "content": "created\n",
        },
        verb="write_file",
        intent=dispatch.Intent.WRITE_ALLOWED,
    )
    edit = await dispatch.run_tool_handler(
        {
            **common,
            "path": (workspace / "note.txt").as_posix(),
            "edits": [{"old_text": "alpha\n", "new_text": "beta\n"}],
        },
        verb="edit_file",
        intent=dispatch.Intent.WRITE_ALLOWED,
    )
    read = await dispatch.run_tool_handler(
        {
            **common,
            "path": (workspace / "note.txt").as_posix(),
        },
        verb="read_file",
        intent=dispatch.Intent.READ_ONLY,
    )

    manager = LayerStack(stack)
    assert write["success"] is True
    assert write["changed_paths"] == ["created.txt"]
    assert write["changed_path_kinds"] == {"created.txt": "write"}
    assert write["mutation_source"] == "api_write"
    assert "workspace.mount_s" not in write["timings"]
    assert write["timings"]["resource.command_exec.workspace_tree_bytes"] == 0.0
    assert edit["success"] is True
    assert edit["applied_edits"] == 1
    assert edit["changed_path_kinds"] == {"note.txt": "write"}
    assert edit["mutation_source"] == "api_edit"
    assert "workspace.mount_s" not in edit["timings"]
    assert edit["timings"]["resource.command_exec.changed_path_count"] == 1.0
    assert read["success"] is True
    assert read["content"] == "beta\n"
    assert "workspace.mount_s" not in read["timings"]
    assert read["timings"]["resource.command_exec.changed_path_count"] == 0.0
    assert manager.read_text("created.txt") == ("created\n", True)
    assert manager.read_text("note.txt") == ("beta\n", True)


@pytest.mark.asyncio
async def test_ephemeral_file_fast_path_omits_changed_paths_on_conflict(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("alpha\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    occ_backend.clear_backend_cache()

    result = await dispatch.run_tool_handler(
        {
            "agent_id": "agent",
            "caller": {"agent_id": "agent"},
            "layer_stack_root": stack.as_posix(),
            "path": (workspace / "note.txt").as_posix(),
            "edits": [{"old_text": "missing\n", "new_text": "beta\n"}],
        },
        verb="edit_file",
        intent=Intent.WRITE_ALLOWED,
    )

    assert result["success"] is False
    assert result["changed_paths"] == []
    assert result["changed_path_kinds"] == {}
    assert result["mutation_source"] == "api_edit"


@pytest.mark.asyncio
async def test_ephemeral_file_verbs_fall_back_for_outside_workspace_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    occ_backend.clear_backend_cache()
    seen: list[tuple[str, str]] = []

    class _Pipeline:
        async def run_tool_call(self, req: object) -> dict[str, object]:
            assert isinstance(req, dispatch.ToolCallRequest)
            seen.append((req.verb, str(req.args.get("path") or "")))
            return {
                "success": True,
                "workspace": "ephemeral",
                "status": "ok",
                "changed_paths": [],
                "timings": {"workspace.mount_s": 0.01},
            }

    async def fake_overlay(*_args: Any, **_kwargs: Any) -> _Pipeline:
        return _Pipeline()

    monkeypatch.setattr(dispatch, "get_sandbox_overlay", fake_overlay)

    result = await dispatch.run_tool_handler(
        {
            "agent_id": "agent",
            "caller": {"agent_id": "agent"},
            "layer_stack_root": stack.as_posix(),
            "path": "/tmp/outside.txt",
            "content": "outside\n",
        },
        verb="write_file",
        intent=Intent.WRITE_ALLOWED,
    )

    assert result["success"] is True
    assert result["timings"]["workspace.mount_s"] == 0.01
    assert seen == [("write_file", "/tmp/outside.txt")]
