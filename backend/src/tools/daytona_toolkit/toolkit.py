"""DaytonaToolkit — groups all Daytona sandbox tools into a single toolkit."""

from __future__ import annotations

import logging
from typing import Any

from tools.base import BaseToolkit

from tools.daytona_toolkit.tools import (
    DaytonaBashTool,
    DaytonaFileReadTool,
    DaytonaFileWriteTool,
    DaytonaGlobTool,
    DaytonaGrepTool,
    DaytonaListFilesTool,
)
from tools.daytona_toolkit.edit_tool import DaytonaEditTool
from tools.daytona_toolkit.lsp_tools import (
    DaytonaLspDefinitionTool,
    DaytonaLspDiagnosticsTool,
    DaytonaLspHoverTool,
    DaytonaLspReferencesTool,
)
from tools.daytona_toolkit.codeact_tool import DaytonaCodeActTool

logger = logging.getLogger(__name__)


class DaytonaToolkit(BaseToolkit):
    """Daytona sandbox toolkit — file I/O, editing, LSP, shell, and CodeAct.

    Requires a pre-created sandbox_id. The sandbox is fetched lazily
    on first tool invocation and injected into ToolExecutionContext.metadata
    via the ``prepare_context`` helper.

    CI integration is optional — tools degrade gracefully if no
    CodeIntelligenceService is configured in the context.

    Usage::

        toolkit = DaytonaToolkit(sandbox_id="sb-abc123")
        registry.register_toolkit(toolkit)

        # Before executing tools, inject sandbox into context:
        toolkit.prepare_context(context)
    """

    def __init__(self, sandbox_id: str | None = None) -> None:
        super().__init__(
            name="sandbox_operations",
            description=(
                "Remote sandbox operations: shell, files, search, "
                "OCC-coordinated editing, LSP queries, and CodeAct execution"
            ),
            tools=[
                # Read tools first (preferred execution order)
                DaytonaListFilesTool(),
                DaytonaGrepTool(),
                DaytonaGlobTool(),
                DaytonaFileReadTool(),
                # LSP queries
                DaytonaLspHoverTool(),
                DaytonaLspDefinitionTool(),
                DaytonaLspReferencesTool(),
                DaytonaLspDiagnosticsTool(),
                # Write tools
                DaytonaFileWriteTool(),
                DaytonaEditTool(),
                DaytonaCodeActTool(),
                # Execution
                DaytonaBashTool(),
            ],
        )
        self.sandbox_id = sandbox_id
        self._sandbox: Any | None = None

    def _get_sandbox(self) -> Any:
        """Lazily fetch the sandbox on first access."""
        if self._sandbox is not None:
            return self._sandbox
        if not self.sandbox_id:
            raise RuntimeError(
                "No sandbox_id configured. Pass sandbox_id to DaytonaToolkit() "
                "or set it via toolkit.sandbox_id = '...'."
            )
        from tools.daytona_toolkit.client import get_sandbox

        self._sandbox = get_sandbox(self.sandbox_id)
        logger.info("Daytona sandbox fetched: %s", self.sandbox_id)
        return self._sandbox

    def prepare_context(self, context: Any) -> None:
        """Inject sandbox and optional CI service into a ToolExecutionContext.

        Call this before executing any Daytona tool so it can access
        the sandbox via ``context.metadata['daytona_sandbox']`` and
        optionally the CI service via ``context.metadata['ci_service']``.
        """
        sandbox = self._get_sandbox()
        context.metadata["daytona_sandbox"] = sandbox
        # Set working directory to project dir if available
        project_dir = getattr(sandbox, "project_dir", None)
        if project_dir:
            context.metadata["daytona_cwd"] = project_dir

        # Inject CI service if available
        if self.sandbox_id and "ci_service" not in context.metadata:
            try:
                from code_intelligence.routing.service import get_code_intelligence

                workspace_root = project_dir or "/workspace"
                svc = get_code_intelligence(
                    sandbox_id=self.sandbox_id,
                    workspace_root=workspace_root,
                    sandbox=sandbox,
                )
                context.metadata["ci_service"] = svc
            except Exception:
                logger.debug("CI service not available for sandbox %s", self.sandbox_id)
