"""Unit tests for the CiBackend Protocol selection + in-process/RPC backends.

Covers the four-truth-table selection logic in
``CodeIntelligenceService._select_backend`` (env x transport x sandbox_id),
the InProcessCiBackend behavioral defaults (e.g. empty workspace returns no
symbols), and that every public op on the RpcCiBackend stub raises
``NotImplementedError``.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from sandbox.code_intelligence.backend import (
    CiBackend,
    InProcessCiBackend,
    RpcCiBackend,
)
from sandbox.code_intelligence.core.types import (
    DeleteSpec,
    EditRequest,
    EditSpec,
    MoveSpec,
    OperationChange,
    WriteSpec,
)
from sandbox.code_intelligence.registry import dispose_all_code_intelligence
from sandbox.code_intelligence.service import CodeIntelligenceService


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure tests don't leak EOS_CI_IN_SANDBOX state across cases."""
    monkeypatch.delenv("EOS_CI_IN_SANDBOX", raising=False)


# ---------------------------------------------------------------------------
# InProcessCiBackend behavior
# ---------------------------------------------------------------------------


def test_inprocess_query_symbols_returns_empty_for_unbuilt_workspace(tmp_path: Path) -> None:
    backend = InProcessCiBackend(sandbox_id="sb-1", workspace_root=str(tmp_path))
    assert backend.query_symbols("foo") == []


def test_inprocess_is_initialized_starts_false(tmp_path: Path) -> None:
    backend = InProcessCiBackend(sandbox_id="sb-2", workspace_root=str(tmp_path))
    assert backend.is_initialized is False


def test_inprocess_exposes_required_components(tmp_path: Path) -> None:
    backend = InProcessCiBackend(sandbox_id="sb-3", workspace_root=str(tmp_path))
    # Load-bearing attributes for callers that read internals (workspace.py,
    # code_intelligence_api.py, several tests).
    assert backend.symbol_index is not None
    assert backend.arbiter is not None
    assert backend.time_machine is not None
    assert backend.patcher is not None
    assert backend.lsp_client is not None
    assert backend._content is not None
    assert backend._write_coordinator is not None
    assert backend._mutations is not None
    assert backend._command_executor is not None


# ---------------------------------------------------------------------------
# CodeIntelligenceService backend-selection truth table
# ---------------------------------------------------------------------------


def test_select_inprocess_when_flag_unset(tmp_path: Path) -> None:
    svc = CodeIntelligenceService(sandbox_id="sb-a", workspace_root=str(tmp_path))
    assert type(svc._impl) is InProcessCiBackend


def test_select_rpc_when_flag_on_with_transport_and_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EOS_CI_IN_SANDBOX", "1")
    transport = MagicMock(name="SandboxTransport")
    svc = CodeIntelligenceService(
        sandbox_id="sb-b",
        workspace_root=str(tmp_path),
        transport=transport,
    )
    assert type(svc._impl) is RpcCiBackend


def test_select_inprocess_when_flag_on_but_no_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EOS_CI_IN_SANDBOX", "1")
    svc = CodeIntelligenceService(sandbox_id="sb-c", workspace_root=str(tmp_path))
    assert type(svc._impl) is InProcessCiBackend


def test_select_inprocess_when_flag_on_but_empty_sandbox_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EOS_CI_IN_SANDBOX", "1")
    transport = MagicMock(name="SandboxTransport")
    svc = CodeIntelligenceService(
        sandbox_id="",
        workspace_root=str(tmp_path),
        transport=transport,
    )
    assert type(svc._impl) is InProcessCiBackend


def test_select_inprocess_when_flag_set_to_other_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EOS_CI_IN_SANDBOX", "true")  # not exactly "1"
    transport = MagicMock(name="SandboxTransport")
    svc = CodeIntelligenceService(
        sandbox_id="sb-d",
        workspace_root=str(tmp_path),
        transport=transport,
    )
    assert type(svc._impl) is InProcessCiBackend


def test_select_inprocess_when_flag_off_with_transport_and_id(tmp_path: Path) -> None:
    transport = MagicMock(name="SandboxTransport")
    svc = CodeIntelligenceService(
        sandbox_id="sb-e",
        workspace_root=str(tmp_path),
        transport=transport,
    )
    assert type(svc._impl) is InProcessCiBackend


# ---------------------------------------------------------------------------
# RpcCiBackend stub: every method raises NotImplementedError
# ---------------------------------------------------------------------------


def _build_rpc_backend() -> RpcCiBackend:
    transport = MagicMock(name="SandboxTransport")
    return RpcCiBackend(
        sandbox_id="sb-rpc",
        workspace_root="/workspace",
        transport=transport,
    )


def test_rpc_backend_init_attributes() -> None:
    backend = _build_rpc_backend()
    assert backend.sandbox_id == "sb-rpc"
    assert backend.workspace_root == "/workspace"
    assert backend.is_initialized is False
    assert backend._transport is not None


_RPC_OP_DUMMY_ARGS: dict[str, tuple[Any, dict[str, Any]]] = {
    "ensure_initialized": ((), {"wait": True}),
    "warmup": ((), {}),
    "rebind_sandbox": ((object(),), {}),
    "find_definitions": (("/tmp/x.py", "sym"), {"line": 1, "character": 0}),
    "find_references": (("/tmp/x.py", "sym"), {"line": 1, "character": 0}),
    "hover": (("/tmp/x.py", 1, 0), {}),
    "diagnostics": (("/tmp/x.py",), {}),
    "query_symbols": (("foo",), {}),
    "apply_edit": (
        (
            EditRequest(
                file_path="/tmp/x.py",
                old_text="a",
                new_text="b",
            ),
        ),
        {},
    ),
    "commit_operation_against_base": (
        (
            (
                OperationChange(
                    file_path="/tmp/x.py",
                    base_content="",
                    base_hash="",
                    final_content="x = 1\n",
                ),
            ),
        ),
        {"edit_type": "edit"},
    ),
    "commit_specs_many": (((),), {}),
    "list_folder_files": (("/tmp",), {}),
    "write_file": (
        (
            (WriteSpec(file_path="/tmp/x.py", content="x = 1\n"),),
        ),
        {},
    ),
    "edit_file": (
        (
            (EditSpec(file_path="/tmp/x.py", edits=()),),
        ),
        {},
    ),
    "delete_file": (
        (
            (DeleteSpec(path="/tmp/x.py"),),
        ),
        {},
    ),
    "move_file": (
        (
            (MoveSpec(src_path="/tmp/x.py", dst_path="/tmp/y.py"),),
        ),
        {},
    ),
    "undo_last_edit": (("/tmp/x.py",), {}),
    "status": ((), {}),
    "get_telemetry": ((), {}),
    "dispose": ((), {}),
}


@pytest.mark.parametrize("op_name", sorted(_RPC_OP_DUMMY_ARGS.keys()))
def test_rpc_backend_sync_op_raises_not_implemented(op_name: str) -> None:
    backend = _build_rpc_backend()
    args, kwargs = _RPC_OP_DUMMY_ARGS[op_name]
    method = getattr(backend, op_name)
    with pytest.raises(NotImplementedError):
        method(*args, **kwargs)


@pytest.mark.asyncio
async def test_rpc_backend_cmd_raises_not_implemented() -> None:
    backend = _build_rpc_backend()
    sandbox = MagicMock()
    with pytest.raises(NotImplementedError):
        await backend.cmd(sandbox, "echo hi")


# ---------------------------------------------------------------------------
# Protocol shape — sanity check that InProcessCiBackend implements every CiBackend op
# ---------------------------------------------------------------------------


def test_inprocess_satisfies_protocol_shape() -> None:
    """Every public method declared on CiBackend exists on InProcessCiBackend."""
    declared = {
        name
        for name, value in inspect.getmembers(CiBackend)
        if not name.startswith("_") and callable(value)
    }
    implemented = {
        name
        for name, value in inspect.getmembers(InProcessCiBackend)
        if not name.startswith("_") and callable(value)
    }
    missing = declared - implemented
    assert missing == set(), f"InProcessCiBackend missing methods: {missing}"


def test_rpc_satisfies_protocol_shape() -> None:
    """Every public method declared on CiBackend exists on RpcCiBackend."""
    declared = {
        name
        for name, value in inspect.getmembers(CiBackend)
        if not name.startswith("_") and callable(value)
    }
    implemented = {
        name
        for name, value in inspect.getmembers(RpcCiBackend)
        if not name.startswith("_") and callable(value)
    }
    missing = declared - implemented
    assert missing == set(), f"RpcCiBackend missing methods: {missing}"
