"""Shared LLM loop for external_trigger and post_run tool phases.

Always uses ``tool_choice={"type": "any"}`` so every turn produces a tool call.
Retries up to ``max_turns`` until a valid tool call passes Pydantic validation.
Exit paths: successful tool call, max turns exhausted, or asyncio cancellation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from providers.types import ApiMessageRequest, ApiToolUseDeltaEvent, ApiMessageCompleteEvent
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


async def _stream_to_response(api_client: Any, request: ApiMessageRequest) -> Any:
    """Consume stream_message and collect tool_use events + final message."""
    tool_uses: list[dict[str, Any]] = []
    text_parts: list[str] = []
    final_message: Any = None

    async for event in api_client.stream_message(request):
        if isinstance(event, ApiToolUseDeltaEvent):
            tool_uses.append({
                "type": "tool_use",
                "id": event.id,
                "name": event.name,
                "input": event.input,
            })
        elif isinstance(event, ApiMessageCompleteEvent):
            final_message = event.message

    # Build a lightweight response-like object
    class _Block:
        def __init__(self, d: dict[str, Any]) -> None:
            self.type = d.get("type", "")
            self.name = d.get("name", "")
            self.input = d.get("input", {})
            self.id = d.get("id", "")
            self.text = d.get("text", "")

    blocks: list[_Block] = []
    # Extract text from final message if available
    if final_message is not None:
        for cb in final_message.content:
            if hasattr(cb, "text") and getattr(cb, "text", None):
                blocks.append(_Block({"type": "text", "text": cb.text}))
    # Add tool_use blocks from mid-stream events
    for tu in tool_uses:
        blocks.append(_Block(tu))

    class _Response:
        def __init__(self, content: list[_Block]) -> None:
            self.content = content

    return _Response(blocks)


async def run(
    *,
    messages: list[dict[str, Any]],
    system_prompt: str,
    prompt: str,
    tools: list[BaseTool],
    api_client: Any,
    max_tokens_per_turn: int = 500,
    model: str | None = None,
    max_turns: int = 10,
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
        Client implementing ``stream_message(ApiMessageRequest)``.
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
    while turn < max_turns:
        turn += 1

        request = ApiMessageRequest(
            model=model or "claude-sonnet-4-20250514",
            max_tokens=max_tokens_per_turn,
            system_prompt=system_prompt,
            tools=api_tools,
            tool_choice={"type": "any"},
            raw_messages=conversation,
        )

        try:
            response = await _stream_to_response(api_client, request)
        except Exception:
            logger.warning(
                "external_trigger runner: API call failed on turn %d/%d, retrying",
                turn,
                max_turns,
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

    raise RuntimeError(f"external_trigger runner: exhausted {max_turns} turns without valid tool call")
