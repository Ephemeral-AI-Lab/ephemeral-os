"""Higher-level system prompt assembly."""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings

__all__ = [
    "build_runtime_system_prompt",
    "build_termination_condition_prompt",
]


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
) -> str:
    """Build the runtime instruction prompt for an agent run."""
    sections = [
        (settings.system_prompt or "").strip(),
    ]
    if settings.fast_mode:
        sections.append(
            "# Session Mode\n"
            "Fast mode is enabled. Prefer concise replies, minimal tool use, "
            "and quicker progress over exhaustive exploration."
        )

    return "\n\n".join(section for section in sections if section.strip())


def build_termination_condition_prompt(
    *,
    terminal_tools: set[str] | list[str] | None = None,
) -> str:
    """Build the runtime termination-condition section.

    Args:
        terminal_tools: Tools that terminate the run immediately when called.
    """
    sections: list[str] = []
    terminal_section = ""
    terminal_names = sorted(
        {
            str(name).strip()
            for name in (terminal_tools or [])
            if str(name).strip()
        }
    )
    if terminal_names:
        terminal_lines = [
            "WARNING: These are one-way exit tools.",
            "If you call any of them, the run terminates immediately.",
            "Your lifecycle ends at that moment: no more reasoning, no more tool calls, no recovery in the same run.",
            "Do not call a termination tool until you are fully ready to end the run.",
            "",
        ]
        terminal_lines.extend(f"- `{name}`" for name in terminal_names)
        terminal_section = "<Termination Condition>\n\n" + "\n".join(terminal_lines) + "\n\n</Termination Condition>"

    if terminal_section:
        sections.append(terminal_section)

    return "\n\n".join(sections)
