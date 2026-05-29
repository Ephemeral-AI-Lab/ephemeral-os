"""Description prompt for the `multi_edit` tool."""

from __future__ import annotations

from tools._names import (
    EDIT_FILE_TOOL_NAME,
    MULTI_EDIT_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    WRITE_FILE_TOOL_NAME,
)


def get_multi_edit_description() -> str:
    return (
        "Apply an ordered batch of search-and-replace edits to a single file in\n"
        "one atomic call.\n"
        "\n"
        "Use this when:\n"
        "- You need several distinct edits to the SAME file and want them to land\n"
        "  together or not at all.\n"
        "- Later edits build on earlier ones: each edit is applied against the\n"
        "  content produced by the previous edit (edit N sees edit N-1's result).\n"
        "\n"
        "Semantics:\n"
        "- Single `file_path`; `edits` is an ordered array of\n"
        "  `{old_text, new_text, replace_all}` objects applied in order.\n"
        "- All-or-nothing: if ANY edit fails (anchor not found, occurrence count\n"
        "  mismatch, or a concurrent change), the whole call aborts and NOTHING is\n"
        "  written.\n"
        "- Per-edit `replace_all`: each edit independently chooses unique-match\n"
        "  (default) or replace-every-occurrence.\n"
        "- `applied_edits` counts edits applied, not occurrences.\n"
        "\n"
        "Required precondition:\n"
        f"- You MUST have read the target file with `{READ_FILE_TOOL_NAME}` in this\n"
        f"  conversation before calling `{MULTI_EDIT_TOOL_NAME}`.\n"
        "\n"
        "Prefer over:\n"
        f"- `{EDIT_FILE_TOOL_NAME}` — use that for a SINGLE change to one file.\n"
        f"- `{WRITE_FILE_TOOL_NAME}` — use that only to create a new file or rewrite\n"
        "  a file wholesale.\n"
        "\n"
        "Concurrency caveat:\n"
        "- `replace_all` replaces however many occurrences exist in the CURRENT\n"
        "  committed content and does NOT detect concurrent edits to that file;\n"
        "  prefer the default unique-match mode when correctness depends on the\n"
        "  file being unchanged.\n"
        "\n"
        "Example:\n"
        "  multi_edit(\n"
        '    file_path="src/foo.py",\n'
        "    edits=[\n"
        '      {"old_text": "import os", "new_text": "import os\\nimport sys"},\n'
        '      {"old_text": "old_name", "new_text": "new_name", "replace_all": True},\n'
        "    ],\n"
        "  )"
    )


__all__ = ["get_multi_edit_description"]
