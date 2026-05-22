"""Thin async client for ``api.isolated_workspace.*`` daemon RPCs.

Wraps :func:`sandbox.host.daemon_client.call_daemon_api` so individual tests
read as intent (``enter()``, ``shell()``, ``exit()``) instead of envelope
boilerplate.

Each helper returns the raw daemon JSON response and lets the caller assert
on ``response["success"]`` / ``response["error"]["kind"]``. Transport errors
propagate; lifecycle errors are surfaced inside the response envelope so test
assertions stay explicit.

This module is intentionally narrow: it does NOT wrap audit-bus reads (use
:mod:`_iws_invariants`), and it does NOT cover daemon-host shell-out
(``adapter.exec`` lives in :mod:`_iws_fixtures` as ``daemon_exec``).
"""

from __future__ import annotations

import base64
from typing import Any

from sandbox.host.daemon_client import call_daemon_api


DEFAULT_TIMEOUT_S = 30


async def enter(
    sandbox_id: str,
    agent_id: str,
    *,
    layer_stack_root: str,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    return await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.enter",
        {"agent_id": agent_id, "layer_stack_root": layer_stack_root},
        timeout=timeout,
    )


async def exit_(
    sandbox_id: str,
    agent_id: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    return await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.exit",
        {"agent_id": agent_id},
        timeout=timeout,
    )


async def status(
    sandbox_id: str,
    agent_id: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    return await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.status",
        {"agent_id": agent_id},
        timeout=timeout,
    )


async def shell(
    sandbox_id: str,
    agent_id: str,
    command: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    return await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.shell",
        {"agent_id": agent_id, "command": command},
        timeout=timeout,
    )


async def read_file(
    sandbox_id: str,
    agent_id: str,
    path: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    return await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.read_file",
        {"agent_id": agent_id, "path": path},
        timeout=timeout,
    )


async def write_file(
    sandbox_id: str,
    agent_id: str,
    path: str,
    content: bytes | str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    # The on-the-wire payload is a regular string — encoding is binary-safe
    # only via base64. The daemon handler decodes the same way.
    body = content if isinstance(content, str) else base64.b64encode(content).decode("ascii")
    return await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.write_file",
        {"agent_id": agent_id, "path": path, "content": body},
        timeout=timeout,
    )


async def edit_file(
    sandbox_id: str,
    agent_id: str,
    path: str,
    content: bytes | str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    body = content if isinstance(content, str) else base64.b64encode(content).decode("ascii")
    return await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.edit_file",
        {"agent_id": agent_id, "path": path, "content": body},
        timeout=timeout,
    )


async def search_content(
    sandbox_id: str,
    agent_id: str,
    pattern: str,
    *,
    path: str = "/testbed",
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    return await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.search_content",
        {"agent_id": agent_id, "pattern": pattern, "path": path},
        timeout=timeout,
    )


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "edit_file",
    "enter",
    "exit_",
    "read_file",
    "search_content",
    "shell",
    "status",
    "write_file",
]
