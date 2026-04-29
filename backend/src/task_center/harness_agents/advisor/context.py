"""Advisor dispatch context construction.

The advisor sees exactly what the calling agent saw — represented here as
a free-form ``calling_agent_context`` string — plus the proposed
``(terminal_tool, input, reason)`` triple.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from task_center.model import TaskId


_ADVISOR_PROMPT_INSTRUCTIONS = (
    "Read CALLING_AGENT_CONTEXT and PROPOSAL. Emit exactly one "
    "submit_advisor_feedback(verdict, reason). Verdict is 'accept' or "
    "'reject' — no retries; rejection means the calling agent must call a "
    "different terminal."
)


@dataclass
class AdvisorLaunchContext:
    """Structural input for an advisor task at dispatch time."""

    caller_id: TaskId
    proposed_terminal_tool: str
    proposed_input: dict[str, Any]
    agent_reason: str
    calling_agent_context: str

    def to_advisor_prompt(self) -> str:
        payload_block = json.dumps(self.proposed_input, indent=2, sort_keys=True)
        return (
            f"## INSTRUCTIONS\n{_ADVISOR_PROMPT_INSTRUCTIONS}\n\n"
            f"## CALLING_AGENT_CONTEXT\n{self.calling_agent_context}\n\n"
            f"## PROPOSAL\n"
            f"caller_task_id: {self.caller_id}\n"
            f"terminal_tool: {self.proposed_terminal_tool}\n"
            f"reason: {self.agent_reason}\n"
            f"input:\n{payload_block}"
        )
