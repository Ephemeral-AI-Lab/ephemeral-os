"""Description prompt for the `edit_file` tool."""

from __future__ import annotations

from tools._names import (
    EDIT_FILE_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    SHELL_TOOL_NAME,
    WRITE_FILE_TOOL_NAME,
)


def get_edit_file_description() -> str:
    return (
        "Apply one exact search-and-replace edit to an existing file.\n"
        "\n"
        "Use this when:\n"
        "- You want a targeted, minimal change to an existing file (rename a symbol\n"
        "  in one spot, fix a line, add an import).\n"
        "- The block you want to replace can be unambiguously identified by 2–6\n"
        "  adjacent lines.\n"
        "\n"
        "Prefer over:\n"
        f"- `{WRITE_FILE_TOOL_NAME}` — for ANY modification of an existing file. Use\n"
        f"  `{WRITE_FILE_TOOL_NAME}` only when you are creating a new file or intentionally\n"
        "  rewriting the whole thing.\n"
        f"- `{SHELL_TOOL_NAME}` with `sed`/`awk` — `{EDIT_FILE_TOOL_NAME}` is atomic, audited, and refuses\n"
        "  ambiguous matches instead of silently mangling.\n"
        "\n"
        "Do NOT use for:\n"
        f"- Creating new files — use `{WRITE_FILE_TOOL_NAME}`.\n"
        "- Renaming a symbol across the whole repo in one call — call `edit_file`\n"
        f"  once per file, or use `{WRITE_FILE_TOOL_NAME}` if the file needs a wholesale\n"
        "  rewrite.\n"
        "\n"
        "Required precondition:\n"
        f"- You MUST have read the target file with `{READ_FILE_TOOL_NAME}` in this conversation\n"
        f"  before calling `{EDIT_FILE_TOOL_NAME}`. The tool will error otherwise — this\n"
        "  protects you from blind edits with stale assumptions about file\n"
        "  contents.\n"
        "\n"
        "Capabilities and constraints:\n"
        "- `old_text` must match byte-for-byte: whitespace, indentation, newlines,\n"
        "  all included.\n"
        "- `old_text` must be unique in the file. If it isn't, widen the match with\n"
        "  surrounding context until it is — don't trim it to be terser.\n"
        "- You cannot create new files. If the path doesn't exist, the call fails.\n"
        "- Optimistic concurrency: if the file changed under you (e.g., another\n"
        "  tool or test run wrote to it), the result is `aborted_version` —\n"
        f"  re-read, recompute `old_text`, and retry.\n"
        "\n"
        "Output shape:\n"
        '- `status`: "edited" | "aborted_version" | "failed".\n'
        "- `changed_paths`: the edited file (and any side-effects from the audit\n"
        "  layer).\n"
        "- `applied_edits`: 1 on success.\n"
        '- `conflict_reason`: populated when `status != "edited"`.\n'
        "\n"
        "Common pitfalls:\n"
        f"- Including the `<lineno><tab>` prefix from `{READ_FILE_TOOL_NAME}` output in\n"
        "  `old_text` — drop it; that prefix isn't in the file.\n"
        "- Deleting a section with empty `new_text`: works, but make sure\n"
        "  `old_text` includes the trailing newline so you don't leave a blank\n"
        "  line.\n"
        f"- Using `{EDIT_FILE_TOOL_NAME}` for find-and-replace across many occurrences in one\n"
        f"  file — split into multiple calls, or rewrite the file with\n"
        f"  `{WRITE_FILE_TOOL_NAME}`.\n"
        "\n"
        "Example:\n"
        "  # Good: 3 lines of context, unique match\n"
        "  edit_file(\n"
        '    file_path="src/foo.py",\n'
        '    old_text="def bar(x: int) -> int:\\n    return x * 2\\n\\n",\n'
        '    new_text="def bar(x: int) -> int:\\n    return x * 3\\n\\n",\n'
        "  )"
    )


__all__ = ["get_edit_file_description"]
