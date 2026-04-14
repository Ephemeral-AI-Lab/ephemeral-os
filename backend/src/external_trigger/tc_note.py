"""Task-center note — spawns an ephemeral agent to generate a progress note."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from external_trigger.runner import run
from tools.context.toolkit import PostNoteTool, PostNoteInput


TC_NOTE_EDIT_PROMPT = (
    "Based on this agent's work so far, write a progress note "
    "for the Task Center.\n"
    "Focus on: what files were edited and why.\n"
    "Include file paths and specific changes made.\n"
    "Keep under 300 words.\n"
    "Call the post_note tool with your note. Include a 'tags' field "
    "with one or more of: implementation, bug_fix, refactor, blocker, warning. "
    "Use 'blocker' if the agent appears stuck."
)

TC_NOTE_TURN_PROMPT = (
    "Based on this agent's work so far, write a progress note "
    "for the Task Center.\n"
    "Include:\n"
    "1. What the agent has accomplished\n"
    "2. Current status (working / stuck / nearly done)\n"
    "3. Whether the agent appears blocked by code that another "
    "task broke (include the file path and error if so)\n"
    "Keep under 300 words.\n"
    "Call the post_note tool with your note. Include a 'tags' field "
    "with one or more of: implementation, bug_fix, blocker, warning, discovery. "
    "Use 'blocker' if the agent appears stuck or blocked by another task's changes."
)

TC_NOTE_SYSTEM_PROMPT = (
    "You are a progress reporter. Read the agent's conversation and "
    "produce a concise progress note. Report facts only — do not "
    "instruct the agent or suggest next steps."
)


@dataclass
class NoteSummary:
    """Result of a tc_note generation."""

    task_id: str
    trigger: str  # "edit" or "turn"
    content: str
    turns_used: int = 0


async def run_tc_note(
    *,
    task_id: str,
    agent_run_id: str,
    messages: list[dict[str, Any]],
    prompt: str,
    trigger: str,
    max_tokens: int = 500,
    model: str | None = None,
    api_client: Any,
) -> NoteSummary:
    """Spawn an ephemeral agent to generate a task-center progress note.

    The agent inherits the task's conversation snapshot and has only the
    post_note tool available. Uses runner.run() for guaranteed tool call.
    """
    result = await run(
        agent_name=f"tc_note:{task_id}",
        messages=messages,
        system_prompt=TC_NOTE_SYSTEM_PROMPT,
        prompt=prompt,
        tools=[PostNoteTool()],
        api_client=api_client,
        max_tokens_per_turn=max_tokens,
        model=model,
    )

    validated = result.validated
    if not isinstance(validated, PostNoteInput):
        raise RuntimeError(
            f"run_tc_note (task={task_id}): runner returned unexpected "
            f"validated type {type(validated).__name__}, expected PostNoteInput"
        )

    return NoteSummary(
        task_id=task_id,
        trigger=trigger,
        content=validated.content,
        turns_used=result.turns_used,
    )
