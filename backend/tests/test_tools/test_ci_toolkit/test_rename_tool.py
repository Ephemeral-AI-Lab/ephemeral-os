"""Tests for the ci_rename_symbol tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from code_intelligence.types import EditResult, PreparedWrite
from tools.ci_toolkit.rename_tool import ci_rename_symbol
from tools.core.base import ToolExecutionContext


def _ctx(metadata=None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


def _prepared(path: str, content: str) -> PreparedWrite:
    return PreparedWrite(
        file_path=path,
        token_id=f"tok-{path}",
        current_content=content,
        current_hash="hash",
        existed=True,
    )


def _ok_result(path: str) -> EditResult:
    return EditResult(success=True, file_path=path)


def _run(tool_input):
    return asyncio.run(
        ci_rename_symbol.execute(ci_rename_symbol.input_model(**tool_input), _svc_ctx)
    )


# ---------- fixtures-as-globals (kept simple for parity with sibling tests) ----


def _make_svc(*, changes: dict[str, str], results: dict[str, EditResult] | None = None):
    svc = MagicMock()
    svc.rename_symbol.return_value = changes
    svc.prepare_write.side_effect = lambda path, **_: _prepared(
        path, f"# old {path}\n"
    )
    svc.refresh_prepared_write.side_effect = lambda prepared: prepared
    svc.commit_prepared_write.side_effect = lambda prepared, new, **_: (
        (results or {}).get(prepared.file_path) or _ok_result(prepared.file_path)
    )
    svc.abort_prepared_write.return_value = None
    return svc


def test_no_service_returns_error():
    ctx = _ctx()
    result = asyncio.run(
        ci_rename_symbol.execute(
            ci_rename_symbol.input_model(
                file_path="/ws/a.py", line=1, new_name="bar"
            ),
            ctx,
        )
    )
    assert result.is_error
    assert "LSP rename not available" in result.output


def test_invalid_new_name_rejected():
    svc = _make_svc(changes={})
    ctx = _ctx({"ci_service": svc})
    result = asyncio.run(
        ci_rename_symbol.execute(
            ci_rename_symbol.input_model(
                file_path="/ws/a.py", line=1, new_name="1bad"
            ),
            ctx,
        )
    )
    assert result.is_error
    assert "Invalid identifier" in result.output


def test_no_changes_returns_status_no_changes():
    svc = _make_svc(changes={})
    ctx = _ctx({"ci_service": svc})
    result = asyncio.run(
        ci_rename_symbol.execute(
            ci_rename_symbol.input_model(
                file_path="/ws/a.py", line=1, new_name="bar"
            ),
            ctx,
        )
    )
    assert not result.is_error
    data = json.loads(result.output)
    assert data["status"] == "no_changes"
    assert data["files"] == []


def test_rename_commits_each_file():
    changes = {
        "/ws/a.py": "# a new\n",
        "/ws/b.py": "# b new\n",
    }
    svc = _make_svc(changes=changes)
    ctx = _ctx({"ci_service": svc})
    result = asyncio.run(
        ci_rename_symbol.execute(
            ci_rename_symbol.input_model(
                file_path="/ws/a.py", line=3, character=4, new_name="bar"
            ),
            ctx,
        )
    )
    assert not result.is_error, result.output
    data = json.loads(result.output)
    assert data["status"] == "renamed"
    assert {f["file_path"] for f in data["files"]} == set(changes)
    assert all(f["status"] == "renamed" for f in data["files"])
    assert svc.prepare_write.call_count == 2
    assert svc.commit_prepared_write.call_count == 2


def test_rename_dry_run_returns_diffs_without_commit():
    changes = {"/ws/a.py": "# new\n"}
    svc = _make_svc(changes=changes)
    ctx = _ctx({"ci_service": svc})
    result = asyncio.run(
        ci_rename_symbol.execute(
            ci_rename_symbol.input_model(
                file_path="/ws/a.py", line=1, new_name="bar", dry_run=True
            ),
            ctx,
        )
    )
    assert not result.is_error
    data = json.loads(result.output)
    assert data["status"] == "dry_run"
    assert len(data["files"]) == 1
    entry = data["files"][0]
    assert entry["status"] == "dry_run"
    assert "--- a//ws/a.py" in entry["diff"] or "a/ws/a.py" in entry["diff"]
    assert svc.commit_prepared_write.call_count == 0


def test_rename_reports_failures_without_aborting_siblings():
    """A failed commit does not abort remaining files.

    OCC guarantees that each successful sibling commit is atomic and safe to
    keep even when another file's commit fails — so we report per-file status
    and let the agent re-run to finish the remaining ones.
    """
    changes = {
        "/ws/a.py": "# a new\n",
        "/ws/b.py": "# b new\n",
        "/ws/c.py": "# c new\n",
    }
    results = {
        "/ws/b.py": EditResult(
            success=False, file_path="/ws/b.py", message="boom", conflict=True,
        ),
    }
    svc = _make_svc(changes=changes, results=results)
    ctx = _ctx({"ci_service": svc})
    result = asyncio.run(
        ci_rename_symbol.execute(
            ci_rename_symbol.input_model(
                file_path="/ws/a.py", line=1, new_name="bar"
            ),
            ctx,
        )
    )
    assert result.is_error
    data = json.loads(result.output)
    assert data["status"] == "failed"
    by_path = {f["file_path"]: f for f in data["files"]}
    assert by_path["/ws/a.py"]["status"] == "renamed"
    assert by_path["/ws/b.py"]["status"] == "failed"
    assert by_path["/ws/c.py"]["status"] == "renamed"
    assert "boom" in by_path["/ws/b.py"]["message"]
    # All three files were attempted because each has its own OCC lock.
    assert svc.commit_prepared_write.call_count == 3
