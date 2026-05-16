"""System prompt builder for EphemeralOS."""

from prompt.runtime_prompt import (
    build_main_role_base_prompt,
    build_runtime_context_message,
    build_runtime_system_prompt,
    build_termination_condition_prompt,
)
from prompt.environment import get_environment_info

__all__ = [
    "build_main_role_base_prompt",
    "build_runtime_context_message",
    "build_runtime_system_prompt",
    "build_termination_condition_prompt",
    "get_environment_info",
]
