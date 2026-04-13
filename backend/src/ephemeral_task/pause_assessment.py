"""PauseAssessmentTask — blocker impact assessment via forced tool call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ephemeral_task.core import EphemeralTaskResult, Snapshot

PAUSE_VERDICT_TOOL = {
    "name": "pause_verdict",
    "description": "Submit your assessment of whether this task is affected by the blocker.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "enum": ["YES", "NO"],
                "description": "YES if your task depends on the broken files, NO otherwise.",
            },
            "reason": {
                "type": "string",
                "description": "Brief explanation of why this task is or is not affected.",
            },
        },
        "required": ["answer", "reason"],
    },
}


@dataclass
class PauseVerdict(EphemeralTaskResult):
    """Parsed blocker assessment result — extends EphemeralTaskResult."""

    task_id: str = ""
    answer: str = ""  # "YES" or "NO"
    reason: str = ""
    conversation: list[dict] = field(default_factory=list)


async def assess_pause(
    *,
    snapshot: Snapshot,
    broken_files: list[str],
    problem: str,
    api_client: Any,
    timeout_seconds: int = 30,
    model: str | None = None,
) -> PauseVerdict:
    """Assess whether a running agent is affected by a blocker.

    Uses forced tool call (tool_choice="any") so the output is always
    a structured {answer: "YES"|"NO", reason: "..."} — no free-text parsing.
    """
    prompt = (
        "BLOCKER CHECK\n"
        "A shared dependency has been reported broken.\n"
        f"Broken files: {', '.join(broken_files)}\n"
        f"Problem: {problem}\n"
        "\n"
        "Based on your work so far in this conversation,\n"
        "does your task depend on any of these files?\n"
        "Call the pause_verdict tool with your assessment."
    )

    conversation = list(snapshot.messages) + [{"role": "user", "content": prompt}]

    result = await snapshot.ask_tool(
        prompt,
        tool=PAUSE_VERDICT_TOOL,
        api_client=api_client,
        max_tokens=200,
        timeout_seconds=timeout_seconds,
        model=model,
    )

    tid = snapshot.task_id
    base = dict(text=result.text, timed_out=result.timed_out, elapsed_seconds=result.elapsed_seconds)

    if result.timed_out:
        return PauseVerdict(
            **base, task_id=tid, answer="TIMEOUT",
            reason="LLM call timed out", conversation=conversation,
        )

    # Structured tool_input is always present when tool_choice="any" succeeds
    if result.tool_input is not None:
        answer = result.tool_input.get("answer", "NO")
        reason = result.tool_input.get("reason", "")
        conversation.append({
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "pause_verdict", "input": result.tool_input}],
        })
        return PauseVerdict(**base, task_id=tid, answer=answer, reason=reason, conversation=conversation)

    # Fallback: tool call failed somehow — treat as NO
    return PauseVerdict(**base, task_id=tid, answer="NO", reason="tool call failed", conversation=conversation)
