"""A14 — Claude Code tool-name collision guard.

Per `.planning/coding_plan_mode_plan.md` v8 §A14, all EphemeralOS tool names
must NOT collide (case-sensitive) with Claude Code's built-in tool names
when shipped on the Anthropic OAuth (plan-mode) path. Three independent
reference implementations (hermes-agent, earendil-works/pi, openclaw) rewrite
or prefix any colliding names; we instead enforce snake_case + non-collision
by convention and verify with this CI test.

Today: zero collisions are possible because every EphemeralOS tool is
snake_case (`read_file`, `edit_file`, `exec_command`, ...) while Claude Code's
reserved set is PascalCase (`Read`, `Edit`, `Bash`, ...). This test future-
proofs against an accidental PascalCase introduction.

Per v8 A14 the reserved-name frozenset lives INLINE in this file (NOT in a
module constant exported from `providers/clients/coding_plan/`) — the only
load-bearing copy is here.
"""

from __future__ import annotations

import pytest

from tools.ask_helper import make_ask_helper_tools
from tools.sandbox._lib.registry import make_sandbox_tools
from tools.submission import make_submission_tools

# Sourced from Claude Code's built-in tool surface as of Claude Code 2.1.x.
# This is the set that Anthropic's OAuth Messages API treats as "canonical"
# Claude Code tools — colliding by name forces a server-side rewrite at best
# and may content-filter at worst. Keep this list manually curated against
# Claude Code releases; expand if/when Anthropic adds new built-ins.
_CLAUDE_CODE_RESERVED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "Read",
        "Edit",
        "Bash",
        "Glob",
        "Grep",
        "Write",
        "WebFetch",
        "WebSearch",
        "TodoWrite",
        "Task",
        "MultiEdit",
        "NotebookEdit",
        "BashOutput",
        "KillShell",
        "ExitPlanMode",
    }
)


def _all_static_tool_names() -> list[str]:
    """Collect tool names from every static factory in `backend/src/tools/`.

    Excludes dynamic / context-bound factories that require runtime state
    (subagent, skills) — those instantiate tool definitions that are
    structurally identical to the static set and don't introduce new names
    on the OAuth wire.
    """
    names: list[str] = []
    for tool in make_sandbox_tools():
        names.append(tool.name)
    for tool in make_submission_tools():
        names.append(tool.name)
    for tool in make_ask_helper_tools():
        names.append(tool.name)
    return names


@pytest.mark.parametrize("tool_name", _all_static_tool_names())
def test_tool_name_does_not_collide_with_claude_code_reserved_set(
    tool_name: str,
) -> None:
    """Every EphemeralOS tool name must NOT appear in the Claude Code reserved set.

    If this test ever fails, the OAuth plan-mode path is at risk: the
    server-side renormalizer may rewrite the tool name back to Claude
    Code's canonical casing, breaking tool-call dispatch in our runtime.
    Fix by renaming the offending tool to snake_case before merging.
    """
    assert tool_name not in _CLAUDE_CODE_RESERVED_TOOL_NAMES, (
        f"Tool {tool_name!r} collides with Claude Code reserved set. "
        f"Rename to snake_case before merging. See plan A14."
    )


def test_all_tool_names_are_lowercase_snake_case() -> None:
    """Bonus assertion: every name is lowercase + only contains [a-z0-9_].

    This is stronger than the reserved-set check — it future-proofs against
    Anthropic expanding the reserved set, since lowercase snake_case names
    cannot collide with PascalCase reserved names regardless of how that
    set evolves.
    """
    bad: list[str] = []
    for name in _all_static_tool_names():
        if not name.replace("_", "").replace(":", "").isalnum():
            bad.append(name)
            continue
        if name != name.lower():
            bad.append(name)
    assert not bad, (
        f"Non-snake-case tool names detected: {bad}. "
        f"All tool names must be lowercase snake_case to avoid future "
        f"collisions with Claude Code reserved names. See plan A14."
    )
