"""System prompt builder for EphemeralOS."""

from ephemeralos.prompts.claudemd import discover_claude_md_files, load_claude_md_prompt
from ephemeralos.prompts.context import build_runtime_system_prompt
from ephemeralos.prompts.system_prompt import build_system_prompt
from ephemeralos.prompts.environment import get_environment_info

__all__ = [
    "build_runtime_system_prompt",
    "build_system_prompt",
    "discover_claude_md_files",
    "get_environment_info",
    "load_claude_md_prompt",
]
