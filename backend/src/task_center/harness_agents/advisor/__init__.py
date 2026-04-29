"""Advisor role — second-LLM check on high-stakes terminal proposals.

Stage 4 of the four-role roadmap. The advisor sees the calling agent's
context object plus the proposed `(terminal_tool, input, reason)` and
emits accept/reject. There is no retry path: rejection means the calling
agent must call a different terminal next.
"""

from task_center.harness_agents.advisor import lifecycle
from task_center.harness_agents.advisor.context import AdvisorLaunchContext
from task_center.harness_agents.advisor.definition import (
    ADVISOR,
    load_system_prompt,
)

__all__ = [
    "ADVISOR",
    "AdvisorLaunchContext",
    "lifecycle",
    "load_system_prompt",
]
