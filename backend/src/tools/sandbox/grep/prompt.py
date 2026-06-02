"""Description prompt for the `grep` tool."""

from __future__ import annotations

from tools._names import (
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    EXEC_COMMAND_TOOL_NAME,
)


def get_grep_description() -> str:
    return (
        "Regex-scan workspace file contents.\n"
        "\n"
        "Use this when:\n"
        "- You need to find which files contain a pattern (`files_with_matches`\n"
        "  mode).\n"
        "- You need to count matches per file (`count` mode).\n"
        "- You need to extract matching lines for inspection (`content` mode).\n"
        "\n"
        "Prefer over:\n"
        f"- `{EXEC_COMMAND_TOOL_NAME}` with `grep`/`rg` — `{GREP_TOOL_NAME}` is cheaper, routed read-only, and\n"
        "  returns structured output.\n"
        "\n"
        "Do NOT use for:\n"
        f"- Reading whole files — once you know the path, use `{READ_FILE_TOOL_NAME}`.\n"
        f"- Enumerating files by name (no content match) — use `{GLOB_TOOL_NAME}`.\n"
        "- Structural code search (AST-aware) — there is no `--type`-aware mode;\n"
        f"  combine `{GLOB_TOOL_NAME}` to narrow scope, then call `{GREP_TOOL_NAME}`.\n"
        "\n"
        "Capabilities and constraints:\n"
        "- Pattern is Python `re` regex (NOT PCRE2). Possessive quantifiers and\n"
        "  recursive groups are unsupported. Literal braces work without escaping.\n"
        "- VCS directories (`.git`/`.svn`/`.hg`/`.bzr`/`.jj`/`.sl`) are excluded.\n"
        "- Files larger than 10 MB and non-UTF-8 files are skipped silently.\n"
        "- Output is capped at 20 KB total content AND `head_limit` entries\n"
        "  (default 250; 0 = unlimited subject to the byte cap).\n"
        "- `multiline=True` enables `re.MULTILINE | re.DOTALL` — `.` matches\n"
        "  newlines, `^`/`$` match line boundaries.\n"
        "- `glob_filter` is fnmatch (e.g. `'*.py'`), not bash glob.\n"
        "\n"
        "Output shape:\n"
        '- `mode`: "files_with_matches" | "count" | "content".\n'
        "- `filenames`: matched files in scan order.\n"
        "- `content`: rendered match content (`content` mode) or `path:count`\n"
        "  lines (`count` mode); empty in `files_with_matches` mode.\n"
        "- `num_files`, `num_lines`, `num_matches`: cardinalities.\n"
        "- `applied_limit`, `applied_offset`, `truncated`: paging signals.\n"
        "\n"
        "Common pitfalls:\n"
        "- Forgetting `multiline=True` for cross-line patterns — the regex won't\n"
        "  match across newlines by default.\n"
        "- Over-broad scope: scanning the whole workspace for \"TODO\" returns\n"
        "  truncated output. Pass `path=...` or `glob_filter=...` to narrow first.\n"
        "- Confusing `head_limit=0` with \"no results\": 0 means \"unlimited\".\n"
        "\n"
        "Example:\n"
        "  # Find every place a symbol is defined\n"
        '  grep(pattern=r"^class (Foo|Bar)\\b", output_mode="content",\n'
        "       line_numbers=True)"
    )


__all__ = ["get_grep_description"]
