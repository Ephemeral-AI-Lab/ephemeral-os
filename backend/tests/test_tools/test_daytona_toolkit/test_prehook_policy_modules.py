"""Direct tests for Daytona pre-hook policy modules."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from code_intelligence.hashing import content_hash
from code_intelligence.types import SemanticFileChange, SemanticRenamePlan, SymbolInfo, SymbolKind
from tools.core.base import ToolExecutionContext
from tools.core.hooks import ToolHookRegistry
from tools.daytona_toolkit.delete_move_tool import (
    DaytonaDeleteFileInput,
    DaytonaMoveFileInput,
)
from tools.daytona_toolkit.hooks.prehook import (
    move_src_scope_deny,
    rename_scope_policy,
    repo_operation_guard,
    write_scope_deny,
)
from tools.daytona_toolkit.rename_tool import DaytonaRenameSymbolsInput


def _ctx(metadata: dict | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/ws"), metadata=metadata or {})


def _coord_ctx(**extra: object) -> ToolExecutionContext:
    metadata = {
        "agent_name": "developer",
        "repo_root": "/ws",
        "daytona_cwd": "/ws",
    }
    metadata.update(extra)
    return _ctx(metadata)


def _run(awaitable):
    return asyncio.run(awaitable)


def _rename_plan(*paths: str, new_name: str = "bar") -> SemanticRenamePlan:
    base = "def foo(): pass\n"
    return SemanticRenamePlan(
        new_name=new_name,
        origin=(paths[0] if paths else "/ws/a.py", 1, 4),
        changes=tuple(
            SemanticFileChange(
                file_path=path,
                base_content=base,
                base_hash=content_hash(base),
                final_content=f"def {new_name}(): pass\n",
            )
            for path in paths
        ),
    )


def _symbol(path: str = "/ws/a.py") -> SymbolInfo:
    return SymbolInfo(
        name="foo",
        kind=SymbolKind.FUNCTION,
        file_path=path,
        line=1,
        character=4,
        signature="def foo()",
    )


def _rename_service(plan: SemanticRenamePlan):
    return SimpleNamespace(
        symbol_index=SimpleNamespace(
            ensure_built=MagicMock(),
            find=MagicMock(return_value=[_symbol()]),
        ),
        rename_symbol_plan=MagicMock(return_value=plan),
    )


def test_repo_guard_blocks_delete_of_repo_root() -> None:
    ctx = _coord_ctx()
    args = DaytonaDeleteFileInput(path="/ws")

    outcome = _run(repo_operation_guard.hook("daytona_delete_file", args, ctx))

    assert outcome.has_error is True
    assert "repo root" in (outcome.error_message or "")


def test_repo_guard_blocks_nested_move() -> None:
    ctx = _coord_ctx()
    args = DaytonaMoveFileInput(src_path="/ws/pkg", target_path="/ws/pkg/sub")

    outcome = _run(repo_operation_guard.hook("daytona_move_file", args, ctx))

    assert outcome.has_error is True
    assert "inside source" in (outcome.error_message or "")


def test_write_scope_deny_checks_folder_members_before_delete_body() -> None:
    svc = MagicMock()
    svc.list_folder_files.return_value = ["/ws/pkg/a.py", "/ws/other/b.py"]
    ctx = _coord_ctx(write_scope=["pkg/"], ci_service=svc)
    args = DaytonaDeleteFileInput(path="/ws/pkg", is_folder=True)

    outcome = _run(write_scope_deny.hook("daytona_delete_file", args, ctx))

    assert outcome.has_error is True
    assert "folder members" in (outcome.error_message or "")
    assert "/ws/other/b.py" in (outcome.error_message or "")
    assert "/ws/pkg/a.py" not in (outcome.error_message or "")
    svc.list_folder_files.assert_called_once_with("/ws/pkg")


def test_move_src_scope_deny_checks_folder_members_before_move_body() -> None:
    svc = MagicMock()
    svc.list_folder_files.return_value = ["/ws/pkg/a.py", "/ws/other/b.py"]
    ctx = _coord_ctx(write_scope=["pkg/"], ci_service=svc)
    args = DaytonaMoveFileInput(
        src_path="/ws/pkg",
        target_path="/ws/moved_pkg",
        is_folder=True,
    )

    outcome = _run(move_src_scope_deny.hook("daytona_move_file", args, ctx))

    assert outcome.has_error is True
    assert "folder members" in (outcome.error_message or "")
    assert "/ws/other/b.py" in (outcome.error_message or "")
    assert "/ws/pkg/a.py" not in (outcome.error_message or "")
    svc.list_folder_files.assert_called_once_with("/ws/pkg")


def test_rename_scope_policy_caches_allowed_plan() -> None:
    plan = _rename_plan("/ws/pkg/a.py")
    svc = _rename_service(plan)
    ctx = _coord_ctx(ci_service=svc, write_scope=["pkg/"])
    args = DaytonaRenameSymbolsInput(symbol="foo", new_name="bar")

    outcome = _run(rename_scope_policy.hook("daytona_rename_symbol", args, ctx))

    assert outcome.has_error is False
    cached = ctx.metadata.get("_daytona_rename_preplan")
    assert cached["plan"] is plan
    assert cached["resolved_path"] == "/ws/a.py"


def test_rename_scope_policy_blocks_planned_out_of_scope_file() -> None:
    svc = _rename_service(_rename_plan("/ws/pkg/a.py", "/ws/other/b.py"))
    ctx = _coord_ctx(ci_service=svc, write_scope=["pkg/"])
    args = DaytonaRenameSymbolsInput(symbol="foo", new_name="bar")

    outcome = _run(rename_scope_policy.hook("daytona_rename_symbol", args, ctx))

    assert outcome.has_error is True
    assert "daytona_rename_symbol blocked by write-scope policy" in (
        outcome.error_message or ""
    )
    assert "/ws/other/b.py" in (outcome.error_message or "")
    assert "_daytona_rename_preplan" not in ctx.metadata


def test_new_pre_hooks_register_once() -> None:
    registry = ToolHookRegistry()

    repo_operation_guard.register(registry)
    repo_operation_guard.register(registry)
    rename_scope_policy.register(registry)
    rename_scope_policy.register(registry)

    assert len(registry.matching("daytona_delete_file", "pre")) == 1
    assert len(registry.matching("daytona_move_file", "pre")) == 1
    assert len(registry.matching("daytona_rename_symbol", "pre")) == 1
