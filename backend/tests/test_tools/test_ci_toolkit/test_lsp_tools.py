"""Tests for hover and diagnostics tools in tools.ci_toolkit.lsp_tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from tools.core.base import ToolExecutionContext
from tools.ci_toolkit.lsp_tools import ci_diagnostics, ci_hover


def _ctx(metadata=None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


def _ctx_with_svc(svc) -> ToolExecutionContext:
    return _ctx({"ci_service": svc})


def test_hover_no_service_returns_error():
    ctx = _ctx()
    result = asyncio.run(
        ci_hover.execute(ci_hover.input_model(file_path="/f.py", line=1), ctx)
    )
    assert result.is_error
    assert "LSP not available" in result.output


def test_hover_no_result():
    svc = MagicMock()
    svc.hover.return_value = None
    ctx = _ctx_with_svc(svc)
    result = asyncio.run(
        ci_hover.execute(ci_hover.input_model(file_path="/f.py", line=5, character=3), ctx)
    )
    assert not result.is_error
    assert "No hover information" in result.output


def test_hover_success():
    hover_result = MagicMock(content="int foo()", language="python")
    svc = MagicMock()
    svc.hover.return_value = hover_result
    ctx = _ctx({"ci_service": svc, "daytona_cwd": "/ws"})
    result = asyncio.run(
        ci_hover.execute(ci_hover.input_model(file_path="/f.py", line=10, character=5), ctx)
    )
    assert not result.is_error
    data = json.loads(result.output)
    assert data["content"] == "int foo()"
    assert data["language"] == "python"
    assert data["cwd"] == "/ws"
    svc.hover.assert_called_once_with("/f.py", 10, 5)


def test_diagnostics_no_service_returns_error():
    ctx = _ctx()
    result = asyncio.run(
        ci_diagnostics.execute(ci_diagnostics.input_model(file_path="/f.py"), ctx)
    )
    assert result.is_error
    assert "LSP not available" in result.output


def test_diagnostics_clean():
    svc = MagicMock()
    svc.diagnostics.return_value = []
    ctx = _ctx({"ci_service": svc, "daytona_cwd": "/ws"})
    result = asyncio.run(
        ci_diagnostics.execute(ci_diagnostics.input_model(file_path="/f.py"), ctx)
    )
    assert not result.is_error
    data = json.loads(result.output)
    assert data["clean"] is True
    assert data["diagnostics"] == []


def test_diagnostics_with_errors():
    diag = MagicMock()
    diag.line = 5
    diag.character = 3
    diag.severity = MagicMock(value="error")
    diag.message = "undefined name 'x'"
    diag.source = "pyright"
    svc = MagicMock()
    svc.diagnostics.return_value = [diag]
    ctx = _ctx({"ci_service": svc, "daytona_cwd": "/ws"})
    result = asyncio.run(
        ci_diagnostics.execute(ci_diagnostics.input_model(file_path="/f.py"), ctx)
    )
    assert not result.is_error
    data = json.loads(result.output)
    assert data["clean"] is False
    assert len(data["diagnostics"]) == 1
    diagnostic = data["diagnostics"][0]
    assert diagnostic["line"] == 5
    assert diagnostic["severity"] == "error"
    assert diagnostic["message"] == "undefined name 'x'"
    assert diagnostic["source"] == "pyright"


def test_diagnostics_severity_without_value_attr():
    diag = MagicMock(spec=["line", "character", "severity", "message", "source"])
    diag.line = 1
    diag.character = 0
    diag.severity = "warning"
    diag.message = "unused import"
    diag.source = "flake8"
    svc = MagicMock()
    svc.diagnostics.return_value = [diag]
    ctx = _ctx_with_svc(svc)
    result = asyncio.run(
        ci_diagnostics.execute(ci_diagnostics.input_model(file_path="/f.py"), ctx)
    )
    data = json.loads(result.output)
    assert data["diagnostics"][0]["severity"] == "warning"
