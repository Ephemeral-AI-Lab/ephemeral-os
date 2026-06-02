"""Description prompt for the `write_file` tool."""

from __future__ import annotations

from tools._names import (
    EDIT_FILE_TOOL_NAME,
    EXEC_COMMAND_TOOL_NAME,
    WRITE_FILE_TOOL_NAME,
)


def get_write_file_description() -> str:
    return (
        "Create a new file, or completely overwrite an existing one, with UTF-8 text.\n"
        "\n"
        "Use this when:\n"
        "- You are creating a file from scratch.\n"
        "- You are intentionally rewriting the entire contents of an existing file\n"
        "  (e.g., a generated artifact, a config rewritten from a template).\n"
        "\n"
        "Prefer over:\n"
        f"- `{EXEC_COMMAND_TOOL_NAME}` with `echo >` or here-docs — `{WRITE_FILE_TOOL_NAME}` is atomic and\n"
        "  audited; command redirection is not.\n"
        "\n"
        "Do NOT use for:\n"
        f"- Partial changes to an existing file — use `{EDIT_FILE_TOOL_NAME}`. `{WRITE_FILE_TOOL_NAME}`\n"
        "  will silently destroy any content you don't supply.\n"
        "- Appending — there is no append mode. To add to a file, read it, then\n"
        "  write the combined content.\n"
        "- Creating directories — the parent directory must already exist. Use\n"
        f"  `{EXEC_COMMAND_TOOL_NAME}` to `mkdir -p` first if needed.\n"
        "\n"
        "Capabilities and constraints:\n"
        "- The call always overwrites if the path exists. There is no \"create if\n"
        "  not exists\" mode.\n"
        "- UTF-8 text only. Binary content is not supported.\n"
        "- Path must be workspace-relative or workspace-absolute.\n"
        "\n"
        "Output shape:\n"
        '- `status`: "written" or a failure status.\n'
        "- `file_path`: resolved path.\n"
        "- `bytes_written`: UTF-8 byte count of `content`.\n"
        "- `changed_paths`: typically `[file_path]`.\n"
        "\n"
        "Common pitfalls:\n"
        f"- Using `{WRITE_FILE_TOOL_NAME}` to \"fix one line\" — that's almost always wrong; use\n"
        f"  `{EDIT_FILE_TOOL_NAME}`. Wholesale rewrites are reviewer-hostile and easy to get\n"
        "  wrong.\n"
        "- Forgetting the trailing newline — most repos expect files to end with\n"
        "  `\\n`."
    )


__all__ = ["get_write_file_description"]
