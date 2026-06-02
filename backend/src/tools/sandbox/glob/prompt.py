"""Description prompt for the `glob` tool."""

from __future__ import annotations

from tools._names import (
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    EXEC_COMMAND_TOOL_NAME,
)


def get_glob_description() -> str:
    return (
        "Enumerate workspace files matching a glob pattern.\n"
        "\n"
        "Use this when:\n"
        "- You need a list of files by name or extension (e.g. \"every Python file\n"
        "  in `pkg/`\").\n"
        f"- You're narrowing scope before a more expensive operation (`{GREP_TOOL_NAME}`,\n"
        f"  `{READ_FILE_TOOL_NAME}` per file).\n"
        "\n"
        "Prefer over:\n"
        f"- `{EXEC_COMMAND_TOOL_NAME}` with `find`/`ls` — `{GLOB_TOOL_NAME}` returns structured output.\n"
        "\n"
        "Do NOT use for:\n"
        f"- Searching file CONTENTS — use `{GREP_TOOL_NAME}`.\n"
        "- Recursive directory walks across hidden VCS data — `.git/`, `.svn/`,\n"
        "  etc. are excluded by design.\n"
        "- Following symlinks — symlinks are listed but not traversed.\n"
        "\n"
        "Capabilities and constraints:\n"
        "- Pattern is Python `fnmatch` style. `*` matches within a path segment;\n"
        "  `**` does NOT recurse — set `path=...` plus a `*.py`-style narrowing\n"
        "  instead.\n"
        "- Brace expansion (`{a,b}`) is NOT supported.\n"
        "- Leading-dot VCS directories are excluded.\n"
        "- Result set is capped at 100 paths. Narrow with `path=...` when\n"
        "  truncated.\n"
        "\n"
        "Output shape:\n"
        "- `filenames`: workspace-relative matched paths.\n"
        "- `num_files`: count returned (post-cap).\n"
        "- `truncated`: True when the cap was hit.\n"
        "\n"
        "Common pitfalls:\n"
        "- Expecting `**/*.py` to recurse — it does not. Use `path=\"src\"` and\n"
        "  `pattern=\"*.py\"`, or call `glob` from a deeper `path` to scope down.\n"
        "- Treating truncation as \"all results\" — check `truncated` before\n"
        "  assuming completeness."
    )


__all__ = ["get_glob_description"]
