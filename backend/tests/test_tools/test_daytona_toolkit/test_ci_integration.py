"""Tests for shared CI runtime helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_intelligence.types import EditResult, OperationResult
from tools.core.base import ToolExecutionContext
from tools.core.ci_runtime import (
    CiOperationChange,
    commit_ci_operation,
    exec_ci_process_operation,
    get_ci_service,
)
from tools.daytona_toolkit.ci_integration import destructive_shell_command_error


def _ctx(metadata=None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


# ---------------------------------------------------------------------------
# get_ci_service
# ---------------------------------------------------------------------------


def test_get_ci_service_returns_none_when_missing():
    ctx = _ctx()
    assert get_ci_service(ctx) is None


def test_get_ci_service_returns_value():
    svc = MagicMock()
    ctx = _ctx({"ci_service": svc})
    assert get_ci_service(ctx) is svc


# ---------------------------------------------------------------------------
# commit_ci_operation — unified OCC entry point
# ---------------------------------------------------------------------------


def test_commit_ci_operation_forwards_to_service():
    svc = MagicMock()
    svc.commit_operation_against_base.return_value = OperationResult(
        success=True,
        status="committed",
        files=(
            EditResult(success=True, file_path="/repo/a.py"),
            EditResult(success=True, file_path="/repo/b.py"),
        ),
    )
    ctx = _ctx({"ci_service": svc, "agent_name": "developer"})

    result = commit_ci_operation(
        ctx,
        [
            CiOperationChange(
                file_path="/repo/a.py",
                base_content="a = 1\n",
                final_content="a = 2\n",
                base_existed=True,
            ),
            CiOperationChange(
                file_path="/repo/b.py",
                base_content=None,
                final_content="b = 1\n",
                base_existed=False,
            ),
        ],
        edit_type="codeact",
        description="one codeact op",
    )

    assert result.success is True
    svc.commit_operation_against_base.assert_called_once()
    changes = svc.commit_operation_against_base.call_args.args[0]
    assert [change.file_path for change in changes] == ["/repo/a.py", "/repo/b.py"]
    assert changes[0].base_hash == hashlib.sha256(b"a = 1\n").hexdigest()[:16]
    assert changes[1].base_content == ""
    assert changes[1].base_existed is False
    assert svc.commit_operation_against_base.call_args.kwargs == {
        "agent_id": "developer",
        "edit_type": "codeact",
        "description": "one codeact op",
    }


def test_commit_ci_operation_mirrors_team_edit():
    svc = MagicMock()
    svc.commit_operation_against_base.return_value = OperationResult(
        success=True,
        status="committed",
        files=(EditResult(success=True, file_path="/repo/file.py"),),
    )
    svc.arbiter = MagicMock()
    team_arbiter = MagicMock()
    team_arbiter.initialized = True
    team_run = SimpleNamespace(arbiter=team_arbiter)
    ctx = _ctx(
        {
            "ci_service": svc,
            "team_run_id": "team-1",
            "agent_run_id": "agent-run-1",
            "agent_name": "developer",
            "work_item_id": "task-7",
        }
    )

    with patch("tools.core.ci_runtime._get_team_run", return_value=team_run):
        result = commit_ci_operation(
            ctx,
            [
                CiOperationChange(
                    file_path="/repo/file.py",
                    base_content="before\n",
                    final_content="after\n",
                    base_existed=True,
                )
            ],
            edit_type="codeact",
            description="operation commit",
        )

    assert result.success is True
    team_arbiter.record_edit.assert_called_once_with(
        file_path="/repo/file.py",
        team_run_id="team-1",
        agent_run_id="agent-run-1",
        task_id="task-7",
        edit_type="codeact",
        old_hash=hashlib.sha256(b"before\n").hexdigest()[:16],
        new_hash=hashlib.sha256(b"after\n").hexdigest()[:16],
        description="operation commit",
    )


def test_commit_ci_operation_raises_without_service():
    ctx = _ctx()
    with pytest.raises(RuntimeError):
        commit_ci_operation(
            ctx,
            [
                CiOperationChange(
                    file_path="/repo/file.py",
                    base_content="",
                    final_content="hi\n",
                    base_existed=False,
                )
            ],
            edit_type="write",
            description="test",
        )


@pytest.mark.asyncio
async def test_exec_ci_process_operation_delegates_audited_process_call():
    sandbox = MagicMock()
    svc = MagicMock()
    svc.exec_process_operation = AsyncMock(return_value=SimpleNamespace(result="ok", exit_code=0))
    ctx = _ctx(
        {
            "ci_service": svc,
            "agent_name": "developer",
            "team_run_id": "team-1",
            "agent_run_id": "agent-1",
            "work_item_id": "task-1",
        }
    )

    result = await exec_ci_process_operation(
        ctx,
        sandbox,
        "echo ok",
        timeout=12,
        description="daytona_codeact shell",
        edit_type="codeact",
    )

    assert result.result == "ok"
    svc.exec_process_operation.assert_awaited_once_with(
        sandbox,
        "echo ok",
        timeout=12,
        description="daytona_codeact shell",
        edit_type="codeact",
        agent_id="developer",
        team_run_id="team-1",
        agent_run_id="agent-1",
        task_id="task-1",
    )


# ---------------------------------------------------------------------------
# destructive_shell_command_error — shell policy regression tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /testbed/dask",
        "rm -rF /testbed",
        "rm --recursive /workspace/project",
        "mv /testbed/dask /tmp/trash",
        "mv /home/user /tmp",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "rm -rf .",
        "echo ok; rm -rf /testbed/dask",
    ],
)
def test_destructive_shell_command_error_blocks(command):
    err = destructive_shell_command_error(command)
    assert err is not None, f"Should block: {command}"
    assert "BLOCKED" in err


@pytest.mark.parametrize(
    "command",
    [
        "rm /testbed/dask/file.py",
        "rm -f /testbed/dask/file.py",
        "mv /testbed/dask/file.py /testbed/dask/new.py",
        "cp -r /testbed/dask /testbed/backup",
        "pytest /testbed/dask/tests",
        "python -c 'import os'",
        "",
    ],
)
def test_destructive_shell_command_error_allows_safe(command):
    err = destructive_shell_command_error(command)
    assert err is None, f"Should allow: {command}"
