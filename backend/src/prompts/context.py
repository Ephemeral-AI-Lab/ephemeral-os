"""Higher-level system prompt assembly."""

from __future__ import annotations

from pathlib import Path

from config.paths import get_project_issue_file, get_project_pr_comments_file
from config.settings import Settings
from prompts.claudemd import load_claude_md_prompt
from prompts.system_prompt import build_system_prompt
from skills.loader import load_skill_registry


def _build_skills_section(cwd: str | Path) -> str | None:
    """Build a system prompt section with full skill content.

    Injects the complete skill body (not just name/description) so the
    model has access to detailed instructions, rules, and tool usage
    discipline from each skill.
    """
    registry = load_skill_registry(cwd)
    skills = registry.list_skills()
    if not skills:
        return None
    sections = [
        "# Skills & Instructions",
        "",
        "The following skills provide detailed instructions for your work. "
        "Follow them when the task matches.",
        "",
    ]
    for skill in skills:
        sections.append(f"## {skill.name}")
        sections.append(skill.content)
        sections.append("")
    return "\n".join(sections)


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
) -> str:
    """Build the runtime system prompt with project instructions and memory."""
    sections = [build_system_prompt(custom_prompt=settings.system_prompt, cwd=str(cwd))]

    if settings.fast_mode:
        sections.append(
            "# Session Mode\nFast mode is enabled. Prefer concise replies, minimal tool use, and quicker progress over exhaustive exploration."
        )

    sections.append(
        "# Reasoning Settings\n"
        f"- Effort: {settings.effort}\n"
        f"- Passes: {settings.passes}\n"
        "Adjust depth and iteration count to match these settings while still completing the task."
    )

    skills_section = _build_skills_section(cwd)
    if skills_section:
        sections.append(skills_section)

    claude_md = load_claude_md_prompt(cwd)
    if claude_md:
        sections.append(claude_md)

    for title, path in (
        ("Issue Context", get_project_issue_file(cwd)),
        ("Pull Request Comments", get_project_pr_comments_file(cwd)),
    ):
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"# {title}\n\n```md\n{content[:12000]}\n```")

    return "\n\n".join(section for section in sections if section.strip())
