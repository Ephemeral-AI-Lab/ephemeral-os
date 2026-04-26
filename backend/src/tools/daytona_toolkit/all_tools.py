"""Sandbox tool exports."""

from __future__ import annotations

from tools.core.base import BaseTool

from tools.daytona_toolkit.delete_file_tool import delete_file
from tools.daytona_toolkit.edit_tool import edit_file
from tools.daytona_toolkit.glob_tool import glob
from tools.daytona_toolkit.grep_tool import grep
from tools.daytona_toolkit.move_file_tool import move_file
from tools.daytona_toolkit.read_file_tool import read_file
from tools.daytona_toolkit.shell_tool import shell
from tools.daytona_toolkit.write_file_tool import write_file


def make_daytona_tools(*, include_shell: bool = True) -> list[BaseTool]:
    """Return sandbox tools."""
    tools: list[BaseTool] = [
        grep,
        glob,
        read_file,
        write_file,
        edit_file,
        delete_file,
        move_file,
    ]
    if include_shell:
        tools.append(shell)
    return tools
