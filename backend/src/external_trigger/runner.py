"""Shared LLM loop for external_trigger and post_run tool phases.

Always uses ``tool_choice="any"`` so every turn produces a tool call attempt.
Retries indefinitely until a valid tool call passes Pydantic validation.
The only exit paths are a successful tool call or asyncio cancellation
by the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from tools.core.base import BaseTool

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Result of a successful external_trigger/post_run tool call."""

    tool_name: str
    tool_input: dict[str, Any]
    validated: BaseModel | None = None
    conversation: list[dict[str, Any]] = field(default_factory=list)
    turns_used: int = 0


async def run(
    *,
    messages: list[dict[str, Any]],
    system_prompt: str,
    prompt: str,
    tools: list[BaseTool],
    api_client: Any,
    max_tokens_per_turn: int = 500,
    model: str | None = None,
) -> RunResult:
    """Execute the LLM loop until a valid tool call succeeds.

    Parameters
    ----------
    messages:
        Frozen conversation snapshot (read-only context for the LLM).
    system_prompt:
        System prompt for the LLM session.
    prompt:
        Injected as the final user message after the snapshot.
    tools:
        Constrained tool set — the LLM must call one of these.
    api_client:
        Anthropic-compatible client with ``create_message()``.
    max_tokens_per_turn:
        Max tokens per LLM response.
    model:
        Model override. Defaults to claude-sonnet-4.
    """
    api_tools = [tool.to_api_schema() for tool in tools]
    tool_map = {tool.name: tool for tool in tools}

    conversation: list[dict[str, Any]] = list(messages) + [
        {"role": "user", "content": prompt},
    ]

    turn = 0
    while True:
        turn += 1

        try:
            response = await api_client.create_message(
                model=model or "claude-sonnet-4-20250514",
                max_tokens=max_tokens_per_turn,
                system=system_prompt,
                messages=conversation,
                tools=api_tools,
                tool_choice={"type": "any"},
            )
        except Exception:
            logger.warning(
                "external_trigger runner: API call failed on turn %d, retrying",
                turn,
                exc_info=True,
            )
            continue

        # Extract tool_use block from response
        tool_use_block: Any = None
        text_parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                tool_use_block = block
            elif getattr(block, "text", None):
                text_parts.append(block.text.strip())

        # With tool_choice="any", tool_use_block should always be present.
        # Defensive: if somehow missing, retry.
        if tool_use_block is None:
            logger.warning("external_trigger runner: no tool_use block on turn %d", turn)
            continue

        tool_name = tool_use_block.name
        tool_input = tool_use_block.input
        tool_id = getattr(tool_use_block, "id", f"tu_{turn}")

        # Build assistant message for conversation trail
        assistant_content: list[dict[str, Any]] = []
        if text_parts:
            assistant_content.append({"type": "text", "text": "\n".join(text_parts)})
        assistant_content.append({
            "type": "tool_use",
            "id": tool_id,
            "name": tool_name,
            "input": tool_input,
        })
        conversation.append({"role": "assistant", "content": assistant_content})

        # Check tool is in our set
        tool = tool_map.get(tool_name)
        if tool is None:
            tool_names = list(tool_map.keys())
            conversation.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id,
                             "content": f"Error: unknown tool '{tool_name}'. "
                                        f"Use one of: {', '.join(tool_names)}",
                             "is_error": True}],
            })
            continue

        # Pydantic validation
        validated: BaseModel | None = None
        try:
            validated = tool.input_model.model_validate(tool_input)
        except Exception as exc:
            conversation.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id,
                             "content": f"Validation error: {exc}. Fix and retry.",
                             "is_error": True}],
            })
            continue

        # Success
        return RunResult(
            tool_name=tool_name,
            tool_input=tool_input,
            validated=validated,
            conversation=conversation,
            turns_used=turn,
        )
