"""Tests for ``sandbox.api.tool.edit``."""

from __future__ import annotations

import pytest

from sandbox.api import EditFileRequest, SandboxCaller, SearchReplaceEdit
from sandbox.api.tool.edit import edit_file


@pytest.mark.asyncio
async def test_edit_file_dispatches_to_sandbox_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object], int]] = []

    async def fake_call_runtime_api(sandbox_id, op, args, *, timeout):
        calls.append((sandbox_id, op, args, timeout))
        return {
            "success": True,
            "changed_paths": ["a.py"],
            "applied_edits": 1,
            "status": "ok",
            "conflict": None,
            "conflict_reason": None,
            "timings": {},
        }

    monkeypatch.setattr(
        "sandbox.api.tool.edit.call_runtime_api",
        fake_call_runtime_api,
    )

    result = await edit_file(
        "sb-edit",
        EditFileRequest(
            path="a.py",
            edits=(SearchReplaceEdit(old_text="old", new_text="new"),),
            caller=SandboxCaller(agent_id="agent-1"),
            description="edit a",
        ),
    )

    assert result.success is True
    assert result.changed_paths == ("a.py",)
    assert result.applied_edits == 1
    assert calls == [
        (
            "sb-edit",
            "api.edit_file",
            {
                "path": "a.py",
                "edits": [{"old_text": "old", "new_text": "new"}],
                "actor_id": "agent-1",
                "description": "edit a",
            },
            60,
        )
    ]


@pytest.mark.asyncio
async def test_edit_file_guard_failure_maps_conflict_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_runtime_api(sandbox_id, op, args, *, timeout):
        del sandbox_id, op, args, timeout
        return {
            "success": False,
            "changed_paths": [],
            "applied_edits": 0,
            "status": "aborted_overlap",
            "conflict": {
                "reason": "aborted_overlap",
                "conflict_file": "a.py",
                "message": "patch_failed",
            },
            "conflict_reason": "patch_failed",
            "timings": {},
        }

    monkeypatch.setattr(
        "sandbox.api.tool.edit.call_runtime_api",
        fake_call_runtime_api,
    )

    result = await edit_file(
        "sb-edit-conflict",
        EditFileRequest(
            path="a.py",
            edits=(SearchReplaceEdit(old_text="old", new_text="new"),),
            caller=SandboxCaller(agent_id="agent-1"),
        ),
    )

    assert result.success is False
    assert result.applied_edits == 0
    assert result.status == "aborted_overlap"
    assert result.conflict is not None
    assert result.conflict.reason == "aborted_overlap"
    assert result.conflict.message == "patch_failed"
    assert result.conflict_reason == "patch_failed"
