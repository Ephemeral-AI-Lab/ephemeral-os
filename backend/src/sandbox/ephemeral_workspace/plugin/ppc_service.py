"""PPC service bridge for daemon-managed plugin runtime processes."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from sandbox.ephemeral_workspace.plugin.op_registry import (
    clear_plugin_registrations,
    pending_plugin_registrations,
)
from sandbox.ephemeral_workspace.plugin.op_context import (
    PluginOpContext,
    plugin_intent_from_envelope,
    sandbox_caller_from_plugin_envelope,
)

_REFRESH_OP = "daemon.workspace_snapshot_refresh"
_OCC_APPLY_CHANGESET_OP = "daemon.occ.apply_changeset"
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
    state = _ServiceState(stream_file)
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
        result = await _dispatch_plugin_op(op, body, state)
    return _reply_frame(message_id, result)


async def _dispatch_plugin_op(
    public_op: str,
    args: dict[str, Any],
    state: "_ServiceState",
) -> dict[str, Any]:
    plugin_name, op_name = _split_public_op(public_op)
    handler = _load_handler(plugin_name, op_name)
    ctx = state.context(args, op_name)
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
    def __init__(self, stream_file: Any | None = None) -> None:
        self._stream_file = stream_file
        self.manifest_key = os.environ.get("EOS_PLUGIN_MANIFEST_KEY", "").strip()
        self.layer_stack_root = os.environ.get("EOS_PLUGIN_LAYER_STACK_ROOT", "").strip()
        self.workspace_root = os.environ.get("EOS_PLUGIN_WORKSPACE_ROOT", "").strip()

    def ack_refresh(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = str(
            payload.get("target_manifest_key")
            or payload.get("manifest_key")
            or self.manifest_key
        )
        if target:
            self.manifest_key = target
        workspace_root = str(payload.get("workspace_root") or "").strip()
        if workspace_root:
            self.workspace_root = workspace_root
        return {"manifest_key": self.manifest_key, "accepted": True}

    def context(self, args: dict[str, Any], op_name: str) -> PluginOpContext:
        layer_stack_root = str(args.get("layer_stack_root") or self.layer_stack_root)
        workspace_root = str(args.get("workspace_root") or self.workspace_root or "/testbed")
        manifest_key = str(args.get("manifest_key") or self.manifest_key or "workspace@0")
        self.layer_stack_root = layer_stack_root
        self.workspace_root = workspace_root
        self.manifest_key = manifest_key
        return PluginOpContext(
            layer_stack_root=layer_stack_root,
            caller=sandbox_caller_from_plugin_envelope(args.get("caller")),
            projection=_MountedWorkspaceProjection(layer_stack_root, lambda: self.manifest_key),
            overlay=_MountedWorkspaceOverlay(
                workspace_root,
                lambda: self.manifest_key,
                self.publish_mounted_workspace_changes,
            ),
            intent=plugin_intent_from_envelope(args.get("intent")),
            metadata={"op_name": op_name, "workspace_root": workspace_root},
        )

    async def publish_mounted_workspace_changes(
        self,
        changed_paths: list[str],
        *,
        workspace_root: str,
    ) -> dict[str, Any]:
        if self._stream_file is None:
            raise RuntimeError("PPC stream is unavailable for OCC callback")
        changes = [
            _change_for_path(Path(workspace_root), changed_path)
            for changed_path in changed_paths
        ]
        message_id = f"plugin-occ-apply-{uuid4().hex}"
        self._stream_file.write(
            _request_frame(
                message_id,
                _OCC_APPLY_CHANGESET_OP,
                {
                    "layer_stack_root": self.layer_stack_root,
                    "changes": changes,
                },
            )
        )
        self._stream_file.flush()
        frame = self._stream_file.readline(_MAX_FRAME_BYTES + 1)
        if not frame:
            raise RuntimeError("PPC OCC callback stream closed before reply")
        if len(frame) > _MAX_FRAME_BYTES:
            raise RuntimeError("PPC OCC callback reply exceeded byte limit")
        reply = json.loads(frame.decode("utf-8"))
        if not isinstance(reply, dict):
            raise RuntimeError("PPC OCC callback reply must be a JSON object")
        if str(reply.get("invocation_id") or "") != message_id:
            raise RuntimeError("PPC OCC callback reply used wrong message id")
        args = reply.get("args")
        if not isinstance(args, dict) or args.get("direction") != "reply":
            raise RuntimeError("PPC OCC callback reply has invalid direction")
        return _json_object(args.get("body"))


@dataclass(frozen=True)
class _MountedWorkspaceProjection:
    layer_stack_root: str
    _manifest_key: Callable[[], str] = field(repr=False)

    def active_manifest_key(self) -> str:
        return str(self._manifest_key())


@dataclass(frozen=True)
class _MountedWorkspaceOverlay:
    workspace_root: str
    _manifest_key: Callable[[], str] = field(repr=False)
    _publish_changes: Callable[..., Any] = field(repr=False)

    def active_manifest_key(self) -> str:
        return str(self._manifest_key())

    async def ensure_current(self, *, reason: str = "") -> str:
        del reason
        return str(self._manifest_key())

    async def publish_mounted_workspace_changes(
        self,
        changed_paths: list[str],
        *,
        workspace_root: str,
    ) -> dict[str, Any]:
        return await self._publish_changes(
            changed_paths,
            workspace_root=workspace_root,
        )


def _request_frame(message_id: str, op: str, body: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            {
                "op": op,
                "invocation_id": message_id,
                "args": {
                    "direction": "request",
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


def _change_for_path(workspace_root: Path, changed_path: str) -> dict[str, Any]:
    path = Path(changed_path)
    if path.is_absolute():
        rel = path.resolve(strict=False).relative_to(workspace_root.resolve(strict=False))
    else:
        rel = path
        path = workspace_root / path
    rel_text = rel.as_posix()
    if path.is_symlink():
        return {
            "kind": "symlink",
            "path": rel_text,
            "source_path": os.readlink(path),
        }
    if not path.exists():
        return {"kind": "delete", "path": rel_text}
    content = path.read_bytes()
    try:
        return {
            "kind": "write",
            "path": rel_text,
            "content_utf8": content.decode("utf-8"),
        }
    except UnicodeDecodeError:
        return {
            "kind": "write",
            "path": rel_text,
            "content_bytes": list(content),
        }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
