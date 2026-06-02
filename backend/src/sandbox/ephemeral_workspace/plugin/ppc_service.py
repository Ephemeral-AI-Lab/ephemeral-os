"""PPC service bridge for daemon-managed plugin runtime processes."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
from collections.abc import Mapping
from typing import Any

from sandbox.ephemeral_workspace.plugin.op_registry import (
    clear_plugin_registrations,
    pending_plugin_registrations,
)
from sandbox.ephemeral_workspace.plugin.runtime_api import _build_plugin_op_context

_REFRESH_OP = "daemon.workspace_snapshot_refresh"
_MAX_FRAME_BYTES = 16 * 1024 * 1024


def main() -> int:
    socket_path = os.environ.get("EOS_PLUGIN_PPC_SOCKET", "").strip()
    if not socket_path:
        sys.stderr.write("EOS_PLUGIN_PPC_SOCKET is required\n")
        return 2
    try:
        return asyncio.run(_serve(socket_path))
    except Exception as exc:
        sys.stderr.write(f"plugin PPC service failed: {exc}\n")
        return 126


async def _serve(socket_path: str) -> int:
    stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stream.connect(socket_path)
    stream_file = stream.makefile("rwb", buffering=0)
    state = _ServiceState()
    try:
        while True:
            frame = stream_file.readline(_MAX_FRAME_BYTES + 1)
            if not frame:
                return 0
            if len(frame) > _MAX_FRAME_BYTES:
                raise ValueError("PPC frame exceeded byte limit")
            reply = await _handle_frame(frame, state)
            stream_file.write(reply)
            stream_file.flush()
    finally:
        stream_file.close()
        stream.close()


async def _handle_frame(frame: bytes, state: "_ServiceState") -> bytes:
    message = json.loads(frame.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("PPC frame must be a JSON object")
    message_id = str(message.get("invocation_id") or "")
    op = str(message.get("op") or "")
    args = message.get("args")
    if not isinstance(args, dict):
        raise ValueError("PPC frame args must be an object")
    if args.get("direction") != "request":
        raise ValueError("PPC service only accepts request frames")
    body = _json_object(args.get("body"))

    if op == _REFRESH_OP:
        result = state.ack_refresh(body)
    else:
        result = await _dispatch_plugin_op(op, body)
    return _reply_frame(message_id, result)


async def _dispatch_plugin_op(public_op: str, args: dict[str, Any]) -> dict[str, Any]:
    plugin_name, op_name = _split_public_op(public_op)
    handler = _load_handler(plugin_name, op_name)
    ctx = await _build_plugin_op_context(args, plugin_name, op_name)
    try:
        result = handler(args, ctx)
        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
            result = await result
    except Exception as exc:
        return {
            "success": False,
            "error": {
                "kind": type(exc).__name__,
                "message": str(exc) or type(exc).__name__,
            },
        }
    if isinstance(result, Mapping):
        return dict(result)
    return {"success": True, "result": result}


def _load_handler(plugin_name: str, op_name: str) -> Any:
    clear_plugin_registrations(plugin_name)
    module_name = f"plugins.catalog.{plugin_name}.runtime.server"
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
    else:
        importlib.import_module(module_name)
    matches = [
        entry.handler
        for entry in pending_plugin_registrations(plugin_name)
        if entry.op_name == op_name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one registered handler for {plugin_name}.{op_name}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _split_public_op(public_op: str) -> tuple[str, str]:
    parts = public_op.split(".")
    if len(parts) < 3 or parts[0] != "plugin":
        raise ValueError(f"invalid plugin public op: {public_op!r}")
    return parts[1], ".".join(parts[2:])


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    parsed = json.loads(str(raw or "{}"))
    if not isinstance(parsed, dict):
        raise ValueError("PPC body must be a JSON object")
    return parsed


def _reply_frame(message_id: str, body: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            {
                "op": "reply",
                "invocation_id": message_id,
                "args": {
                    "direction": "reply",
                    "body": json.dumps(
                        dict(body),
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


class _ServiceState:
    def __init__(self) -> None:
        self.manifest_key = os.environ.get("EOS_PLUGIN_MANIFEST_KEY", "").strip()

    def ack_refresh(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = str(
            payload.get("target_manifest_key")
            or payload.get("manifest_key")
            or self.manifest_key
        )
        if target:
            self.manifest_key = target
        return {"manifest_key": self.manifest_key, "accepted": True}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
