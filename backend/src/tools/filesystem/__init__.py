"""Filesystem toolkit — file read, write, edit, and search tools."""

from ephemeralos.tools.base import BaseToolkit
from ephemeralos.tools.filesystem.file_edit_tool import FileEditTool
from ephemeralos.tools.filesystem.file_read_tool import FileReadTool
from ephemeralos.tools.filesystem.file_write_tool import FileWriteTool
from ephemeralos.tools.filesystem.glob_tool import GlobTool
from ephemeralos.tools.filesystem.grep_tool import GrepTool
from ephemeralos.tools.filesystem.notebook_edit_tool import NotebookEditTool


class FilesystemToolkit(BaseToolkit):
    """File system operations: read, write, edit, search."""

    def __init__(self) -> None:
        super().__init__(
            name="filesystem",
            description="File system operations: read, write, edit, search",
            tools=[
                FileReadTool(),
                FileWriteTool(),
                FileEditTool(),
                NotebookEditTool(),
                GlobTool(),
                GrepTool(),
            ],
        )


__all__ = [
    "FilesystemToolkit",
    "FileReadTool",
    "FileWriteTool",
    "FileEditTool",
    "NotebookEditTool",
    "GlobTool",
    "GrepTool",
]
