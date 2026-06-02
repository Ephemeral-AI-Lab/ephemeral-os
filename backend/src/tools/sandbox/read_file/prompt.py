"""Description prompt for the `read_file` tool."""

from __future__ import annotations

from tools._names import (
    EDIT_FILE_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    EXEC_COMMAND_TOOL_NAME,
    WRITE_FILE_TOOL_NAME,
)
from tools.sandbox._lib.file_payloads import MAX_READ_FILE_LINES


def get_read_file_description() -> str:
    return (
        "Read a UTF-8 text file from the workspace, returned with line numbers.\n"
        "\n"
        "Use this when:\n"
        "- You need the actual contents of a specific file path.\n"
        f"- You need to inspect a code/config region before editing it (`{EDIT_FILE_TOOL_NAME}`\n"
        "  requires you to read first).\n"
        "\n"
        "Prefer over:\n"
        f"- `{EXEC_COMMAND_TOOL_NAME}` with `cat`/`sed -n`/`head`/`tail` — `{READ_FILE_TOOL_NAME}` is cheaper,\n"
        "  returns structured output, and integrates with the edit precondition\n"
        "  check.\n"
        "\n"
        "Do NOT use for:\n"
        "- Binary files (PDF, images, archives) — output is UTF-8 only.\n"
        f"- Directory listings — use `{GLOB_TOOL_NAME}`.\n"
        f"- Searching for content across many files — use `{GREP_TOOL_NAME}`.\n"
        f"- Re-reading a file you just edited — `{EDIT_FILE_TOOL_NAME}`/`{WRITE_FILE_TOOL_NAME}` would have\n"
        "  errored if the change failed; the harness already tracks the new\n"
        "  content.\n"
        "\n"
        "Capabilities and constraints:\n"
        f"- You can read up to MAX_READ_FILE_LINES ({MAX_READ_FILE_LINES}) per call. Use `start_line`\n"
        "  and `end_line` to page through larger files.\n"
        "- Paths are workspace-relative or workspace-absolute. Paths outside the\n"
        "  workspace return an error.\n"
        "- Output is line-numbered with `cat -n` style prefixes\n"
        "  (`<lineno><tab>line`), making it easy to cite specific lines\n"
        "  (file_path:lineno).\n"
        "\n"
        "Output shape:\n"
        "- `file_path`: resolved path.\n"
        "- `content`: numbered text block.\n"
        "- `start_line`, `end_line`: window actually returned.\n"
        "- `truncated`: True when more lines exist past `end_line`.\n"
        "\n"
        "Common pitfalls:\n"
        f"- Indentation in `{EDIT_FILE_TOOL_NAME}`: the line-number prefix is NOT part of file\n"
        f"  content. When you echo a line back into `{EDIT_FILE_TOOL_NAME}.old_text`, drop the\n"
        "  `<lineno><tab>` prefix.\n"
        "- Stale reads: if another tool has changed the file since your last read,\n"
        f"  the next `{EDIT_FILE_TOOL_NAME}` may return `aborted_version` — re-read and retry.\n"
        "- Empty files return an empty `content`, not an error."
    )


__all__ = ["get_read_file_description"]
