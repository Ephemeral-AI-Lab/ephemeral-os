"""Sandbox tool registry."""

from __future__ import annotations

from tools.core.base import BaseTool
from tools.core.context_requirements import SANDBOX_CONTEXT

from tools.sandbox_toolkit.edit_file import edit_file
from tools.sandbox_toolkit.read_file import read_file
from tools.sandbox_toolkit.shell import shell
from tools.sandbox_toolkit.write_file import write_file


def make_sandbox_tools(*, include_shell: bool = True) -> list[BaseTool]:
    """Return sandbox tools."""
    tools: list[BaseTool] = [
        read_file,
        write_file,
        edit_file,
    ]
    if include_shell:
        tools.append(shell)
    for tool in tools:
        tool.context_requirements = (SANDBOX_CONTEXT,)
    return tools


__all__ = ["make_sandbox_tools"]
