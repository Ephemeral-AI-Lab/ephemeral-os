"""Tests for daytona_delete_file and daytona_move_file."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit.delete_move_tool import (
    daytona_delete_file,
    daytona_move_file,
)


def _ctx(metadata=None) -> ToolExecutionContext:
    metadata = dict(metadata or {})
    if "ci_service" in metadata and "daytona_sandbox" not in metadata:
        metadata["daytona_sandbox"] = SimpleNamespace()
    if "ci_service" in metadata and "repo_root" not in metadata:
        metadata["repo_root"] = "/ws"
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


def _run(tool, payload, ctx):
    return asyncio.run(tool.execute(tool.input_model(**payload), ctx))


def _shell_svc(
    *,
    stdout: str = "ok",
    exit_code: int = 0,
    changed_paths: list[str] | None = None,
):
    svc = MagicMock()
    svc.exec_process_operation = AsyncMock(
        return_value=SimpleNamespace(
            result=f"{stdout}\n__CODEX_EXIT_CODE__={exit_code}\n",
            exit_code=exit_code,
            changed_paths=changed_paths or [],
            files_written=len(changed_paths or []),
        )
    )
    return svc


# ---------------------------------------------------------------------------
# daytona_delete_file
# ---------------------------------------------------------------------------


def test_delete_file_success_routes_one_shell_command_through_ci() -> None:
    svc = _shell_svc(changed_paths=["/ws/gone.py"])
    ctx = _ctx({"ci_service": svc})

    result = _run(daytona_delete_file, {"file_path": "/ws/gone.py"}, ctx)

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "deleted"
    assert payload["paths"] == ["/ws/gone.py"]
    svc.exec_process_operation.assert_awaited_once()
    command = svc.exec_process_operation.await_args.args[1]
    assert "rm -f -- /ws/gone.py" in command


def test_delete_file_ci_required_when_service_missing() -> None:
    # No ci_service in context -> tool should surface ci_required error.
    ctx = _ctx({"daytona_sandbox": SimpleNamespace()})
    result = _run(daytona_delete_file, {"file_path": "/ws/gone.py"}, ctx)
    assert result.is_error is True
    assert "ci_required" in (result.metadata or {})


def test_delete_file_reports_not_found() -> None:
    svc = _shell_svc(stdout="Path does not exist: /ws/missing.py", exit_code=66)
    ctx = _ctx({"ci_service": svc})
    result = _run(daytona_delete_file, {"file_path": "/ws/missing.py"}, ctx)
    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "not_found"
    assert payload["conflict_reason"] == "not_found"


def test_delete_file_directory_requires_recursive_flag() -> None:
    svc = _shell_svc(
        stdout="Path is a directory; pass recursive=true to delete recursively: /ws/pkg",
        exit_code=73,
    )
    ctx = _ctx({"ci_service": svc})
    result = _run(daytona_delete_file, {"file_path": "/ws/pkg"}, ctx)
    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert payload["conflict_reason"] == "recursive_required"


def test_delete_file_records_write_scope_warning() -> None:
    """Out-of-scope delete still proceeds but records a coordination warning."""
    svc = _shell_svc(changed_paths=["/ws/other/file.py"])
    ctx = _ctx(
        {
            "ci_service": svc,
            "agent_name": "developer",
            "repo_root": "/ws",
            "write_scope": ["allowed/"],
        }
    )
    result = _run(daytona_delete_file, {"file_path": "/ws/other/file.py"}, ctx)
    assert result.is_error is False
    svc.exec_process_operation.assert_awaited_once()
    warnings = ctx.metadata.get("coordination_warnings") or []
    assert any(w.get("category") == "outside_write_scope" for w in warnings)


def test_delete_file_recursive_routes_one_shell_command_through_ci() -> None:
    svc = _shell_svc(changed_paths=["/ws/pkg/a.py", "/ws/pkg/b.py"])
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        daytona_delete_file,
        {"file_path": "/ws/pkg", "recursive": True},
        ctx,
    )

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "deleted"
    assert payload["paths"] == ["/ws/pkg/a.py", "/ws/pkg/b.py"]
    svc.exec_process_operation.assert_awaited_once()
    command = svc.exec_process_operation.await_args.args[1]
    assert "rm -rf -- /ws/pkg" in command


def test_delete_file_recursive_reports_missing_path() -> None:
    svc = _shell_svc(stdout="Path does not exist: /ws/missing", exit_code=66)
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        daytona_delete_file,
        {"file_path": "/ws/missing", "recursive": True},
        ctx,
    )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "not_found"


def test_delete_file_recursive_rejects_repo_root() -> None:
    svc = _shell_svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        daytona_delete_file,
        {"file_path": "/ws", "recursive": True},
        ctx,
    )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert "repo root" in payload["message"]
    svc.exec_process_operation.assert_not_awaited()


# ---------------------------------------------------------------------------
# daytona_move_file
# ---------------------------------------------------------------------------


def test_move_file_success_routes_one_shell_command_through_ci() -> None:
    svc = _shell_svc(changed_paths=["/ws/src.py", "/ws/dst.py"])
    ctx = _ctx({"ci_service": svc})
    result = _run(
        daytona_move_file,
        {"src_path": "/ws/src.py", "dst_path": "/ws/dst.py"},
        ctx,
    )
    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "moved"
    assert payload["paths"] == ["/ws/src.py", "/ws/dst.py"]
    svc.exec_process_operation.assert_awaited_once()
    command = svc.exec_process_operation.await_args.args[1]
    assert "mv -T -- /ws/src.py /ws/dst.py" in command


def test_move_file_overwrite_removes_destination_first() -> None:
    svc = _shell_svc(changed_paths=["/ws/a", "/ws/b"])
    ctx = _ctx({"ci_service": svc})
    _run(
        daytona_move_file,
        {"src_path": "/ws/a", "dst_path": "/ws/b", "overwrite": True},
        ctx,
    )
    command = svc.exec_process_operation.await_args.args[1]
    assert "rm -f -- /ws/b" in command


def test_move_file_dst_exists_without_overwrite() -> None:
    svc = _shell_svc(
        stdout="Destination exists: /ws/dst.py (pass overwrite=True to replace)",
        exit_code=74,
    )
    ctx = _ctx({"ci_service": svc})
    result = _run(
        daytona_move_file,
        {"src_path": "/ws/src.py", "dst_path": "/ws/dst.py"},
        ctx,
    )
    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "dst_exists"


def test_move_file_directory_requires_recursive_flag() -> None:
    svc = _shell_svc(
        stdout="Path is a directory; pass recursive=true to move recursively: /ws/src",
        exit_code=73,
    )
    ctx = _ctx({"ci_service": svc})
    result = _run(
        daytona_move_file,
        {"src_path": "/ws/src", "dst_path": "/ws/dst"},
        ctx,
    )
    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert payload["conflict_reason"] == "recursive_required"


def test_move_file_records_write_scope_warning_for_out_of_scope_src() -> None:
    svc = _shell_svc(changed_paths=["/ws/other/a.py", "/ws/allowed/b.py"])
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
        {"src_path": "/ws/other/a.py", "dst_path": "/ws/allowed/b.py"},
        ctx,
    )
    assert result.is_error is False
    svc.exec_process_operation.assert_awaited_once()
    warnings = ctx.metadata.get("coordination_warnings") or []
    assert any(w.get("category") == "outside_write_scope" for w in warnings)


def test_move_file_recursive_routes_one_shell_command_through_ci() -> None:
    svc = _shell_svc(changed_paths=["/ws/renamed/a.py", "/ws/renamed/b.py"])
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        daytona_move_file,
        {"src_path": "/ws/pkg", "dst_path": "/ws/renamed", "recursive": True},
        ctx,
    )

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "moved"
    assert payload["paths"] == ["/ws/renamed/a.py", "/ws/renamed/b.py"]
    svc.exec_process_operation.assert_awaited_once()
    command = svc.exec_process_operation.await_args.args[1]
    assert "mv -T -- /ws/pkg /ws/renamed" in command


def test_move_file_recursive_rejects_destination_inside_source() -> None:
    svc = _shell_svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        daytona_move_file,
        {"src_path": "/ws/pkg", "dst_path": "/ws/pkg/nested", "recursive": True},
        ctx,
    )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert "inside source" in payload["message"]
    svc.exec_process_operation.assert_not_awaited()


def test_move_file_recursive_rejects_destination_containing_source() -> None:
    svc = _shell_svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        daytona_move_file,
        {"src_path": "/ws/pkg/nested", "dst_path": "/ws/pkg", "recursive": True},
        ctx,
    )

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "failed"
    assert "contains source" in payload["message"]
    svc.exec_process_operation.assert_not_awaited()
