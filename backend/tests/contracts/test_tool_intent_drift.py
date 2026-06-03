"""Contract test: tool intent labels stay in sync with public sandbox RPCs.

The Python daemon route table was deleted when the Rust sandbox became the only
daemon-side truth. Tool contracts now compare against public transport constants
and the tool registry, not ``sandbox.daemon`` implementation tables.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest

from sandbox._shared.models import Intent
from sandbox.api import transport
from tools._framework.core.base import BaseTool


_TOOL_MODULES = (
    "tools.sandbox.read_file.read_file",
    "tools.sandbox.write_file.write_file",
    "tools.sandbox.edit_file.edit_file",
    "tools.sandbox.multi_edit.multi_edit",
    "tools.sandbox.exec_command.exec_command",
    "tools.sandbox.write_stdin.write_stdin",
    "tools.sandbox.grep.grep",
    "tools.sandbox.glob.glob",
    "tools.ask_helper.ask_advisor.ask_advisor",
    "tools.subagent.run_subagent.run_subagent",
    "tools.isolated_workspace.enter_isolated_workspace.definition",
    "tools.isolated_workspace.exit_isolated_workspace.definition",
    "tools.submission.reducer.submit_reducer_outcome.submit_reducer_outcome",
    "tools.submission.advisor.submit_advisor_feedback.submit_advisor_feedback",
    "tools.submission.planner.submit_planner_outcome.submit_planner_outcome",
    "tools.submission.explorer.submit_exploration_result.submit_exploration_result",
    "tools.submission.root.submit_root_outcome.submit_root_outcome",
    "tools.submission.generator.submit_generator_outcome.submit_generator_outcome",
    "tools.workflow.delegate_workflow",
    "tools.workflow.check_workflow_status",
    "tools.workflow.cancel_workflow",
    "plugins.catalog.lsp.tools.hover",
    "plugins.catalog.lsp.tools.find_definitions",
    "plugins.catalog.lsp.tools.find_references",
    "plugins.catalog.lsp.tools.diagnostics",
    "plugins.catalog.lsp.tools.query_symbols",
    "plugins.catalog.lsp.tools.code_actions",
    "plugins.catalog.lsp.tools.apply_workspace_edit",
    "plugins.catalog.lsp.tools.apply_code_action",
    "plugins.catalog.lsp.tools.rename",
    "plugins.catalog.lsp.tools.format",
)

SANDBOX_TOOL_INTENTS = {
    "read_file": Intent.READ_ONLY,
    "glob": Intent.READ_ONLY,
    "grep": Intent.READ_ONLY,
    "write_file": Intent.WRITE_ALLOWED,
    "edit_file": Intent.WRITE_ALLOWED,
    "multi_edit": Intent.WRITE_ALLOWED,
    "exec_command": Intent.WRITE_ALLOWED,
    "write_stdin": Intent.WRITE_ALLOWED,
    "enter_isolated_workspace": Intent.LIFECYCLE,
    "exit_isolated_workspace": Intent.LIFECYCLE,
}

SANDBOX_TOOL_TRANSPORT_OPS = {
    "read_file": transport.DAEMON_OP_READ_FILE,
    "write_file": transport.DAEMON_OP_WRITE_FILE,
    "edit_file": transport.DAEMON_OP_EDIT_FILE,
    "multi_edit": transport.DAEMON_OP_EDIT_FILE,
    "exec_command": transport.DAEMON_OP_EXEC_COMMAND,
    "write_stdin": transport.DAEMON_OP_COMMAND_WRITE_STDIN,
    "glob": transport.DAEMON_OP_GLOB,
    "grep": transport.DAEMON_OP_GREP,
}


def _iter_decorated_tools() -> Iterator[BaseTool]:
    for module_name in _TOOL_MODULES:
        module = importlib.import_module(module_name)
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if isinstance(obj, BaseTool):
                yield obj


def test_every_decorated_tool_has_intent_attribute() -> None:
    """@tool decorations MUST set tool.intent to an Intent member."""
    tools = list(_iter_decorated_tools())
    assert tools, "no @tool callsites discovered — _TOOL_MODULES is stale"
    missing: list[str] = []
    for tool in tools:
        intent = getattr(tool, "intent", None)
        if not isinstance(intent, Intent):
            missing.append(tool.name)
    assert not missing, f"@tool callsites missing intent=: {missing}"


@pytest.mark.parametrize("tool_name,expected", sorted(SANDBOX_TOOL_INTENTS.items()))
def test_sandbox_tool_intent_matches_public_rpc_contract(
    tool_name: str,
    expected: Intent,
) -> None:
    tools = {tool.name: tool for tool in _iter_decorated_tools()}
    assert tool_name in tools
    assert tools[tool_name].intent == expected


def test_sandbox_tool_transport_contract_uses_exec_command_and_write_stdin() -> None:
    tools = {tool.name for tool in _iter_decorated_tools()}
    assert "exec_command" in tools
    assert "write_stdin" in tools
    assert "shell" not in tools
    assert SANDBOX_TOOL_TRANSPORT_OPS["exec_command"] == "api.v1.exec_command"
    assert SANDBOX_TOOL_TRANSPORT_OPS["write_stdin"] == "api.v1.write_stdin"
    assert "api.v1.shell" not in SANDBOX_TOOL_TRANSPORT_OPS.values()
    assert "api.v1.command.write_stdin" not in SANDBOX_TOOL_TRANSPORT_OPS.values()
