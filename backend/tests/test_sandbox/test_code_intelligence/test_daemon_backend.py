"""Unit tests for ``DaemonBackend.ensure_initialized`` and daemon commands.

Phase 3.5 retired the orchestrator-side pickle-snapshot fallback. These tests
exercise the daemon-route contract: ``ensure_initialized`` launches the daemon
(mocked) and polls ``index_ready``; command calls route through the daemon and
surface errors instead of falling back to stale orchestrator state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from sandbox.code_intelligence.backends import DaemonBackend
from sandbox.code_intelligence.daemon import client as daemon_client
from sandbox.code_intelligence.daemon.client import DaemonCommandClient


class _NullTransport:
    """Minimal stub — DaemonBackend transport execution is bypassed here by injecting a fake daemon command handler. The daemon launcher is mocked at the boundary."""

    name = "null"

    async def exec(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("DaemonBackend should not call transport.exec post-3.5")


class _FakeDaemon:
    """Stand-in for :class:`DaemonBackend` returning canned daemon responses."""

    def __init__(
        self,
        *,
        index_ready: bool = True,
        cmd_response: dict[str, Any] | None = None,
        raise_for_op: dict[str, Exception] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self._index_ready = index_ready
        self._cmd_response = cmd_response or {}
        self._raise_for_op = dict(raise_for_op or {})

    async def _call_daemon_command(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        del timeout
        self.calls.append((op, args))
        if op in self._raise_for_op:
            raise self._raise_for_op[op]
        if op == "index_ready":
            return {"ready": self._index_ready}
        if op == "svc_cmd":
            return self._cmd_response
        return None


def _backend_with_fake_daemon(daemon: _FakeDaemon) -> DaemonBackend:
    backend = DaemonBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=_NullTransport(),  # type: ignore[arg-type]
    )
    backend._call_daemon_command = daemon._call_daemon_command  # type: ignore[method-assign]
    backend._launcher = _FakeLauncher()  # type: ignore[assignment]
    return backend


class _FakeLauncher:
    """Stand-in for :class:`DaemonLauncher` — ``ensure_daemon`` is a no-op."""

    instances: list[_FakeLauncher] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        type(self).instances.append(self)
        self.ensure_calls = 0
        self.shutdown_calls = 0

    async def ensure_daemon(self) -> None:
        if self not in type(self).instances:
            type(self).instances.append(self)
        self.ensure_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_ensure_initialized_polls_index_ready_until_built() -> None:
    """Daemon launches; we poll ``index_ready`` and flip is_initialized once true."""
    daemon = _FakeDaemon(index_ready=True)
    backend = _backend_with_fake_daemon(daemon)
    _FakeLauncher.instances.clear()
    with patch(
        "sandbox.code_intelligence.daemon.launcher.DaemonLauncher", _FakeLauncher
    ):
        ok = backend.ensure_initialized(wait=True)
    assert ok is True
    assert backend.is_initialized is True
    assert _FakeLauncher.instances and _FakeLauncher.instances[-1].ensure_calls == 1
    # The poll should have called index_ready at least once.
    assert any(call[0] == "index_ready" for call in daemon.calls)


def test_ensure_initialized_idempotent() -> None:
    daemon = _FakeDaemon(index_ready=True)
    backend = _backend_with_fake_daemon(daemon)
    _FakeLauncher.instances.clear()
    with patch(
        "sandbox.code_intelligence.daemon.launcher.DaemonLauncher", _FakeLauncher
    ):
        backend.ensure_initialized(wait=True)
        n = len(_FakeLauncher.instances)
        backend.ensure_initialized(wait=True)
    # Second call short-circuits; no new launcher constructed.
    assert len(_FakeLauncher.instances) == n


def test_ensure_initialized_returns_true_even_if_index_ready_times_out() -> None:
    """When the index-ready poll times out, ensure_initialized still flips
    is_initialized so callers can attempt queries (which return [] until the
    background build completes)."""
    daemon = _FakeDaemon(index_ready=False)
    backend = _backend_with_fake_daemon(daemon)
    backend._INDEX_READY_TIMEOUT_S = 0.05  # type: ignore[assignment]
    backend._INDEX_READY_POLL_S = 0.01  # type: ignore[assignment]
    _FakeLauncher.instances.clear()
    with patch(
        "sandbox.code_intelligence.daemon.launcher.DaemonLauncher", _FakeLauncher
    ):
        ok = backend.ensure_initialized(wait=True)
    assert ok is True
    assert backend.is_initialized is True


def test_cmd_routes_through_daemon_and_reconstructs_namespace() -> None:
    """``cmd`` uses the daemon ``svc_cmd`` op and preserves result fields."""
    import asyncio
    from unittest.mock import MagicMock

    daemon = _FakeDaemon(
        cmd_response={
            "result": "hi\n",
            "exit_code": 0,
            "changed_paths": ["/ws/a.py"],
            "ambient_changed_paths": [],
            "files_written": 1,
            "git_commit_status": "committed",
            "git_conflict_file": None,
            "git_conflict_reason": None,
            "gitinclude_changed_paths": ["/ws/a.py"],
            "gitignore_direct_merged_paths": [],
            "gitignore_direct_merged_count": 0,
            "mixed_gitinclude_gitignore": False,
            "mixed_partial_apply": False,
            "warnings": [],
            "overlay_run_timings": {"total": 0.2},
        }
    )
    backend = _backend_with_fake_daemon(daemon)
    progress: list[str] = []

    async def _run() -> None:
        result = await backend.cmd(
            MagicMock(),
            "echo hi",
            timeout=5,
            agent_id="agent-a",
            on_progress_line=progress.append,
        )
        assert result.result == "hi\n"
        assert result.exit_code == 0
        assert result.changed_paths == ["/ws/a.py"]
        assert result.overlay_run_timings == {"total": 0.2}
        assert result.daemon_call_timings["total"] >= 0.0

    asyncio.run(_run())
    assert progress == ["hi\n"]
    assert daemon.calls == [
        (
            "svc_cmd",
            {
                "command": "echo hi",
                "timeout": 5,
                "agent_id": "agent-a",
            },
        )
    ]


def test_rebind_sandbox_is_noop() -> None:
    backend = DaemonBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=_NullTransport(),  # type: ignore[arg-type]
    )
    backend.rebind_sandbox(object())


def test_init_drops_legacy_cache_attributes() -> None:
    """Cleanup invariant: the orchestrator-side snapshot cache attributes
    are gone (Phase 3.5 retirement)."""
    backend = DaemonBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=_NullTransport(),  # type: ignore[arg-type]
    )
    for attr in (
        "_symbol_cache",
        "_cached_file_count",
        "_cached_symbol_count",
        "_snapshot_bytes",
    ):
        assert not hasattr(backend, attr), (
            f"Phase 3.5 cleanup regression: {attr} still on DaemonBackend"
        )


def test_daemon_client_module_has_no_language_server_queries() -> None:
    """Boundary invariant: daemon/client.py stays transport-only."""
    source = Path(daemon_client.__file__).read_text(encoding="utf-8")
    forbidden = (
        "find_definitions",
        "find_references",
        "query_symbols",
        "hover_result_from_dict",
        "reference_info_from_dict",
        "diagnostic_from_dict",
        "def hover(",
        "def diagnostics(",
    )
    for token in forbidden:
        assert token not in source
    for method in (
        "find_definitions",
        "find_references",
        "query_symbols",
        "hover",
        "diagnostics",
    ):
        assert not hasattr(DaemonCommandClient, method)
