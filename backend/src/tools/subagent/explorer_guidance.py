"""Static launch prompt for the explorer subagent."""

from __future__ import annotations


EXPLORER_DIRECTIVE = "Investigate the parent's question and return concrete findings."


def build_explorer_launch_prompt() -> str:
    """Return the run prompt used after the parent's free-text task message."""
    return (
        "# What's in context\n"
        "- Parent's user message above\n"
        "\n"
        "# What to do\n"
        f"- {EXPLORER_DIRECTIVE}\n"
        "\n"
        "## Deliver\n"
        "- File paths, line numbers, specific symbols. No vague hand-waves.\n"
        "- Missing context the parent will need to act on the findings.\n"
        "- Obvious areas you skipped.\n"
        "\n"
        "## Submit\n"
        "Call `submit_exploration_result`."
    )


__all__ = ["EXPLORER_DIRECTIVE", "build_explorer_launch_prompt"]
