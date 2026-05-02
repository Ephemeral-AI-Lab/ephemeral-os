"""Unit tests for the code intelligence LSP client (Phase 3.6 rewire).

The Phase 1 jedi.Script per-call subprocess shim and its unit tests were
deleted alongside ``python_backend.py``. What remains here:

* Cache + readiness contract (``_run_cached_query``, ``ensure_ready``,
  ``connected``, ``reset_backend_availability``).
* Path / line helpers from :mod:`.path_helpers`
  (``_resolve_path``, ``_read_line`` + invalidation).
* Routing assertions: every public ``goto_definition`` / ``find_references`` /
  ``hover`` / ``diagnostics`` call goes through the persistent
  :class:`LspBackendChild` (mocked here via :class:`LspAsyncHost.run`).

Whatever live LSP behavior matters to the user is exercised by the live E2E
suite (``backend/tests/test_e2e/test_live_ci_phase3_6_lsp_benchmark.py``)
against a real basedpyright child.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.code_intelligence.core.types import (
    Diagnostic,
    DiagnosticSeverity,
    HoverResult,
    ReferenceInfo,
    SymbolInfo,
    SymbolKind,
)
from sandbox.code_intelligence.language_server.client import LspClient


class _Transport:
    name = "test"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def exec(self, sandbox_id: str, command: str, **kwargs):
        del kwargs
        assert sandbox_id == "sb-1"
        self.calls.append(command)
        if "basedpyright-langserver" in command and "install" not in command:
            return SimpleNamespace(exit_code=0)
        if "pip install" in command and "basedpyright" in command:
            return SimpleNamespace(exit_code=0)
        return SimpleNamespace(exit_code=1)


# ---------------------------------------------------------------------------
# Path helpers (unchanged contract)
# ---------------------------------------------------------------------------


def test_resolve_path_prepends_workspace_root() -> None:
    lsp = LspClient(workspace_root="/testbed")
    assert lsp._resolve_path("dask/core.py") == "/testbed/dask/core.py"


def test_resolve_path_leaves_absolute_unchanged() -> None:
    lsp = LspClient(workspace_root="/testbed")
    assert lsp._resolve_path("/testbed/dask/core.py") == "/testbed/dask/core.py"


def test_resolve_path_no_workspace_root_keeps_relative() -> None:
    lsp = LspClient(workspace_root="")
    assert lsp._resolve_path("dask/core.py") == "dask/core.py"


def test_local_read_line_caches_until_invalidate(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "core.py"
    source.parent.mkdir()
    source.write_text("def alpha(value):\n    return value\n", encoding="utf-8")
    lsp = LspClient(workspace_root=str(tmp_path))

    assert lsp._read_line("pkg/core.py", 1) == "def alpha(value):"
    source.write_text("def beta(value):\n    return value\n", encoding="utf-8")
    assert lsp._read_line("pkg/core.py", 1) == "def alpha(value):"

    lsp.invalidate(str(source))

    assert lsp._read_line("pkg/core.py", 1) == "def beta(value):"


# ---------------------------------------------------------------------------
# Cache / single-flight contract
# ---------------------------------------------------------------------------


def test_cached_query_singleflights_concurrent_misses() -> None:
    lsp = LspClient(workspace_root="/workspace")
    calls = 0
    calls_lock = threading.Lock()

    def loader() -> str:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return "resolved"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda _: lsp._run_cached_query("same-key", loader), range(8))
        )

    assert results == ["resolved"] * 8
    assert calls == 1


def test_reset_backend_availability_clears_cached_readiness() -> None:
    lsp = LspClient(workspace_root="/workspace")
    lsp._py_available = False
    lsp.reset_backend_availability()
    assert lsp._py_available is None


# ---------------------------------------------------------------------------
# Phase 3.6 readiness probe (basedpyright, NOT jedi)
# ---------------------------------------------------------------------------


def test_ensure_ready_probes_basedpyright_langserver_binary() -> None:
    """The Phase 3.6 readiness probe checks for ``basedpyright-langserver``,
    NOT ``import jedi`` (which the rewire deleted)."""

    transport = _Transport()
    lsp = LspClient(
        workspace_root="/workspace", transport=transport, sandbox_id="sb-1",
    )

    readiness = lsp.ensure_ready(languages=("python",))

    assert readiness == {"python": True}
    assert any("basedpyright-langserver" in cmd for cmd in transport.calls)
    assert not any("import jedi" in cmd for cmd in transport.calls)


def test_connected_only_probes_python_backend() -> None:
    transport = _Transport()
    lsp = LspClient(
        workspace_root="/workspace", transport=transport, sandbox_id="sb-1",
    )
    assert lsp.connected is True
    assert any("basedpyright-langserver" in cmd for cmd in transport.calls)


def test_ensure_ready_install_command_targets_basedpyright() -> None:
    """``install_missing=True`` runs ``pip install basedpyright`` (NOT jedi)."""

    class _InstallTransport(_Transport):
        async def exec(self, sandbox_id: str, command: str, **kwargs):
            del kwargs
            assert sandbox_id == "sb-1"
            self.calls.append(command)
            if "basedpyright-langserver" in command and "install" not in command:
                return SimpleNamespace(exit_code=1)
            if "pip install" in command and "basedpyright" in command:
                return SimpleNamespace(exit_code=0)
            return SimpleNamespace(exit_code=1)

    transport = _InstallTransport()
    lsp = LspClient(
        workspace_root="/workspace", transport=transport, sandbox_id="sb-1",
    )

    readiness = lsp.ensure_ready(install_missing=True)

    assert readiness == {"python": True}
    assert any(
        "pip install" in cmd and "basedpyright" in cmd
        for cmd in transport.calls
    )
    assert not any("install" in cmd and "jedi" in cmd for cmd in transport.calls)


# ---------------------------------------------------------------------------
# Backend routing — every public method goes through LspAsyncHost.run(child)
# ---------------------------------------------------------------------------


def _patched_host(host_run_results: dict[str, object]) -> object:
    """Build a fake LspAsyncHost whose ``run(fn)`` returns the result for the
    method being called on the dummy child."""

    class _DummyChild:
        async def find_definitions(self, *args, **kwargs):
            return host_run_results.get("find_definitions", [])

        async def find_references(self, *args, **kwargs):
            return host_run_results.get("find_references", [])

        async def hover(self, *args, **kwargs):
            return host_run_results.get("hover")

        async def diagnostics(self, *args, **kwargs):
            return host_run_results.get("diagnostics", [])

    class _FakeHost:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self, fn):
            import asyncio

            return asyncio.run(fn(_DummyChild()))

        def close(self) -> None:
            return None

    return _FakeHost


def test_goto_definition_routes_through_lsp_async_host() -> None:
    sym = SymbolInfo(
        name="foo",
        kind=SymbolKind.FUNCTION,
        file_path="/ws/foo.py",
        line=5,
        character=4,
    )
    fake = _patched_host({"find_definitions": [sym]})
    with patch(
        "sandbox.code_intelligence.language_server.client.LspAsyncHost",
        new=fake,
    ):
        lsp = LspClient(workspace_root="/ws")
        results = lsp.goto_definition("/ws/foo.py", 1, 0)
        assert results == [sym]


def test_find_references_routes_through_lsp_async_host() -> None:
    ref = ReferenceInfo(file_path="/ws/foo.py", line=5, character=2)
    fake = _patched_host({"find_references": [ref]})
    with patch(
        "sandbox.code_intelligence.language_server.client.LspAsyncHost",
        new=fake,
    ):
        lsp = LspClient(workspace_root="/ws")
        results = lsp.find_references("/ws/foo.py", 1, 0)
        assert results == [ref]


def test_hover_routes_through_lsp_async_host() -> None:
    hov = HoverResult(content="docs", language="python")
    fake = _patched_host({"hover": hov})
    with patch(
        "sandbox.code_intelligence.language_server.client.LspAsyncHost",
        new=fake,
    ):
        lsp = LspClient(workspace_root="/ws")
        result = lsp.hover("/ws/foo.py", 1, 0)
        assert result == hov


def test_diagnostics_routes_through_lsp_async_host() -> None:
    diag = Diagnostic(
        file_path="/ws/foo.py",
        line=2,
        character=0,
        severity=DiagnosticSeverity.WARNING,
        message="hint",
    )
    fake = _patched_host({"diagnostics": [diag]})
    with patch(
        "sandbox.code_intelligence.language_server.client.LspAsyncHost",
        new=fake,
    ):
        lsp = LspClient(workspace_root="/ws")
        results = lsp.diagnostics("/ws/foo.py")
        assert results == [diag]


def test_close_releases_host_idempotently() -> None:
    """``close()`` releases the host and is safe to call twice."""
    closed_calls: list[bool] = []

    class _FakeHost:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self, fn):
            import asyncio

            class _C:
                async def find_definitions(self, *a, **k):
                    return []

            return asyncio.run(fn(_C()))

        def close(self) -> None:
            closed_calls.append(True)

    with patch(
        "sandbox.code_intelligence.language_server.client.LspAsyncHost",
        new=_FakeHost,
    ):
        lsp = LspClient(workspace_root="/ws")
        # Trigger lazy host construction.
        lsp.goto_definition("/ws/foo.py", 1, 0)
        lsp.close()
        lsp.close()
    assert closed_calls == [True]
