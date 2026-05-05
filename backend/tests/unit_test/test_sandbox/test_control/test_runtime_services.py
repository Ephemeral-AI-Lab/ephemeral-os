"""Tests for provider-backed sandbox runtime service bindings."""

from __future__ import annotations

import pytest

from sandbox.api import SandboxCaller, SearchReplaceEdit
from sandbox.control.ops import runtime_services


class _Adapter:
    async def exec(self, *_args, **_kwargs):
        raise AssertionError("runtime server call is mocked in this test")


@pytest.mark.asyncio
async def test_remote_runtime_services_dispatch_public_verbs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object], int]] = []

    async def fake_ensure_runtime_uploaded(sandbox_id: str) -> str:
        assert sandbox_id == "sb-runtime"
        return "bundle-hash"

    async def fake_call_runtime_server(*, exec_fn, sandbox_id, op, args, timeout):
        del exec_fn
        calls.append((sandbox_id, op, args, timeout))
        if op == "api.write_file":
            return {
                "success": True,
                "changed_paths": ["a.py"],
                "status": "ok",
                "timings": {},
            }
        if op == "api.edit_file":
            return {
                "success": True,
                "changed_paths": ["a.py"],
                "applied_edits": 1,
                "status": "ok",
                "timings": {},
            }
        if op == "api.read_file":
            return {
                "success": True,
                "exists": True,
                "content": "content",
                "encoding": "utf-8",
                "timings": {},
            }
        if op == "api.shell":
            return {
                "success": True,
                "exit_code": 0,
                "stdout": "ok\n",
                "stderr": "",
                "changed_paths": ["b.py"],
                "status": "ok",
                "warnings": [],
                "timings": {},
            }
        raise AssertionError(f"unexpected op: {op}")

    monkeypatch.setattr(
        runtime_services,
        "ensure_runtime_uploaded",
        fake_ensure_runtime_uploaded,
    )
    monkeypatch.setattr(runtime_services, "get_adapter", lambda _sandbox_id: _Adapter())
    monkeypatch.setattr(
        runtime_services,
        "_call_runtime_server",
        fake_call_runtime_server,
    )

    services = runtime_services.create_remote_runtime_services(
        sandbox_id="sb-runtime",
        layer_stack_root="/sandbox/layers",
        ignored_paths={"build/"},
    )
    caller = SandboxCaller(agent_id="agent-1")

    write = await services.write_file(
        path="a.py",
        content="x",
        caller=caller,
        description="write a",
    )
    edit = await services.edit_file(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="x", new_text="y"),),
        caller=caller,
        description="edit a",
    )
    read = await services.read_file(path="a.py", caller=caller)
    command = await services.shell(
        command="printf ok",
        timeout=10,
        cwd=".",
        caller=caller,
        description="shell",
    )

    assert write.changed_paths == ("a.py",)
    assert edit.applied_edits == 1
    assert read.content == "content"
    assert command.stdout == "ok\n"
    assert [call[1] for call in calls] == [
        "api.write_file",
        "api.edit_file",
        "api.read_file",
        "api.shell",
    ]
    for _, _, args, _ in calls:
        assert args["layer_stack_root"] == "/sandbox/layers"
        assert args["ignored_paths"] == ["build"]
