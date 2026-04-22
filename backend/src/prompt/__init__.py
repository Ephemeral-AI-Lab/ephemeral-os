"""System prompt builder for EphemeralOS."""

from prompt.runtime_prompt import (
    build_runtime_context_message,
    build_runtime_system_prompt,
    build_termination_condition_prompt,
)
from prompt.environment import get_environment_info
from prompt.system_prompt import build_system_prompt

__all__ = [
    "build_runtime_context_message",
    "build_runtime_system_prompt",
    "build_system_prompt",
    "build_termination_condition_prompt",
    "get_environment_info",
]
