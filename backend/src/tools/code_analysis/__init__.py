"""Code analysis toolkit — LSP-powered code intelligence."""

from ephemeralos.tools.base import BaseToolkit
from ephemeralos.tools.code_analysis.lsp_tool import LspTool


class CodeAnalysisToolkit(BaseToolkit):
    """Code intelligence via Language Server Protocol."""

    def __init__(self) -> None:
        super().__init__(
            name="code_analysis",
            description="Code intelligence via Language Server Protocol",
            tools=[LspTool()],
        )


__all__ = ["CodeAnalysisToolkit", "LspTool"]
