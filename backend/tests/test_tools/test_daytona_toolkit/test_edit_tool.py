"""Tests for tools.daytona_toolkit.edit_tool.

The tool now delegates to ``svc.edit_file`` directly, so these tests
mock the service instead of framing a shell payload. Behaviour checked:
input normalization, scope-warning plumbing, OCC failure translation,
and legacy "search text not found" error-text preservation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from code_intelligence.types import EditSpec, EditResult, OperationResult
from tools.core.base import ToolExecutionContext, run_tool_safely
from tools.daytona_toolkit.edit_tool import daytona_edit_file


def _ctx(metadata=None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


def _success_op(file_path: str) -> OperationResult:
    return OperationResult(
        success=True,
        status="committed",
        files=(
            EditResult(
                success=True,
                file_path=file_path,
                message="Wrote file",
            ),
        ),
        conflict_file=None,
        conflict_reason="",
        timings={},
    )


def _failed_op(file_path: str, *, status: str, conflict_reason: str) -> OperationResult:
    return OperationResult(
        success=False,
        status=status,  # type: ignore[arg-type]
        files=(
            EditResult(
                success=False,
                file_path=file_path,
                message=conflict_reason,
            ),
        ),
        conflict_file=file_path,
        conflict_reason=conflict_reason,
        timings={},
    )


def _svc(result: OperationResult | None = None) -> SimpleNamespace:
    svc = SimpleNamespace()
    svc.edit_file = MagicMock(return_value=result or _success_op("/ws/file.py"))
    return svc


def _run(args: dict, ctx: ToolExecutionContext):
    return asyncio.run(run_tool_safely(daytona_edit_file, args, context=ctx))


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------


def test_missing_ci_service_returns_write_required_error() -> None:
    ctx = _ctx({"ci_service": None, "repo_root": "/ws"})

    result = _run({"file_path": "/ws/f.py", "old_text": "a", "new_text": "b"}, ctx)

    assert result.is_error
    assert result.metadata.get("ci_required") is True


def test_both_edits_and_old_text_is_rejected() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        {
            "file_path": "/ws/f.py",
            "old_text": "a",
            "new_text": "b",
            "edits": [{"strategy": "search_replace", "search": "x", "replace": "y"}],
        },
        ctx,
    )

    assert result.is_error
    assert "not both" in result.output
    svc.edit_file.assert_not_called()


def test_empty_edits_list_is_rejected() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run({"file_path": "/ws/f.py", "edits": []}, ctx)

    assert result.is_error
    assert "At least one edit" in result.output
    svc.edit_file.assert_not_called()


def test_single_old_new_text_edit_succeeds() -> None:
    svc = _svc(_success_op("/ws/file.py"))
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        {"file_path": "/ws/file.py", "old_text": "foo", "new_text": "bar"},
        ctx,
    )

    assert not result.is_error
    svc.edit_file.assert_called_once()
    specs = svc.edit_file.call_args.args[0]
    assert isinstance(specs, list) and len(specs) == 1
    assert isinstance(specs[0], EditSpec)
    assert specs[0].file_path == "/ws/file.py"
    payload = json.loads(result.output)
    assert payload["status"] == "edited"
    assert payload["applied_edits"] == 1


def test_search_replace_batch_variants_normalize_correctly() -> None:
    svc = _svc(_success_op("/ws/file.py"))
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        {
            "file_path": "/ws/file.py",
            "edits": [
                {"strategy": "search_replace", "search": "a", "replace": "b"},
                {"strategy": "search_replace", "old_text": "c", "new_text": "d"},
                {"strategy": "search_replace", "old_string": "e", "new_string": "f"},
            ],
        },
        ctx,
    )

    assert not result.is_error
    specs = svc.edit_file.call_args.args[0]
    assert len(specs[0].edits) == 3
    payload = json.loads(result.output)
    assert payload["applied_edits"] == 3


def test_unknown_edit_strategy_is_rejected() -> None:
    svc = _svc()
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        {
            "file_path": "/ws/f.py",
            "edits": [{"strategy": "rocket", "search": "a", "replace": "b"}],
        },
        ctx,
    )

    assert result.is_error
    assert "unknown strategy" in result.output
    svc.edit_file.assert_not_called()


# ---------------------------------------------------------------------------
# OCC failure translation
# ---------------------------------------------------------------------------


def test_aborted_version_is_surfaced_to_caller() -> None:
    svc = _svc(_failed_op("/ws/file.py", status="aborted_version", conflict_reason="drift"))
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        {"file_path": "/ws/file.py", "old_text": "a", "new_text": "b"},
        ctx,
    )

    assert result.is_error
    payload = json.loads(result.output)
    assert payload["status"] == "aborted_version"
    assert payload["conflict_reason"] == "drift"
    assert payload["conflict_file"] == "/ws/file.py"


def test_patch_failed_in_single_edit_mode_returns_legacy_error_text() -> None:
    """A single top-level old_text/new_text edit keeps the historical message."""
    svc = _svc(
        _failed_op("/ws/file.py", status="failed", conflict_reason="patch_failed"),
    )
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        {"file_path": "/ws/file.py", "old_text": "missing", "new_text": "x"},
        ctx,
    )

    assert result.is_error
    assert "Search text not found in /ws/file.py" in result.output


def test_patch_failed_in_batch_mode_uses_structured_payload() -> None:
    """Batch mode doesn't pretend to be single-edit; it surfaces the structured failure."""
    svc = _svc(
        _failed_op("/ws/file.py", status="failed", conflict_reason="patch_failed"),
    )
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run(
        {
            "file_path": "/ws/file.py",
            "edits": [{"strategy": "search_replace", "search": "m", "replace": "n"}],
        },
        ctx,
    )

    assert result.is_error
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert payload["conflict_reason"] == "patch_failed"


# ---------------------------------------------------------------------------
# Metadata wiring
# ---------------------------------------------------------------------------


def test_agent_id_is_passed_to_svc() -> None:
    svc = _svc(_success_op("/ws/file.py"))
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws", "agent_run_id": "run-42"})

    _run(
        {"file_path": "/ws/file.py", "old_text": "a", "new_text": "b"},
        ctx,
    )

    assert svc.edit_file.call_args.kwargs["agent_id"] == "run-42"


def test_description_flows_through_to_svc() -> None:
    svc = _svc(_success_op("/ws/file.py"))
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    _run(
        {
            "file_path": "/ws/file.py",
            "old_text": "a",
            "new_text": "b",
            "description": "tidy imports",
        },
        ctx,
    )

    assert svc.edit_file.call_args.kwargs["description"] == "tidy imports"


@pytest.mark.parametrize(
    "field,value",
    [("edits", [{"strategy": "", "search": "a", "replace": "b"}])],
)
def test_ambiguous_edit_shapes_are_tolerated(field: str, value) -> None:
    """Missing strategy with recognizable keys is treated as search_replace."""
    svc = _svc(_success_op("/ws/file.py"))
    ctx = _ctx({"ci_service": svc, "repo_root": "/ws"})

    result = _run({"file_path": "/ws/file.py", field: value}, ctx)

    assert not result.is_error
    svc.edit_file.assert_called_once()
