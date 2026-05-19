"""Description prompt for the `shell` tool."""

from __future__ import annotations

from tools._names import (
    EDIT_FILE_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    WRITE_FILE_TOOL_NAME,
)


def get_shell_description() -> str:
    return (
        "Run a single bash command from the workspace root. You get captured stdout,\n"
        "stderr, and exit code, and any file writes the command performs are tracked.\n"
        "\n"
        "Use this when:\n"
        "- You need to run tests, builds, linters, type-checkers, or other tooling\n"
        "  (`pytest`, `make build`, `npm test`, `ruff check`).\n"
        "- You need a capability not exposed as a dedicated tool (git operations,\n"
        "  pip/uv/npm install, codegen).\n"
        f"- You're verifying environment state (`which python`, `git status`, `ls -la`).\n"
        "\n"
        "Prefer dedicated tools when applicable:\n"
        f"- File reads -> `{READ_FILE_TOOL_NAME}`, not `cat`/`head`/`tail`/`sed`.\n"
        f"- File mutations -> `{WRITE_FILE_TOOL_NAME}` / `{EDIT_FILE_TOOL_NAME}`. The dedicated tools produce\n"
        "  cleaner audit trails and structured errors.\n"
        f"- Filename search -> `{GLOB_TOOL_NAME}`, not `find`/`ls`.\n"
        f"- Content search -> `{GREP_TOOL_NAME}`, not `grep`/`rg` via shell.\n"
        "- Use `shell` for genuine gaps (moves via `mv`, deletes via `rm`, git,\n"
        "  codegen).\n"
        "\n"
        "Do NOT use for:\n"
        "- Long-running interactive processes (REPLs, watchers, dev servers). Each\n"
        "  call is one-shot and bounded by `timeout`.\n"
        "- Background daemons. There is no persistent shell session between calls;\n"
        "  cwd resets to the workspace root each time.\n"
        "- Streaming progress — you only get the final captured output.\n"
        "\n"
        "Capabilities and constraints:\n"
        "- Runs as bash, with the workspace root as cwd.\n"
        "- `timeout` (seconds) bounds the run; default is 900.\n"
        "- Writes performed by the command are tracked. A command that exits 0 but\n"
        '  writes outside the audited boundary returns `is_error=True` with\n'
        '  "commit aborted: ...".\n'
        "- No environment leakage between calls — set env vars inline\n"
        "  (`FOO=bar cmd ...`).\n"
        "- No interactive input — use non-interactive flags (`--yes`,\n"
        "  `--non-interactive`, `--no-input`).\n"
        "\n"
        "Output shape:\n"
        '- `status`: "ok" | "error".\n'
        "- `changed_paths`: files changed by the command.\n"
        "- `conflict_reason`: populated when the audit/commit step conflicts.\n"
        "- `command`, `exit_code`, `stdout`, `stderr`: captured command output.\n"
        '- `error`: populated when status is "error" — combines exit-code failures\n'
        "  and audit conflicts.\n"
        "\n"
        "Common pitfalls:\n"
        "- Quoting: prefer single quotes around regexes and arguments containing `$`.\n"
        "- Pipelines: pipe failures are masked unless you `set -o pipefail` inline.\n"
        "- Background `&`: don't — the audit will not see the result, and you have\n"
        "  no way to wait.\n"
        "- `cd <dir> && ...`: cwd does not persist across calls; the form is fine\n"
        "  within one call but useless across calls."
    )


__all__ = ["get_shell_description"]
