"""Diagnostics tool owned by code intelligence tools."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from sandbox.api.models import DiagnosticsRequest
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.core.sandbox_session import actor_from_context, code_intelligence_api_or_error


def _ci_cwd(context: ToolExecutionContextService) -> str | None:
    """Return the effective workspace root exposed to CI-backed tools."""
    return str(
        context.get("repo_root")
        or context.get("ci_workspace_root")
        or context.cwd
        or ""
    ).strip() or None


class CiDiagnosticsInput(BaseModel):
    file_path: str = Field(
        ...,
        description="Path to the file to diagnose.",
    )


class DiagnosticOutput(BaseModel):
    line: int = Field(..., description="One-based diagnostic line number.")
    character: int = Field(..., description="Zero-based diagnostic character offset.")
    severity: str | int = Field(..., description="Diagnostic severity.")
    message: str = Field(..., description="Diagnostic message.")
    source: str | None = Field(default=None, description="Diagnostic source.")


class CiDiagnosticsOutput(BaseModel):
    cwd: str = Field(..., description="Effective workspace root.")
    file_path: str = Field(..., description="Diagnosed file path.")
    diagnostics: list[DiagnosticOutput] = Field(
        default_factory=list,
        description="Diagnostics returned for the file.",
    )
    clean: bool = Field(..., description="True when no diagnostics were found.")


@tool(
    name="ci_diagnostics",
    description=(
        "Run syntax, import, name-resolution, and type checks on a single file and return "
        "structured diagnostics. Use after editing a file to verify it parses and types cleanly, "
        "or to triage a failing module. Prefer this over running pyflakes/mypy ad-hoc via "
        "`shell` — faster, cached, and structured."
    ),
    short_description="Check a file for diagnostics.",
    input_model=CiDiagnosticsInput,
    output_model=CiDiagnosticsOutput,
)
async def ci_diagnostics(
    file_path: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Get syntax and semantic diagnostics for a file."""
    api, err = code_intelligence_api_or_error(context)
    if err is not None:
        return ToolResult(output="LSP not available", is_error=True)

    try:
        result = await api.diagnostics(
            str(context.get("sandbox_id") or ""),
            DiagnosticsRequest(file_path=file_path, actor=actor_from_context(context)),
        )
    except Exception as exc:
        return ToolResult(
            output=f"LSP diagnostics unavailable: {exc}",
            is_error=True,
        )
    if not result.diagnostics:
        return ToolResult(
            output=json.dumps(
                {
                    "cwd": _ci_cwd(context) or "",
                    "file_path": file_path,
                    "diagnostics": [],
                    "clean": True,
                }
            )
        )

    diags = []
    for diag in result.diagnostics:
        diags.append(
            {
                "line": diag.line,
                "character": diag.character,
                "severity": diag.severity,
                "message": diag.message,
                "source": diag.source,
            }
        )

    return ToolResult(
        output=json.dumps(
            {
                "cwd": _ci_cwd(context) or "",
                "file_path": file_path,
                "diagnostics": diags,
                "clean": False,
            }
        )
    )
