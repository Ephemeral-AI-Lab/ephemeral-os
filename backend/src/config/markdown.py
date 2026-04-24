"""Markdown configuration parsing helpers."""

from __future__ import annotations

from typing import Any

import yaml


def parse_markdown_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown document into YAML frontmatter and body text."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}, content
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        return {}, content
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return frontmatter, "\n".join(lines[end + 1 :]).strip()
