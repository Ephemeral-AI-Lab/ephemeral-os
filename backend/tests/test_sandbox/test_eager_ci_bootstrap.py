"""Unit tests for the eager in-sandbox CI bootstrap hook + lifecycle wiring.

Covers:

* :func:`bootstrap_in_sandbox_ci_runtime` no-ops when the flag is off,
  transport is missing, or workspace is empty.
* :func:`bootstrap_in_sandbox_ci_runtime` starts the daemon when the flag is set.
* :meth:`SandboxService.create_sandbox` (a) calls the hook when the flag is
  set, (b) skips when the flag is unset, (c) propagates errors from the hook.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_CI_IN_SANDBOX", "1")


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOS_CI_IN_SANDBOX", raising=False)


# ---------------------------------------------------------------------------
# bootstrap_in_sandbox_ci_runtime
# ---------------------------------------------------------------------------


def test_bootstrap_helper_noop_when_flag_off(flag_off: None) -> None:
    from sandbox.lifecycle.workspace import bootstrap_in_sandbox_ci_runtime

    sentinel = object()
    transport = type("T", (), {"exec": lambda *_, **__: sentinel})()

    asyncio.run(
        bootstrap_in_sandbox_ci_runtime(
            sandbox_id="sb-1",
            workspace_root="/ws",
            transport=transport,
        )
    )  # No exception, no exec called (lambda would have returned sentinel).


def test_bootstrap_helper_noop_when_transport_none(flag_on: None) -> None:
    from sandbox.lifecycle.workspace import bootstrap_in_sandbox_ci_runtime

    asyncio.run(
        bootstrap_in_sandbox_ci_runtime(
            sandbox_id="sb-1",
            workspace_root="/ws",
            transport=None,
        )
    )


def test_bootstrap_helper_noop_when_workspace_empty(flag_on: None) -> None:
    from sandbox.lifecycle.workspace import bootstrap_in_sandbox_ci_runtime

    transport = type(
        "T",
        (),
        {"exec": lambda *_, **__: pytest.fail("transport.exec must not be called")},
    )()

    asyncio.run(
        bootstrap_in_sandbox_ci_runtime(
            sandbox_id="sb-1",
            workspace_root="",
            transport=transport,
        )
    )


def test_bootstrap_helper_starts_daemon(flag_on: None) -> None:
    from sandbox.code_intelligence.rpc.launcher import bundle_hash
    from sandbox.lifecycle.workspace import bootstrap_in_sandbox_ci_runtime

    calls: list[tuple[str, str]] = []

    class FakeTransport:
        async def exec(self, sandbox_id: str, command: str, **_: Any) -> Any:
            calls.append((sandbox_id, command))
            if 'printf %s "$HOME"' in command:
                return type("R", (), {"exit_code": 0, "stdout": "/home/u"})()
            if "daemon.pid" in command and "kill -0" in command:
                return type("R", (), {"exit_code": 1, "stdout": ""})()
            if ".bundle-hash" in command and "tar -xzf" not in command:
                return type("R", (), {"exit_code": 0, "stdout": bundle_hash()})()
            return type("R", (), {"exit_code": 0, "stdout": "{\"ok\": true}"})()

        async def write_bytes(
            self, sandbox_id: str, path: str, content: bytes
        ) -> None:
            del sandbox_id, path, content
            pytest.fail("write_bytes must not be used (use chunked exec)")

    asyncio.run(
        bootstrap_in_sandbox_ci_runtime(
            sandbox_id="sb-1",
            workspace_root="/ws",
            transport=FakeTransport(),
        )
    )
    assert any(
        "setsid nohup python3 -m sandbox.code_intelligence.in_sandbox" in cmd
        for _, cmd in calls
    )
    assert any("--workspace-root /ws" in cmd for _, cmd in calls)
    assert any("test -S" in cmd and "daemon.sock" in cmd for _, cmd in calls)


def test_bootstrap_helper_raises_on_daemon_failure(flag_on: None) -> None:
    from sandbox.code_intelligence.rpc.launcher import CiDaemonUnavailable
    from sandbox.lifecycle.workspace import bootstrap_in_sandbox_ci_runtime

    class FakeTransport:
        async def exec(self, sandbox_id: str, command: str, **_: Any) -> Any:
            return type("R", (), {"exit_code": 0, "stdout": ""})()

        async def write_bytes(
            self, sandbox_id: str, path: str, content: bytes
        ) -> None:
            del sandbox_id, path, content

    async def fail_ensure(*_: Any, **__: Any) -> None:
        raise CiDaemonUnavailable("socket timeout")

    with patch(
        "sandbox.code_intelligence.rpc.launcher.DaemonLauncher.ensure_daemon",
        new=fail_ensure,
    ), pytest.raises(CiDaemonUnavailable, match="socket timeout"):
        asyncio.run(
            bootstrap_in_sandbox_ci_runtime(
                sandbox_id="sb-1",
                workspace_root="/ws",
                transport=FakeTransport(),
            )
        )


# ---------------------------------------------------------------------------
# _maybe_run_eager_ci_bootstrap (lifecycle entry point)
# ---------------------------------------------------------------------------


def _make_raw_sandbox(project_dir: str | None) -> Any:
    return type(
        "RawSandbox",
        (),
        {"project_dir": project_dir, "labels": None},
    )()


def test_maybe_bootstrap_skips_when_flag_off(flag_off: None) -> None:
    from sandbox.lifecycle.service import _maybe_run_eager_ci_bootstrap

    sentinel_called = {"called": False}

    async def boom(*_: Any, **__: Any) -> None:
        sentinel_called["called"] = True

    with patch(
        "sandbox.lifecycle.service.bootstrap_in_sandbox_ci_runtime",
        new=boom,
    ):
        _maybe_run_eager_ci_bootstrap(_make_raw_sandbox("/ws"), "sb-1")
    assert sentinel_called["called"] is False


def test_maybe_bootstrap_skips_when_workspace_unresolvable(
    flag_on: None,
) -> None:
    from sandbox.lifecycle.service import _maybe_run_eager_ci_bootstrap

    sentinel_called = {"called": False}

    async def boom(*_: Any, **__: Any) -> None:
        sentinel_called["called"] = True

    with patch(
        "sandbox.lifecycle.service.bootstrap_in_sandbox_ci_runtime",
        new=boom,
    ):
        _maybe_run_eager_ci_bootstrap(_make_raw_sandbox(None), "sb-1")
    assert sentinel_called["called"] is False


def test_maybe_bootstrap_invokes_helper_when_flag_on(
    flag_on: None,
) -> None:
    from sandbox.lifecycle.service import _maybe_run_eager_ci_bootstrap

    calls: list[dict[str, Any]] = []

    async def fake_helper(
        sandbox_id: str, workspace_root: str, *, transport: Any
    ) -> None:
        calls.append(
            {
                "sandbox_id": sandbox_id,
                "workspace_root": workspace_root,
                "transport": transport,
            }
        )

    fake_transport = object()
    with patch(
        "sandbox.lifecycle.service.bootstrap_in_sandbox_ci_runtime",
        new=fake_helper,
    ), patch(
        "sandbox.daytona.transport.DaytonaTransport",
        return_value=fake_transport,
    ):
        _maybe_run_eager_ci_bootstrap(_make_raw_sandbox("/ws"), "sb-1")

    assert len(calls) == 1
    assert calls[0]["sandbox_id"] == "sb-1"
    assert calls[0]["workspace_root"] == "/ws"
    assert calls[0]["transport"] is fake_transport


def test_maybe_bootstrap_propagates_runtime_error(flag_on: None) -> None:
    from sandbox.lifecycle.service import _maybe_run_eager_ci_bootstrap

    async def fake_helper(*_: Any, **__: Any) -> None:
        raise RuntimeError("indexer crashed")

    with patch(
        "sandbox.lifecycle.service.bootstrap_in_sandbox_ci_runtime",
        new=fake_helper,
    ), patch(
        "sandbox.daytona.transport.DaytonaTransport",
        return_value=object(),
    ), pytest.raises(RuntimeError, match="indexer crashed"):
        _maybe_run_eager_ci_bootstrap(_make_raw_sandbox("/ws"), "sb-1")
