"""Block CodeAct file-edit side channels in coordinated team lanes."""

from __future__ import annotations

import re

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks._common import is_coordinated_team_agent
from tools.daytona_toolkit.hooks.prehook._codeact_common import python_code, shell_command

FILE_EDIT_POLICY_MESSAGE = (
    "BLOCKED: daytona_codeact is for runtime commands, tests, and inspection in "
    "coordinated team lanes. Repo writes, explicit deletes, and moves must use daytona_edit_file, "
    "daytona_write_file, daytona_rename_symbol, daytona_delete_file, or "
    "daytona_move_file so write-scope, OCC, and invalid-edit guardrails run "
    "before mutation. Pure file removals may run through CodeAct because the "
    "overlay audit path converts tracked whiteouts into OCC-gated deletes and "
    "rejects unsupported removal shapes. Use daytona_move_file for path moves. "
    "Do not retry cleanup with mv, shutil.move, os.rename, git rm, or git mv "
    "inside CodeAct."
)
_SHELL_FILE_EDIT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:g?sed|sed)\b(?:(?![;&|]).)*\s-[A-Za-z]*i(?:\b|[=.])",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "in-place sed",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)perl\b(?:(?![;&|]).)*\s-\S*i\S*",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "in-place perl",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)tee\b(?:\s+-[A-Za-z]+)*\s+(?!/dev/null(?:\s|$))\S+",
            flags=re.IGNORECASE,
        ),
        "tee file write",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:touch|truncate|cp|mv|install)\b"
            r"|(?:^|[;&|]\s*)git\s+(?:rm|mv)\b",
            flags=re.IGNORECASE,
        ),
        "filesystem mutation command",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)python(?:3(?:\.\d+)?)?\b.*"
            r"(?:write_text|write_bytes|"
            r"\bopen\s*\([^)]*,\s*['\"][^'\"]*[wax+]|"
            r"\bshutil\.(?:copy|copyfile|copytree|move)|"
            r"\bos\.(?:rename|replace)|"
            r"\bPath\s*\([^)]*\)\.(?:touch|rename|replace|mkdir))",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "inline Python file mutation",
    ),
)
_SHELL_OUTPUT_REDIRECTION_PATTERN = re.compile(
    r"(?<![<>&])(?:\b\d*)?(?:>>?|&>)\s*(?!&\d\b)(?!/dev/null(?:\s|$))\S+",
    flags=re.IGNORECASE,
)
_PYTHON_FILE_EDIT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![.\w])write\s*\(", flags=re.IGNORECASE), "CodeAct write() helper"),
    (re.compile(r"\bwrite_text\s*\(", flags=re.IGNORECASE), "Path.write_text"),
    (re.compile(r"\bwrite_bytes\s*\(", flags=re.IGNORECASE), "Path.write_bytes"),
    (
        re.compile(
            r"\bopen\s*\([^)]*,\s*['\"][^'\"]*[wax+]",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "write-mode open()",
    ),
    (
        re.compile(
            r"\b(?:os|Path\s*\([^)]*\))\.(?:rename|replace|touch|mkdir)\b",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "Python filesystem mutation",
    ),
    (
        re.compile(
            r"\bshutil\.(?:copy|copyfile|copytree|move)\b",
            flags=re.IGNORECASE,
        ),
        "shutil file mutation",
    ),
)


def _has_team_task_context(context: ToolExecutionContext) -> bool:
    return bool(
        context.metadata.get("task_center")
        or context.metadata.get("team_run_id")
        or context.metadata.get("work_item_id")
        or context.metadata.get("benchmark_test_ids")
        or context.metadata.get("benchmark_test_files")
    )


def should_disable_codeact_file_edits(context: ToolExecutionContext) -> bool:
    return is_coordinated_team_agent(context) and _has_team_task_context(context)


def _file_edit_policy_error(kind: str) -> str:
    return f"{FILE_EDIT_POLICY_MESSAGE} Detected {kind}."


def _mask_shell_quoted_text(command: str) -> str:
    """Mask shell-quoted text while keeping quote delimiters and rough token shape."""
    out: list[str] = []
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            out.append("x" if quote else char)
            escaped = False
            continue
        if char == "\\":
            out.append("x" if quote else char)
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
                out.append(char)
            else:
                out.append("x" if not char.isspace() else char)
            continue
        if char in {"'", '"'}:
            quote = char
        out.append(char)
    return "".join(out)


def shell_file_edit_policy_error(command: str) -> str | None:
    if _SHELL_OUTPUT_REDIRECTION_PATTERN.search(_mask_shell_quoted_text(command or "")):
        return _file_edit_policy_error("shell output redirection")
    for pattern, kind in _SHELL_FILE_EDIT_PATTERNS:
        if pattern.search(command or ""):
            return _file_edit_policy_error(kind)
    return None


def python_file_edit_policy_error(code: str) -> str | None:
    for pattern, kind in _PYTHON_FILE_EDIT_PATTERNS:
        if pattern.search(code or ""):
            return _file_edit_policy_error(kind)
    return None


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    if not should_disable_codeact_file_edits(context):
        return PreHookOutcome()
    command = shell_command(args)
    if command is not None:
        err = shell_file_edit_policy_error(command)
        if err is not None:
            return PreHookOutcome(has_error=True, error_message=err)
        return PreHookOutcome()
    code = python_code(args)
    if code is not None:
        err = python_file_edit_policy_error(code)
        if err is not None:
            return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "pre",
        30,
        hook,
        name="daytona_codeact:file_edit_policy",
    )
