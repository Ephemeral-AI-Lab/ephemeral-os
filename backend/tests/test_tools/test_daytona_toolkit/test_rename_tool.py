"""Tests for tools.daytona_toolkit.rename_tool.

The tool resolves the symbol through :class:`SymbolIndex`, builds a rename
plan, then delegates the already-built plan to ``svc.commit_rename_plan``.
These tests mock the service and cover: identifier validation, ambiguity,
empty-plan handling, OCC commit success / abort translation, and write-scope
gating.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from code_intelligence.hashing import content_hash
from code_intelligence.types import (
    EditResult,
    OperationResult,
    SemanticFileChange,
    SemanticRenamePlan,
    SymbolInfo,
    SymbolKind,
)
from tools.core.base import ToolExecutionContext, run_tool_safely
from tools.daytona_toolkit.rename_tool import daytona_rename_symbol


def _ctx(metadata=None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


def _sym(
    name: str,
    file_path: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    line: int = 10,
    character: int = 4,
    container: str = "",
    signature: str = "def foo()",
) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        kind=kind,
        file_path=file_path,
        line=line,
        character=character,
        signature=signature,
        container=container,
    )


def _plan(*paths: str, new_name: str = "bar") -> SemanticRenamePlan:
    changes = tuple(
        SemanticFileChange(
            file_path=path,
            base_content="def foo(): pass\n",
            base_hash=content_hash("def foo(): pass\n"),
            final_content=f"def {new_name}(): pass\n",
        )
        for path in paths
    )
    origin = (paths[0] if paths else "/ws/a.py", 1, 4)
    return SemanticRenamePlan(new_name=new_name, origin=origin, changes=changes)


def _success_op(paths: list[str]) -> OperationResult:
    return OperationResult(
        success=True,
        status="committed",
        files=tuple(
            EditResult(success=True, file_path=path, message="Wrote file")
            for path in paths
        ),
        conflict_file=None,
        conflict_reason="",
        timings={},
    )


def _failed_op(paths: list[str], *, status: str, reason: str) -> OperationResult:
    return OperationResult(
        success=False,
        status=status,  # type: ignore[arg-type]
        files=tuple(
            EditResult(success=False, file_path=path, message=reason)
            for path in paths
        ),
        conflict_file=paths[0] if paths else None,
        conflict_reason=reason,
        timings={},
    )


def _make_svc(
    *,
    matches: list[SymbolInfo] | None = None,
    plan: SemanticRenamePlan | None = None,
    rename_result: OperationResult | None = None,
) -> SimpleNamespace:
    symbol_index = SimpleNamespace(
        ensure_built=MagicMock(),
        find=MagicMock(return_value=list(matches or [])),
    )
    svc = SimpleNamespace(
        symbol_index=symbol_index,
        rename_symbol_plan=MagicMock(return_value=plan or _plan("/ws/a.py")),
        commit_rename_plan=MagicMock(
            return_value=rename_result or _success_op(["/ws/a.py"])
        ),
    )
    return svc


def _run(args: dict, ctx: ToolExecutionContext):
    return asyncio.run(run_tool_safely(daytona_rename_symbol, args, context=ctx))


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_missing_ci_service_returns_ci_required_error() -> None:
    ctx = _ctx({"ci_service": None})

    result = _run({"symbol": "foo", "new_name": "bar"}, ctx)

    assert result.is_error
    assert result.metadata.get("ci_required") is True


def test_invalid_identifier_is_rejected() -> None:
    svc = _make_svc(matches=[_sym("foo", "/ws/a.py")])
    ctx = _ctx({"ci_service": svc})

    result = _run({"symbol": "foo", "new_name": "1bad"}, ctx)

    assert result.is_error
    assert "Invalid identifier" in result.output


def test_python_keyword_is_rejected() -> None:
    svc = _make_svc(matches=[_sym("foo", "/ws/a.py")])
    ctx = _ctx({"ci_service": svc})

    result = _run({"symbol": "foo", "new_name": "class"}, ctx)

    assert result.is_error
    assert "Python keyword" in result.output


# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------


def test_no_match_returns_helpful_message() -> None:
    svc = _make_svc(matches=[])
    ctx = _ctx({"ci_service": svc})

    result = _run({"symbol": "ghost", "new_name": "phantom"}, ctx)

    assert result.is_error
    payload = json.loads(result.output)
    assert payload["status"] == "no_match"
    assert "ci_query_symbol" in payload["message"]
    svc.commit_rename_plan.assert_not_called()


def test_ambiguous_matches_return_candidates_without_renaming() -> None:
    svc = _make_svc(
        matches=[_sym("foo", "/ws/a.py"), _sym("foo", "/ws/b.py")],
    )
    ctx = _ctx({"ci_service": svc})

    result = _run({"symbol": "foo", "new_name": "bar"}, ctx)

    assert result.is_error
    payload = json.loads(result.output)
    assert payload["status"] == "ambiguous"
    assert len(payload["candidates"]) == 2
    svc.commit_rename_plan.assert_not_called()


def test_dotted_name_filters_by_container() -> None:
    svc = _make_svc(
        matches=[
            _sym("bar", "/ws/a.py", container="Foo"),
            _sym("bar", "/ws/b.py", container="Other"),
        ],
    )
    ctx = _ctx({"ci_service": svc})

    result = _run({"symbol": "Foo.bar", "new_name": "baz"}, ctx)

    assert not result.is_error
    payload = json.loads(result.output)
    assert payload["status"] == "renamed"


# ---------------------------------------------------------------------------
# OCC path
# ---------------------------------------------------------------------------


def test_single_match_delegates_to_svc_commit_rename_plan() -> None:
    svc = _make_svc(
        matches=[_sym("foo", "/ws/a.py")],
        plan=_plan("/ws/a.py", "/ws/b.py"),
        rename_result=_success_op(["/ws/a.py", "/ws/b.py"]),
    )
    ctx = _ctx({"ci_service": svc, "agent_run_id": "run-1"})

    result = _run({"symbol": "foo", "new_name": "bar"}, ctx)

    assert not result.is_error
    payload = json.loads(result.output)
    assert payload["status"] == "renamed"
    assert {f["file_path"] for f in payload["files"]} == {"/ws/a.py", "/ws/b.py"}
    svc.commit_rename_plan.assert_called_once()
    assert svc.commit_rename_plan.call_args.kwargs["agent_id"] == "run-1"


def test_empty_plan_returns_no_changes_without_calling_rename() -> None:
    svc = _make_svc(
        matches=[_sym("foo", "/ws/a.py")],
        plan=SemanticRenamePlan(new_name="bar", origin=("/ws/a.py", 1, 0), changes=()),
    )
    ctx = _ctx({"ci_service": svc})

    result = _run({"symbol": "foo", "new_name": "bar"}, ctx)

    payload = json.loads(result.output)
    assert payload["status"] == "no_changes"
    svc.commit_rename_plan.assert_not_called()


def test_aborted_version_is_surfaced() -> None:
    svc = _make_svc(
        matches=[_sym("foo", "/ws/a.py")],
        plan=_plan("/ws/a.py"),
        rename_result=_failed_op(
            ["/ws/a.py"], status="aborted_version", reason="file changed",
        ),
    )
    ctx = _ctx({"ci_service": svc})

    result = _run({"symbol": "foo", "new_name": "bar"}, ctx)

    assert result.is_error
    payload = json.loads(result.output)
    assert payload["status"] == "aborted_version"
    assert payload["conflict_reason"] == "file changed"


# ---------------------------------------------------------------------------
# Write-scope policy
# ---------------------------------------------------------------------------


def test_write_scope_hard_error_blocks_rename_before_commit(monkeypatch) -> None:
    """Test-file block runs in the pre-hook and surfaces write-scope policy."""
    svc = _make_svc(
        matches=[_sym("foo", "/ws/a.py")],
        plan=_plan("/ws/a.py", "/ws/b.py"),
    )
    ctx = _ctx({"ci_service": svc})

    def _fake_error(_ctx, path, tool_name):
        return None if path == "/ws/a.py" else "blocked by policy"

    monkeypatch.setattr(
        "tools.daytona_toolkit.hooks.prehook.rename_scope_policy._team_repo_write_error",
        _fake_error,
    )
    monkeypatch.setattr(
        "tools.daytona_toolkit.hooks.prehook.rename_scope_policy._team_repo_scope_deny_errors",
        lambda _ctx, _paths, tool_name: [],
    )

    result = _run({"symbol": "foo", "new_name": "bar"}, ctx)

    assert result.is_error
    assert "Rename blocked by write-scope policy" in result.output
    svc.commit_rename_plan.assert_not_called()


def test_rename_outside_scope_denies_with_offender_only_listing(monkeypatch) -> None:
    """Outside-scope rename paths are denied; message lists only offenders."""
    svc = _make_svc(
        matches=[_sym("foo", "/ws/a.py")],
        plan=_plan("/ws/allowed/a.py", "/ws/other/b.py", "/ws/allowed/c.py"),
    )
    ctx = _ctx({
        "ci_service": svc,
        "agent_name": "developer",
        "daytona_cwd": "/ws",
        "write_scope": ["allowed/"],
    })

    result = _run({"symbol": "foo", "new_name": "bar"}, ctx)

    assert result.is_error
    assert "daytona_rename_symbol blocked by write-scope policy" in result.output
    assert "/ws/other/b.py" in result.output
    assert "/ws/allowed/a.py" not in result.output
    assert "/ws/allowed/c.py" not in result.output
    svc.commit_rename_plan.assert_not_called()
