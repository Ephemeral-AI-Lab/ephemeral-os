"""DaytonaToolkit — groups all Daytona sandbox tools into a single toolkit."""

from __future__ import annotations

import logging
from typing import Any

from tools.core.base import BaseToolkit

from tools.daytona_toolkit.tools import (
    daytona_bash,
    daytona_glob,
    daytona_grep,
    daytona_list_files,
    daytona_read_file,
    daytona_write_file,
)
from tools.daytona_toolkit.edit_tool import daytona_edit_file
from tools.daytona_toolkit.lsp_tools import (
    daytona_lsp_definition,
    daytona_lsp_diagnostics,
    daytona_lsp_hover,
    daytona_lsp_references,
)
from tools.daytona_toolkit.codeact_tool import daytona_codeact

logger = logging.getLogger(__name__)

_TEAM_SAFE_AGENT_NAMES = frozenset({"developer", "validator"})


def _build_tools(*, include_codeact: bool) -> list[Any]:
    tools: list[Any] = [
        # Read tools first (preferred execution order)
        daytona_list_files,
        daytona_grep,
        daytona_glob,
        daytona_read_file,
        # LSP queries
        daytona_lsp_hover,
        daytona_lsp_definition,
        daytona_lsp_references,
        daytona_lsp_diagnostics,
        # Write tools
        daytona_write_file,
        daytona_edit_file,
    ]
    if include_codeact:
        tools.append(daytona_codeact)
    # Execution
    tools.append(daytona_bash)
    return tools


def _build_instructions(*, include_codeact: bool) -> str:
    codeact_line = (
        "- `daytona_codeact` — execute Python with atomic file I/O. "
        "Use for multi-step transformations that need read/write/shell in one operation.\n"
        if include_codeact
        else "- Coordinated team developer/validator lanes intentionally omit "
        "`daytona_codeact`; keep multi-step changes reviewable with direct "
        "`daytona_read_file` / `daytona_edit_file` / `daytona_write_file` calls.\n"
    )
    return (
        "Interact with a remote Daytona sandbox for file operations, "
        "code analysis, editing, and command execution. "
        "Read before you write — explore and understand context first.\n\n"
        "**Explore & Search**\n"
        "- `daytona_list_files` — list directory contents. Use to orient yourself.\n"
        "- `daytona_glob` — find files by pattern (e.g. `**/*.py`). Use to locate files.\n"
        "- `daytona_grep` — search file contents by regex. Use to find code patterns.\n"
        "- `daytona_read_file` — read a file. Use before editing to understand context.\n\n"
        "**Analyze**\n"
        "- `daytona_lsp_hover` — type info and docs for a symbol at a position.\n"
        "- `daytona_lsp_definition` — jump to where a symbol is defined.\n"
        "- `daytona_lsp_references` — find all usages of a symbol across files.\n"
        "- `daytona_lsp_diagnostics` — check a file for errors and warnings.\n\n"
        "**Edit**\n"
        "- `daytona_edit_file` — atomic file edits using `search_replace` or `line_range`, including small batched edits.\n"
        "- `daytona_write_file` — create or overwrite a file. Use for new files.\n"
        f"{codeact_line}\n"
        "**Execute**\n"
        "- `daytona_bash` — run a shell command. Use for tests, builds, installs, verification. In coordinated team runs, mutating commands must pass `declared_output_paths` so the runtime can reserve those paths before execution; undeclared mutations are rejected.\n"
        "- When an injected sandbox cwd/repo root is configured, shell and file tools already run relative to that root. Prefer relative repo paths and do not prepend guessed roots like `/workspace`, `/home/user`, or `/home/user/repos/...` unless you truly need a real subdirectory."
    )


class DaytonaToolkit(BaseToolkit):
    """Daytona sandbox toolkit — file I/O, editing, LSP, shell, and CodeAct.

    Requires a pre-created sandbox_id. The sandbox is fetched lazily
    on first tool invocation and injected into ToolExecutionContext.metadata
    via the ``prepare_context`` helper.

    Usage::

        toolkit = DaytonaToolkit(sandbox_id="sb-abc123")
        registry.register_toolkit(toolkit)

        # Before executing tools, inject sandbox into context:
        toolkit.prepare_context(context)
    """

    @classmethod
    def from_context(cls, ctx: Any) -> DaytonaToolkit:
        sandbox_id = ctx.metadata.get("sandbox_id", "") if ctx is not None else ""
        agent_name = str(ctx.metadata.get("agent_name", "") or "") if ctx is not None else ""
        include_codeact = agent_name not in _TEAM_SAFE_AGENT_NAMES
        return cls(sandbox_id=sandbox_id or None, include_codeact=include_codeact)

    def __init__(self, sandbox_id: str | None = None, *, include_codeact: bool = True) -> None:
        description = (
            "Remote sandbox operations: shell, files, search, "
            "editing, and LSP queries"
        )
        if include_codeact:
            description += ", and CodeAct execution"
        super().__init__(
            name="sandbox_operations",
            description=description,
            tools=_build_tools(include_codeact=include_codeact),
            instructions=_build_instructions(include_codeact=include_codeact),
        )
        self.sandbox_id = sandbox_id
        self._sandbox: Any | None = None
        self._sandbox_loop_id: int | None = None

    def _get_sandbox(self) -> Any:
        """Lazily fetch the sandbox on first access."""
        if self._sandbox is not None:
            return self._sandbox
        if not self.sandbox_id:
            raise RuntimeError(
                "No sandbox_id configured. Pass sandbox_id to DaytonaToolkit() "
                "or set it via toolkit.sandbox_id = '...'."
            )
        from sandbox import fetch_sandbox as get_sandbox

        self._sandbox = get_sandbox(self.sandbox_id)
        logger.info("Daytona sandbox fetched: %s", self.sandbox_id)
        return self._sandbox

    async def _get_sandbox_async(self) -> Any:
        """Lazily fetch the async sandbox on first access.

        Invalidates the cached sandbox when the event loop changes
        (e.g. pytest-asyncio creates a new loop per test).
        """
        import asyncio

        loop_id = id(asyncio.get_running_loop())
        if self._sandbox is not None and self._sandbox_loop_id == loop_id:
            return self._sandbox
        # Stale sandbox from a different (possibly closed) loop — discard it
        self._sandbox = None
        self._sandbox_loop_id = None
        if not self.sandbox_id:
            raise RuntimeError(
                "No sandbox_id configured. Pass sandbox_id to DaytonaToolkit() "
                "or set it via toolkit.sandbox_id = '...'."
            )
        from sandbox.async_client import get_async_sandbox

        self._sandbox = await get_async_sandbox(self.sandbox_id)
        self._sandbox_loop_id = loop_id
        logger.info("Async Daytona sandbox fetched: %s", self.sandbox_id)
        return self._sandbox

    @staticmethod
    def _resolve_cwd_sync(sandbox: Any) -> str | None:
        from sandbox.workspace import discover_workspace

        return discover_workspace(sandbox)

    @staticmethod
    async def _resolve_cwd_async(sandbox: Any) -> str | None:
        from sandbox.workspace import discover_workspace_async

        return await discover_workspace_async(sandbox)

    def _inject_ci(self, context: Any, sandbox: Any, workspace_root: str) -> None:
        from sandbox.workspace import inject_code_intelligence

        inject_code_intelligence(context, self.sandbox_id, sandbox, workspace_root)

    def prepare_context(self, context: Any) -> None:
        """Inject sandbox, cwd, and optional CI service into a ToolExecutionContext.

        Call this before executing any Daytona tool so it can access
        the sandbox via ``context.metadata['daytona_sandbox']`` and
        the resolved cwd via ``context.metadata['daytona_cwd']``.
        """
        sandbox = self._get_sandbox()
        context.metadata["daytona_sandbox"] = sandbox
        cwd = context.metadata.get("daytona_cwd") or self._resolve_cwd_sync(sandbox)
        if cwd:
            context.metadata["daytona_cwd"] = cwd
        ci_root = context.metadata.get("ci_workspace_root") or cwd or "/home/daytona"
        self._inject_ci(context, sandbox, ci_root)

    async def prepare_context_async(self, context: Any) -> None:
        """Inject async sandbox, cwd, and optional CI service into a ToolExecutionContext.

        Use this for streaming tool execution where cancellation support is needed.
        The async sandbox supports asyncio.CancelledError propagation.
        """
        sandbox = await self._get_sandbox_async()
        context.metadata["daytona_sandbox"] = sandbox
        cwd = context.metadata.get("daytona_cwd") or await self._resolve_cwd_async(sandbox)
        if cwd:
            context.metadata["daytona_cwd"] = cwd
        ci_root = context.metadata.get("ci_workspace_root") or cwd or "/home/daytona"
        self._inject_ci(context, sandbox, ci_root)
