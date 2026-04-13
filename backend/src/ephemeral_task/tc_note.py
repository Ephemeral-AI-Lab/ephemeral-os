"""CheckpointTask — progress note generation via forced tool call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ephemeral_task.core import EphemeralTaskResult, Snapshot

EDIT_CHECKPOINT_PROMPT = (
    "Based on this agent's work so far, write a progress note "
    "for the Task Center.\n"
    "Focus on: what files were edited and why.\n"
    "Include file paths and specific changes made.\n"
    "Keep under 300 words.\n"
    "Call the post_note tool with your note."
)

TURN_CHECKPOINT_PROMPT = (
    "Based on this agent's work so far, write a progress note "
    "for the Task Center.\n"
    "Include:\n"
    "1. What the agent has accomplished\n"
    "2. Current status (working / stuck / nearly done)\n"
    "3. Whether the agent appears blocked by code that another "
    "task broke (include the file path and error if so)\n"
    "Keep under 300 words.\n"
    "Call the post_note tool with your note."
)

CHECKPOINT_SYSTEM_PROMPT = (
    "You are a progress reporter. Read the agent's conversation and "
    "produce a concise progress note. Report facts only — do not "
    "instruct the agent or suggest next steps."
)

POST_NOTE_TOOL = {
    "name": "post_note",
    "description": "Post a progress note to the Task Center summarizing this agent's work.",
    "input_schema": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "The progress note content. Include file paths, changes made, and current status.",
            },
            "status": {
                "type": "string",
                "enum": ["working", "stuck", "nearly_done", "blocked"],
                "description": "Current status of the agent's work.",
            },
            "blocked_by": {
                "type": "string",
                "description": "If status is 'blocked', the file path or dependency causing the block. Empty string otherwise.",
            },
        },
        "required": ["note", "status"],
    },
}


@dataclass
class NoteSummary(EphemeralTaskResult):
    """Parsed checkpoint note result — extends EphemeralTaskResult."""

    task_id: str = ""
    trigger: str = ""  # "edit" or "turn"
    status: str = ""  # "working", "stuck", "nearly_done", "blocked"
    blocked_by: str = ""


async def run_checkpoint(
    *,
    snapshot: Snapshot,
    prompt: str,
    trigger: str = "",
    timeout_seconds: int = 30,
    max_tokens: int = 500,
    model: str | None = None,
    api_client: object,
) -> NoteSummary:
    """Single-shot checkpoint call with forced tool use. Returns NoteSummary."""
    result = await snapshot.ask_tool(
        prompt,
        tool=POST_NOTE_TOOL,
        api_client=api_client,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        model=model,
    )

    note_text = result.text
    status = ""
    blocked_by = ""

    if result.tool_input is not None:
        note_text = result.tool_input.get("note", result.text)
        status = result.tool_input.get("status", "")
        blocked_by = result.tool_input.get("blocked_by", "")

    return NoteSummary(
        text=note_text,
        timed_out=result.timed_out,
        elapsed_seconds=result.elapsed_seconds,
        task_id=snapshot.task_id,
        trigger=trigger,
        status=status,
        blocked_by=blocked_by,
    )
