"""Code intelligence tools for agents."""

from tools.core.base import BaseToolkit
from tools.ci_toolkit.query_tools import (
    ci_status,
    ci_query_symbol,
    ci_workspace_structure,
)
from tools.ci_toolkit.lsp_tools import ci_diagnostics
from tools.ci_toolkit.rename_tool import ci_rename, ci_rename_symbol

_ALL_TOOLS = [
    ci_status,
    ci_workspace_structure,
    ci_query_symbol,
    ci_diagnostics,
    ci_rename,
    ci_rename_symbol,
]

_INSTRUCTIONS = (
    "Code intelligence for grounding same-run work.\n\n"
    "## CI-first discovery rule\n"
    "Always start with CI tools before falling back to grep or raw file reads:\n"
    "1. `ci_query_symbol(name)` — find where a function/class/method is defined. "
    "Use this first when you need to locate code, not grep. If you only know an exact "
    "file, one file-path query returns the indexed definitions in that file so you can "
    "continue with real symbol names.\n"
    "2. `ci_query_symbol(name, references=true)` — also trace all callers and import sites. "
    "Use this to follow import chains and find who depends on a symbol before editing it.\n"
    "3. `ci_rename(symbol, new_name)` — semantic cross-file rename by name. "
    "Resolves coordinates from the symbol index; returns `status=\"ambiguous\"` "
    "with candidates if the name is not unique (narrow with `kind` or `file_hint`). "
    "Atomic: commits all touched files or none. Prefer over chained `daytona_edit_file` "
    "calls; won't hit unrelated string/comment matches. Use `ci_rename_symbol(file, "
    "line, new_name)` when you already have coordinates. Always try `dry_run=true` first.\n"
    "4. `ci_diagnostics(file)` — check for errors after edits, before running full test suites.\n"
    "Only fall back to `daytona_grep`/`daytona_read_file` when CI tools return no results "
    "(cold index) or when you need content not captured by symbol queries.\n\n"
    "## Typical CI workflow\n"
    "- Localizing a bug: `ci_query_symbol(name)` → find definition → "
    "`ci_query_symbol(name, references=true)` → trace callers → inspect only the relevant lines.\n"
    "- Checking edit safety: `ci_query_symbol(name, references=true)` on the symbol you plan "
    "to change → see all downstream callers → `ci_diagnostics` after patching.\n\n"
    "## Other tools\n"
    "- `ci_status` — check if the code intelligence service is available and inspect edit hotspots when needed.\n"
    "- `ci_workspace_structure` — tree view of the project layout.\n"
    "- Use sandbox file reads only after CI symbol queries narrowed the seam.\n\n"
    "## Anti-patterns\n"
    "- Do not grep for a symbol name when `ci_query_symbol` can find its definition directly.\n"
    "- Do not trace callers by grepping import statements when `ci_query_symbol(name, references=true)` "
    "maps the full call graph.\n"
    "- Dead-cycle rule — if the same boundary survives one scoped packet, one owner query, "
    "and one narrow repro, stop opening more greps or readbacks and move to edit, blocker, or replan."
)


class CIToolkit(BaseToolkit):
    """Code intelligence toolkit."""

    @classmethod
    def from_context(cls, ctx):  # type: ignore[override]
        return cls(
            name="code_intelligence",
            description="Code intelligence: symbols, LSP, structure, changes",
            tools=list(_ALL_TOOLS),
            instructions=_INSTRUCTIONS,
        )


__all__ = ["CIToolkit"]
