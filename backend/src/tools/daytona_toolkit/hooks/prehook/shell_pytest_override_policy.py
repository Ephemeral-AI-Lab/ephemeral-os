"""Block daytona_shell pytest commands that override config, plugins, or warnings."""

from __future__ import annotations

import re

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks.prehook._shell_common import shell_commands

PYTEST_OVERRIDE_POLICY_MESSAGE = (
    "daytona_shell policy error: pytest verification commands must not override "
    "config, plugins, conftest discovery, ini settings, or warning filters, and "
    "must not pipe stdout through head/tail/grep/awk/sed/wc/sort/uniq or redirect "
    "stdout to a file. Drop `-c <path>`, `--noconftest`, `--confcutdir <path>`, "
    "`--rootdir <path>`, `-o <name=value>`, `--override-ini <name=value>`, "
    "`-p no:<plugin>`, `-p=no:<plugin>`, `-W <filter>`/`--pythonwarnings`, and "
    "any `pytest ... | <filter>` or `pytest ... > file` wrappers. Run the raw "
    "exact command; overrides and output filters are RCA-only and cannot make "
    "success."
)

_PYTEST_INVOCATION = re.compile(r"(?:^|[\s/])(?:py\.test|pytest)\b|-m\s+pytest\b")
_FORBIDDEN_FLAGS = (
    re.compile(r"(?:^|\s)-c(?:\s+|=)\S+"),
    re.compile(r"(?:^|\s)--noconftest\b"),
    re.compile(r"(?:^|\s)--confcutdir(?:\s+|=)\S+"),
    re.compile(r"(?:^|\s)--rootdir(?:\s+|=)\S+"),
    re.compile(r"(?:^|\s)-o(?:\s+|=)\S+"),
    re.compile(r"(?:^|\s)--override-ini(?:\s+|=)\S+"),
    re.compile(r"(?:^|\s)-p(?:\s+|=)['\"]?no:\S+"),
    re.compile(r"(?:^|\s)-W(?:\s+|=)\S+"),
    re.compile(r"(?:^|\s)--pythonwarnings(?:\s+|=)\S+"),
)
_STDOUT_FILTER_PIPE = re.compile(
    r"\|\s*(?:head|tail|grep|egrep|fgrep|awk|sed|wc|sort|uniq|tee)\b"
)
_STDOUT_TO_FILE = re.compile(r"(?<![<>&|])(?:^|\s)>{1,2}\s*(?!&)\S+")


def _has_pytest_override(command: str) -> bool:
    if not _PYTEST_INVOCATION.search(command):
        return False
    if any(pattern.search(command) for pattern in _FORBIDDEN_FLAGS):
        return True
    if _STDOUT_FILTER_PIPE.search(command):
        return True
    if _STDOUT_TO_FILE.search(command):
        return True
    return False


def shell_pytest_override_policy_error(args: BaseModel) -> str | None:
    for command in shell_commands(args):
        if _has_pytest_override(command):
            return PYTEST_OVERRIDE_POLICY_MESSAGE
    return None


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    del context
    err = shell_pytest_override_policy_error(args)
    if err is not None:
        return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_shell",
        "pre",
        29,
        hook,
        name="daytona_shell:pytest_override_policy",
    )
