"""Bundled skill definitions loaded from directory-based skills.

Each skill is a subdirectory of ``backend/config/skills`` containing a ``SKILL.md``
file with optional YAML frontmatter (``name``, ``description``).
"""

from __future__ import annotations

from config.markdown import parse_markdown_frontmatter
from config.paths import get_config_skills_dir
from skills.core.types import SkillDefinition

_CONTENT_DIR = get_config_skills_dir()


def get_bundled_skills() -> list[SkillDefinition]:
    """Load all bundled skills from ``backend/config/skills``."""
    skills: list[SkillDefinition] = []
    if not _CONTENT_DIR.exists():
        return skills

    # Directory-based skills: content/<skill-name>/SKILL.md
    for skill_dir in sorted(_CONTENT_DIR.iterdir()):
        if skill_dir.is_dir():
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                name, description = _parse_skill_metadata(skill_dir.name, content)
                # Discover reference files in references/ subdirectory
                references: dict[str, str] = {}
                refs_dir = skill_dir / "references"
                if refs_dir.is_dir():
                    for ref_file in sorted(refs_dir.glob("*.md")):
                        references[ref_file.stem] = ref_file.read_text(encoding="utf-8")
                skills.append(
                    SkillDefinition(
                        name=name,
                        description=description,
                        content=content,
                        source="bundled",
                        path=str(skill_dir),
                        references=references,
                    )
                )

    return skills


def _parse_skill_metadata(default_name: str, content: str) -> tuple[str, str]:
    """Extract name and description from a skill markdown file with YAML frontmatter."""
    frontmatter, _body = parse_markdown_frontmatter(content)
    name = str(frontmatter.get("name") or default_name)
    description = str(frontmatter.get("description") or "")

    # Fallback: heading + first paragraph
    if not description:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                if not name or name == default_name:
                    name = stripped[2:].strip() or default_name
                continue
            if stripped and not stripped.startswith("---") and not stripped.startswith("#"):
                description = stripped[:200]
                break

    return name, description or f"Bundled skill: {name}"
