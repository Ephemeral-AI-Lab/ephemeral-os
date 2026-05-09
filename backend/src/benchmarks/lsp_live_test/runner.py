"""Async runner that executes one ``LspScenario`` against a real sandbox."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import sandbox.api as sandbox_api
from benchmarks.lsp_live_test.scenarios import (
    LspScenario,
    LspToolCall,
    ScenarioFailure,
)
from sandbox.api import (
    EditFileRequest,
    SandboxCaller,
    SearchReplaceEdit,
    WriteFileRequest,
)
from tools.core.context import ToolExecutionContextService
from tools.core.results import ToolResult
from tools.factory import ToolFactoryContext, create_tool

__all__ = [
    "LspScenarioReport",
    "build_lsp_tool_context",
    "run_lsp_scenario",
]


logger = logging.getLogger(__name__)


@dataclass
class LspScenarioReport:
    name: str
    passed: bool
    duration_s: float
    tool_durations_s: list[tuple[str, float]] = field(default_factory=list)
    failure: str | None = None


def build_lsp_tool_context(
    sandbox_id: str,
    *,
    repo_root: str,
) -> ToolExecutionContextService:
    ctx = ToolExecutionContextService(cwd=Path("/tmp"))
    ctx["sandbox_id"] = sandbox_id
    ctx["repo_root"] = repo_root
    ctx["agent_run_id"] = "lsp-live-test"
    ctx["agent_name"] = "lsp-live-test"
    return ctx


async def run_lsp_scenario(
    scenario: LspScenario,
    *,
    sandbox_id: str,
    repo_root: str,
) -> LspScenarioReport:
    start = time.monotonic()
    ctx = build_lsp_tool_context(sandbox_id, repo_root=repo_root)
    caller = SandboxCaller(
        agent_id="lsp-live-test",
        run_id="lsp-live-test",
        agent_run_id="lsp-live-test",
        task_id="lsp-live-test",
    )
    tool_durations: list[tuple[str, float]] = []
    try:
        await _seed_files(scenario, sandbox_id, repo_root, caller)
        for index, call in enumerate(scenario.tool_calls):
            await _maybe_apply_edits(
                scenario, before_index=index, sandbox_id=sandbox_id,
                repo_root=repo_root, caller=caller,
            )
            call_start = time.monotonic()
            result = await _execute_tool_call(call, ctx)
            tool_durations.append((call.tool_name, time.monotonic() - call_start))
            for assertion in call.assertions:
                assertion(result)
    except ScenarioFailure as exc:
        return LspScenarioReport(
            name=scenario.name,
            passed=False,
            duration_s=time.monotonic() - start,
            tool_durations_s=tool_durations,
            failure=str(exc),
        )
    except Exception as exc:  # surface any infra failure with context
        return LspScenarioReport(
            name=scenario.name,
            passed=False,
            duration_s=time.monotonic() - start,
            tool_durations_s=tool_durations,
            failure=f"unexpected error: {type(exc).__name__}: {exc}",
        )
    return LspScenarioReport(
        name=scenario.name,
        passed=True,
        duration_s=time.monotonic() - start,
        tool_durations_s=tool_durations,
    )


async def _seed_files(
    scenario: LspScenario,
    sandbox_id: str,
    repo_root: str,
    caller: SandboxCaller,
) -> None:
    for relative_path, contents in scenario.setup_files.items():
        target = _resolve(repo_root, relative_path)
        result = await sandbox_api.write_file(
            sandbox_id,
            WriteFileRequest(path=target, content=contents, caller=caller),
        )
        if not result.success:
            raise ScenarioFailure(f"setup write failed for {relative_path}: {result}")


async def _maybe_apply_edits(
    scenario: LspScenario,
    *,
    before_index: int,
    sandbox_id: str,
    repo_root: str,
    caller: SandboxCaller,
) -> None:
    """Apply any scenario edits scheduled before the *before_index*-th tool call.

    Uses edit_file when the scenario provides a search/replace pair, otherwise
    write_file as a full overwrite. The layer-stack publishes a new manifest
    version; the LSP session refreshes its stable projection root before the
    next tool call.
    """
    for index, edit in scenario.edits:
        if index != before_index:
            continue
        target = _resolve(repo_root, edit.file_path)
        if edit.old_text is not None:
            result = await sandbox_api.edit_file(
                sandbox_id,
                EditFileRequest(
                    path=target,
                    edits=(
                        SearchReplaceEdit(
                            old_text=edit.old_text,
                            new_text=edit.new_contents,
                        ),
                    ),
                    caller=caller,
                ),
            )
            if not result.success or result.applied_edits != 1:
                raise ScenarioFailure(f"edit failed for {edit.file_path}: {result}")
            continue
        result = await sandbox_api.write_file(
            sandbox_id,
            WriteFileRequest(path=target, content=edit.new_contents, caller=caller),
        )
        if not result.success:
            raise ScenarioFailure(f"write edit failed for {edit.file_path}: {result}")


async def _execute_tool_call(
    call: LspToolCall,
    ctx: ToolExecutionContextService,
) -> ToolResult:
    tool = create_tool(call.tool_name, ToolFactoryContext())
    arguments = tool.input_model(**call.args)
    return await tool.execute(arguments, ctx)


def _resolve(repo_root: str, relative_path: str) -> str:
    if relative_path.startswith("/"):
        return relative_path
    return f"{repo_root.rstrip('/')}/{relative_path}"
