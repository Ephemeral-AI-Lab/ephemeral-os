"""Unit tests for the daemon-managed plugin PPC service bridge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sandbox.ephemeral_workspace.plugin import ppc_service


class _FakePpcStream:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    def flush(self) -> None:
        return None

    def readline(self, limit: int) -> bytes:
        del limit
        request = json.loads(self.writes[-1].decode("utf-8"))
        return ppc_service._reply_frame(
            request["invocation_id"],
            {
                "success": True,
                "published_manifest_version": 5,
                "files": [{"path": "pkg/mod.py", "status": "committed"}],
            },
        )


def test_ppc_service_context_uses_mounted_workspace_state(
    monkeypatch,
) -> None:
    state = ppc_service._ServiceState()
    state.ack_refresh(
        {
            "manifest_key": "root@7",
            "workspace_root": "/testbed",
        }
    )

    captured: dict[str, Any] = {}

    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        captured["args"] = args
        captured["manifest_key"] = ctx.projection.active_manifest_key()
        captured["workspace_root"] = ctx.overlay.workspace_root
        captured["has_acquire_overlay"] = hasattr(ctx.projection, "acquire_overlay")
        return {"success": True, "manifest_key": captured["manifest_key"]}

    monkeypatch.setattr(ppc_service, "_load_handler", lambda _plugin, _op: handler)

    result = asyncio.run(
        ppc_service._dispatch_plugin_op(
            "plugin.demo.run",
            {
                "caller": {"task_id": "task-1"},
                "intent": "read_only",
                "layer_stack_root": "/eos/layer-stack",
            },
            state,
        )
    )

    assert result == {"success": True, "manifest_key": "root@7"}
    assert captured == {
        "args": {
            "caller": {"task_id": "task-1"},
            "intent": "read_only",
            "layer_stack_root": "/eos/layer-stack",
        },
        "manifest_key": "root@7",
        "workspace_root": "/testbed",
        "has_acquire_overlay": False,
    }


def test_ppc_service_publishes_mounted_workspace_changes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "testbed"
    module = workspace / "pkg" / "mod.py"
    module.parent.mkdir(parents=True)
    module.write_text("value = 2\n", encoding="utf-8")
    stream = _FakePpcStream()
    state = ppc_service._ServiceState(stream)
    state.layer_stack_root = "/eos/layer-stack"

    result = asyncio.run(
        state.publish_mounted_workspace_changes(
            ["pkg/mod.py"],
            workspace_root=workspace.as_posix(),
        )
    )

    assert result == {
        "success": True,
        "published_manifest_version": 5,
        "files": [{"path": "pkg/mod.py", "status": "committed"}],
    }
    request = json.loads(stream.writes[0].decode("utf-8"))
    assert request["op"] == "daemon.occ.apply_changeset"
    body = json.loads(request["args"]["body"])
    assert body == {
        "changes": [
            {
                "content_utf8": "value = 2\n",
                "kind": "write",
                "path": "pkg/mod.py",
            }
        ],
        "layer_stack_root": "/eos/layer-stack",
    }
