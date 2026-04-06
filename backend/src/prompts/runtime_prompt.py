"""Higher-level system prompt assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config.paths import get_project_issue_file, get_project_pr_comments_file
from config.settings import Settings
from prompts.system_prompt import build_system_prompt
from tools.core.base import BaseToolkit


def render_template(template: str, variables: dict[str, Any]) -> str:
    """Render a template with variable substitution.

    Supports {{variable}} syntax. Variables are auto-converted to strings.

    Args:
        template: Template string with {{variable}} placeholders.
        variables: Dict of variable names to values.

    Returns:
        Rendered string with all placeholders substituted.
    """
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        template = template.replace(placeholder, str(value) if value is not None else "")
    return template


def render_section(template: str, variables: dict[str, Any], condition: bool = True) -> str:
    """Render a section template if condition is truthy.

    Args:
        template: Section template with {{variable}} placeholders.
        variables: Dict of variable names to values.
        condition: If False, returns empty string.

    Returns:
        Rendered section or empty string if condition is falsy.
    """
    if not condition:
        return ""
    return render_template(template, variables)


# =============================================================================
# CLAUDE.md Discovery and Loading
# =============================================================================


def discover_claude_md_files(cwd: str | Path) -> list[Path]:
    """Discover all CLAUDE.md files in the project directory.

    Args:
        cwd: The working directory to search in.

    Returns:
        List of Path objects for discovered CLAUDE.md files.
    """
    if isinstance(cwd, str):
        cwd = Path(cwd)

    results: list[Path] = []
    current = cwd.resolve()

    while True:
        claude_md = current / "CLAUDE.md"
        if claude_md.exists():
            results.append(claude_md)

        parent = current.parent
        if parent == current:
            break
        current = parent

    return results


def load_claude_md_content(cwd: str | Path) -> str | None:
    """Load CLAUDE.md content, using the first file found walking up from cwd.

    Args:
        cwd: The working directory to search in.

    Returns:
        The CLAUDE.md content as a string, or None if not found.
    """
    files = discover_claude_md_files(cwd)
    if not files:
        return None

    content = files[0].read_text(encoding="utf-8", errors="replace")
    return content.strip() if content.strip() else None


# =============================================================================
# Runtime Prompt Builders
# =============================================================================


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
) -> str:
    """Build the runtime system prompt with project instructions and memory."""
    variables = {
        "base_prompt": build_system_prompt(
            agent_system_prompt=settings.system_prompt, cwd=str(cwd)
        ),
        "fast_mode": settings.fast_mode,
        "effort": settings.effort,
        "passes": settings.passes,
        "claude_md": load_claude_md_content(cwd),
        "cwd": str(cwd),
    }

    sections = [
        variables["base_prompt"],
        render_section(
            "# Session Mode\n"
            "Fast mode is enabled. Prefer concise replies, minimal tool use, "
            "and quicker progress over exhaustive exploration.",
            variables,
            condition=variables["fast_mode"],
        ),
        "# Reasoning Settings\n"
        f"- Effort: {variables['effort']}\n"
        f"- Passes: {variables['passes']}\n"
        "Adjust depth and iteration count to match these settings while still completing the task.",
    ]

    if variables["claude_md"]:
        sections.append(variables["claude_md"])

    for title, path in (
        ("Issue Context", get_project_issue_file(cwd)),
        ("Pull Request Comments", get_project_pr_comments_file(cwd)),
    ):
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"# {title}\n\n```md\n{content[:12000]}\n```")

    return "\n\n".join(section for section in sections if section.strip())


def build_agent_capabilities_prompt(
    toolkits: list[BaseToolkit],
    has_background_tools: bool = False,
    bg_tool_names: list[str] | None = None,
) -> str:
    """Build the full toolkit and capability awareness section.

    Args:
        toolkits: Registered toolkits for behavioral guidance.
        has_background_tools: Whether background execution is available.
        bg_tool_names: Names of tools that support background execution.
    """
    sections: list[str] = []

    # Toolkit instructions — only include toolkits that have behavioral guidance
    tk_sections = []
    for tk in toolkits:
        if tk.instructions:
            tk_sections.append(f"## {tk.name}\n{tk.instructions}")
    if tk_sections:
        sections.append("# Toolkit Instructions\n\n" + "\n\n".join(tk_sections))

    # Task note enforcement (when background tools are available)
    if has_background_tools:
        sections.append(build_task_note_prompt())

    return "\n\n".join(sections)


def build_task_note_prompt() -> str:
    """Build the system prompt section for the mandatory task_note field."""
    return (
        "# Tool Call Notes\n\n"
        '**Every tool call MUST include a `"task_note"` field (~20 words) '
        "describing what you are doing and why.** The call will be rejected without it. "
        "This note appears in logs and progress reports "
        "so you can recall context later.\n\n"
        'Example: `"task_note": "running full pytest suite to verify auth changes before merge to main"`\n'
    )
