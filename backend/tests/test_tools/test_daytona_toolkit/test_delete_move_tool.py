"""Tests for daytona_delete_file and daytona_move_file."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from code_intelligence.types import EditResult, MoveSpec, OperationResult
from tools.core.base import ToolExecutionContext, run_tool_safely
from tools.daytona_toolkit.delete_move_tool import (
    daytona_delete_file,
    daytona_move_file,
)


def _ctx(metadata=None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=dict(metadata or {}))


def _run(tool, payload, ctx):
    return asyncio.run(run_tool_safely(tool, payload, ctx))


def _operation_result(
    *,
    success: bool,
    status: str = "committed",
    paths: list[str] | None = None,
    conflict_file: str | None = None,
    conflict_reason: str = "",
) -> OperationResult:
    return OperationResult(
        success=success,
        status=status,  # type: ignore[arg-type]
        files=tuple(
            EditResult(
                success=success,
                file_path=path,
                message=conflict_reason,
                conflict=not success,
                conflict_reason=status if status.startswith("aborted") else "",
            )
            for path in (paths or [])
        ),
        conflict_file=conflict_file,
        conflict_reason=conflict_reason,
        timings={},
    )


def _svc(
    *,
    delete_result: OperationResult | None = None,
    move_result: OperationResult | None = None,
):
    svc = MagicMock()
    svc.delete_file = MagicMock(
        return_value=delete_result
        or _operation_result(success=True, paths=["/ws/gone.py"])
    )
    svc.move_file = MagicMock(
        return_value=move_result
        or _operation_result(success=True, paths=["/ws/src.py", "/ws/dst.py"])
    )
    svc.rebind_sandbox = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# daytona_delete_file
# ---------------------------------------------------------------------------


def test_delete_file_success_routes_through_occ_service() -> None:
    svc = _svc(delete_result=_operation_result(success=True, paths=["/ws/gone.py"]))
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws", "agent_run_id": "run-1"})

    result = _run(daytona_delete_file, {"path": "/ws/gone.py"}, ctx)

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "deleted"
    assert payload["paths"] == ["/ws/gone.py"]
    svc.delete_file.assert_called_once_with(
        ["/ws/gone.py"],
        agent_id="run-1",
        description="delete /ws/gone.py",
    )


def test_delete_file_occ_call_runs_off_active_event_loop_thread() -> None:
    caller_thread = threading.get_ident()

    class ThreadCheckingService:
        def __init__(self) -> None:
            self.rebind_sandbox = MagicMock()
            self.delete_file = MagicMock(side_effect=self._delete_file)

        def _delete_file(self, *args, **kwargs) -> OperationResult:
            assert threading.get_ident() != caller_thread
            return _operation_result(success=True, paths=["/ws/gone.py"])

    svc = ThreadCheckingService()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws", "agent_run_id": "run-1"})

    result = _run(daytona_delete_file, {"path": "/ws/gone.py"}, ctx)

    assert result.is_error is False
    svc.delete_file.assert_called_once_with(
        ["/ws/gone.py"],
        agent_id="run-1",
        description="delete /ws/gone.py",
    )


def test_delete_file_ci_required_when_service_missing() -> None:
    ctx = _ctx({"daytona_sandbox": SimpleNamespace(), "repo_root": "/ws"})
    result = _run(daytona_delete_file, {"path": "/ws/gone.py"}, ctx)
    assert result.is_error is True
    assert "ci_required" in (result.metadata or {})


def test_delete_file_reports_not_found() -> None:
    svc = _svc(
        delete_result=_operation_result(
            success=False,
            status="failed",
            paths=["/ws/missing.py"],
            conflict_file="/ws/missing.py",
            conflict_reason="not_found",
        )
    )
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(daytona_delete_file, {"path": "/ws/missing.py"}, ctx)

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "not_found"
    assert payload["conflict_reason"] == "not_found"


def test_delete_file_reports_aborted_version_without_merge_fallback() -> None:
    svc = _svc(
        delete_result=_operation_result(
            success=False,
            status="aborted_version",
            paths=["/ws/gone.py"],
            conflict_file="/ws/gone.py",
            conflict_reason="file content changed before delete",
        )
    )
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(daytona_delete_file, {"path": "/ws/gone.py"}, ctx)

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "aborted_version"
    assert payload["conflict_reason"] == "file content changed before delete"


def test_delete_file_rejects_repo_root() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(daytona_delete_file, {"path": "/ws"}, ctx)

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert "repo root" in payload["message"]
    svc.delete_file.assert_not_called()


# ---------------------------------------------------------------------------
# daytona_move_file
# ---------------------------------------------------------------------------


def test_move_file_success_routes_through_occ_service() -> None:
    svc = _svc(move_result=_operation_result(success=True, paths=["/ws/src.py", "/ws/dst.py"]))
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws", "agent_run_id": "run-2"})
    result = _run(
        daytona_move_file,
        {"src_path": "/ws/src.py", "target_path": "/ws/dst.py"},
        ctx,
    )
    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "moved"
    assert payload["paths"] == ["/ws/src.py", "/ws/dst.py"]
    svc.move_file.assert_called_once_with(
        [MoveSpec(src_path="/ws/src.py", dst_path="/ws/dst.py", overwrite=False)],
        agent_id="run-2",
        description="move /ws/src.py -> /ws/dst.py",
    )


def test_move_file_occ_call_runs_off_active_event_loop_thread() -> None:
    caller_thread = threading.get_ident()

    class ThreadCheckingService:
        def __init__(self) -> None:
            self.rebind_sandbox = MagicMock()
            self.move_file = MagicMock(side_effect=self._move_file)

        def _move_file(self, *args, **kwargs) -> OperationResult:
            assert threading.get_ident() != caller_thread
            return _operation_result(success=True, paths=["/ws/src.py", "/ws/dst.py"])

    svc = ThreadCheckingService()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws", "agent_run_id": "run-2"})

    result = _run(
        daytona_move_file,
        {"src_path": "/ws/src.py", "target_path": "/ws/dst.py"},
        ctx,
    )

    assert result.is_error is False
    svc.move_file.assert_called_once_with(
        [MoveSpec(src_path="/ws/src.py", dst_path="/ws/dst.py", overwrite=False)],
        agent_id="run-2",
        description="move /ws/src.py -> /ws/dst.py",
    )


def test_move_file_dst_exists_surfaces_as_error() -> None:
    svc = _svc(
        move_result=_operation_result(
            success=False,
            status="failed",
            paths=["/ws/dst.py"],
            conflict_file="/ws/dst.py",
            conflict_reason="dst_exists",
        )
    )
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})
    result = _run(
        daytona_move_file,
        {"src_path": "/ws/src.py", "target_path": "/ws/dst.py"},
        ctx,
    )
    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "dst_exists"


def test_move_file_denies_out_of_scope_src() -> None:
    """Out-of-scope src_path is a hard Deny (guard-pipeline policy)."""
    svc = _svc(
        move_result=_operation_result(
            success=True,
            paths=["/ws/other/a.py", "/ws/allowed/b.py"],
        )
    )
    ctx = _ctx(
        {
            "ci_service": svc,
            "agent_name": "developer",
            "repo_root": "/ws",
            "write_scope": ["allowed/"],
        }
    )
    result = _run(
        daytona_move_file,
        {"src_path": "/ws/other/a.py", "target_path": "/ws/allowed/b.py"},
        ctx,
    )
    assert result.is_error is True
    assert "write-scope policy" in result.output
    assert "/ws/other/a.py" in result.output
    svc.move_file.assert_not_called()


def test_move_file_in_scope_src_extends_write_scope_to_dst() -> None:
    svc = _svc(move_result=_operation_result(success=True, paths=["/ws/a.py", "/ws/b.py"]))
    original_scope = ["a.py"]
    ctx = _ctx(
        {
            "ci_service": svc,
            "agent_name": "developer",
            "repo_root": "/ws",
            "write_scope": original_scope,
        }
    )
    result = _run(
        daytona_move_file,
        {"src_path": "/ws/a.py", "target_path": "/ws/b.py"},
        ctx,
    )
    assert result.is_error is False
    warnings = ctx.metadata.get("coordination_warnings") or []
    assert not any(w.get("category") == "outside_write_scope" for w in warnings)
    assert "b.py" in (ctx.metadata.get("write_scope") or [])
    assert original_scope == ["a.py"]


def test_move_file_rejects_destination_inside_source() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        daytona_move_file,
        {"src_path": "/ws/pkg", "target_path": "/ws/pkg/nested"},
        ctx,
    )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert "inside source" in payload["message"]
    svc.move_file.assert_not_called()


# ---------------------------------------------------------------------------
# is_folder=True branches
# ---------------------------------------------------------------------------


def test_delete_folder_enumerates_and_batches_through_service() -> None:
    svc = _svc(
        delete_result=_operation_result(
            success=True, paths=["/ws/pkg/a.py", "/ws/pkg/sub/b.py"],
        )
    )
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws", "agent_run_id": "run-1"})

    async def fake_list(_ctx, folder):
        assert folder == "/ws/pkg"
        return ["/ws/pkg/a.py", "/ws/pkg/sub/b.py"]

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_delete_file,
            {"path": "/ws/pkg", "is_folder": True},
            ctx,
        )

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "deleted"
    assert payload["paths"] == ["/ws/pkg/a.py", "/ws/pkg/sub/b.py"]
    svc.delete_file.assert_called_once_with(
        ["/ws/pkg/a.py", "/ws/pkg/sub/b.py"],
        agent_id="run-1",
        description="delete /ws/pkg",
    )


def test_delete_folder_reports_not_found_when_missing() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    async def fake_list(_ctx, folder):
        raise FileNotFoundError(folder)

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_delete_file,
            {"path": "/ws/missing_dir", "is_folder": True},
            ctx,
        )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "not_found"
    svc.delete_file.assert_not_called()


def test_delete_folder_with_is_folder_on_regular_file_fails() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    async def fake_list(_ctx, folder):
        raise NotADirectoryError(folder)

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_delete_file,
            {"path": "/ws/not_a_dir.py", "is_folder": True},
            ctx,
        )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert "is_folder=True" in payload["message"]
    svc.delete_file.assert_not_called()


def test_delete_folder_empty_short_circuits_to_deleted() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    async def fake_list(_ctx, folder):
        return []

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_delete_file,
            {"path": "/ws/empty_dir", "is_folder": True},
            ctx,
        )

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "deleted"
    assert payload["paths"] == []
    svc.delete_file.assert_not_called()


def test_move_folder_enumerates_and_remaps_prefix() -> None:
    svc = _svc(
        move_result=_operation_result(
            success=True,
            paths=[
                "/ws/src_pkg/a.py",
                "/ws/dst_pkg/a.py",
                "/ws/src_pkg/sub/b.py",
                "/ws/dst_pkg/sub/b.py",
            ],
        )
    )
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws", "agent_run_id": "run-2"})

    async def fake_list(_ctx, folder):
        assert folder == "/ws/src_pkg"
        return ["/ws/src_pkg/a.py", "/ws/src_pkg/sub/b.py"]

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_move_file,
            {
                "src_path": "/ws/src_pkg",
                "target_path": "/ws/dst_pkg",
                "is_folder": True,
            },
            ctx,
        )

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "moved"
    specs = svc.move_file.call_args.args[0]
    assert specs == [
        MoveSpec(
            src_path="/ws/src_pkg/a.py",
            dst_path="/ws/dst_pkg/a.py",
            overwrite=False,
        ),
        MoveSpec(
            src_path="/ws/src_pkg/sub/b.py",
            dst_path="/ws/dst_pkg/sub/b.py",
            overwrite=False,
        ),
    ]


def test_move_folder_not_found_returns_not_found() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    async def fake_list(_ctx, folder):
        raise FileNotFoundError(folder)

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_move_file,
            {
                "src_path": "/ws/missing",
                "target_path": "/ws/dst",
                "is_folder": True,
            },
            ctx,
        )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "not_found"
    svc.move_file.assert_not_called()


def test_move_folder_with_is_folder_on_regular_file_fails() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    async def fake_list(_ctx, folder):
        raise NotADirectoryError(folder)

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_move_file,
            {
                "src_path": "/ws/a.py",
                "target_path": "/ws/b.py",
                "is_folder": True,
            },
            ctx,
        )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert "is_folder=True" in payload["message"]
    svc.move_file.assert_not_called()


def test_move_folder_empty_short_circuits_to_moved() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    async def fake_list(_ctx, folder):
        return []

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_move_file,
            {
                "src_path": "/ws/empty_dir",
                "target_path": "/ws/dst_dir",
                "is_folder": True,
            },
            ctx,
        )

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "moved"
    assert payload["paths"] == []
    svc.move_file.assert_not_called()


# ---------------------------------------------------------------------------
# Write-scope policy via guard pipeline (Q3/Q4 behavior)
# ---------------------------------------------------------------------------


def _coord_ctx(svc, **extra) -> ToolExecutionContext:
    metadata = {
        "ci_service": svc,
        "agent_name": "developer",
        "daytona_cwd": "/ws",
        "repo_root": "/ws",
    }
    metadata.update(extra)
    return _ctx(metadata)


def test_delete_file_denies_out_of_scope_single_path() -> None:
    """Outside-scope single-file delete is a hard Deny (upgraded from advisory)."""
    svc = _svc()
    ctx = _coord_ctx(svc, write_scope=["allowed/"])

    result = _run(daytona_delete_file, {"path": "/ws/other/gone.py"}, ctx)

    assert result.is_error is True
    assert "write-scope policy" in result.output
    assert "/ws/other/gone.py" in result.output
    svc.delete_file.assert_not_called()


def test_delete_file_hard_block_reads_path_not_file_path() -> None:
    """Regression: p10 guard must read DaytonaDeleteFileInput.path (was file_path)."""
    svc = _svc()
    ctx = _coord_ctx(svc, write_scope=["allowed/tests/"])

    result = _run(
        daytona_delete_file,
        {"path": "/ws/allowed/tests/test_foo.py"},
        ctx,
    )

    assert result.is_error is True
    assert "BLOCKED_TEST_FILE_EDIT" in result.output
    svc.delete_file.assert_not_called()


def test_delete_folder_member_offender_listing() -> None:
    """is_folder=True: tool body enumerates, denies listing only outside-scope members."""
    svc = _svc()
    ctx = _coord_ctx(svc, write_scope=["pkg/"])

    async def fake_list(_ctx, folder):
        return [
            "/ws/pkg/a.py",
            "/ws/pkg/b.py",
            "/ws/other/c.py",
        ]

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_delete_file,
            {"path": "/ws/pkg", "is_folder": True},
            ctx,
        )

    assert result.is_error is True
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert "/ws/other/c.py" in result.output
    assert "/ws/pkg/a.py" not in result.output
    assert "/ws/pkg/b.py" not in result.output
    svc.delete_file.assert_not_called()


def test_move_src_deny_blocks_out_of_scope_src_test_file() -> None:
    """Test-file src block still fires (p10) in coordinated lanes."""
    svc = _svc()
    ctx = _coord_ctx(svc, write_scope=["src/"])

    result = _run(
        daytona_move_file,
        {"src_path": "/ws/src/tests/test_a.py", "target_path": "/ws/src/moved.py"},
        ctx,
    )

    assert result.is_error is True
    assert "BLOCKED_TEST_FILE_EDIT" in result.output
    svc.move_file.assert_not_called()


def test_move_dst_advisory_suppressed_when_src_in_scope() -> None:
    """src in scope → dst advisory guard returns Allow (naming op, not widening)."""
    svc = _svc(
        move_result=_operation_result(success=True, paths=["/ws/src/a.py", "/ws/other/b.py"])
    )
    ctx = _coord_ctx(svc, write_scope=["src/"])

    result = _run(
        daytona_move_file,
        {"src_path": "/ws/src/a.py", "target_path": "/ws/other/b.py"},
        ctx,
    )

    assert result.is_error is False
    warnings = ctx.metadata.get("coordination_warnings") or []
    assert not any(w.get("category") == "outside_write_scope" for w in warnings)


def test_move_folder_member_offender_listing() -> None:
    """is_folder=True move: member-level scope check denies listing only offenders."""
    svc = _svc()
    ctx = _coord_ctx(svc, write_scope=["pkg/"])

    async def fake_list(_ctx, folder):
        return [
            "/ws/pkg/a.py",
            "/ws/pkg/b.py",
            "/ws/other/stowaway.py",
        ]

    with patch(
        "tools.daytona_toolkit.delete_move_tool._list_folder_files",
        new=fake_list,
    ):
        result = _run(
            daytona_move_file,
            {
                "src_path": "/ws/pkg",
                "target_path": "/ws/moved_pkg",
                "is_folder": True,
            },
            ctx,
        )

    assert result.is_error is True
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert "/ws/other/stowaway.py" in result.output
    assert "/ws/pkg/a.py" not in result.output
    svc.move_file.assert_not_called()
