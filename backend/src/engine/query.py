"""Core tool-aware query loop."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from ephemeralos.utils.compact import SessionState

from ephemeralos.models.types import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    SupportsStreamingMessages,
    UsageSnapshot,
)
from ephemeralos.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from ephemeralos.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
    ThinkingDelta,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from ephemeralos.hooks import HookEvent, HookExecutor
from ephemeralos.tools.base import ToolExecutionContext
from ephemeralos.tools.base import ToolRegistry


@dataclass
class QueryContext:
    """Context shared across a query run."""

    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    max_turns: int = 200
    hook_executor: HookExecutor | None = None
    tool_metadata: dict[str, object] | None = None
    session_state: "SessionState | None" = None


_TEXT_TOOL_CALL_RE = re.compile(
    r"\[TOOL_CALL\]\s*(.*?)\s*\[/TOOL_CALL\]", re.DOTALL
)


def _parse_text_tool_calls(text: str) -> list[ToolUseBlock]:
    """Parse [TOOL_CALL]...[/TOOL_CALL] markers from model text.

    Supports formats like:
      {tool => "name", args => {...}}
      {"tool": "name", "args": {...}}
    """
    results: list[ToolUseBlock] = []
    for match in _TEXT_TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        tool_name: str | None = None
        tool_args: dict = {}

        # Try JSON format first: {"tool": "name", "args": {...}}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                tool_name = parsed.get("tool") or parsed.get("name")
                tool_args = parsed.get("args") or parsed.get("input") or {}
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: {tool => "name", args => {...}}
        if tool_name is None:
            name_match = re.search(r'tool\s*(?:=>|:)\s*"([^"]+)"', raw)
            if name_match:
                tool_name = name_match.group(1)
            args_match = re.search(r'args\s*(?:=>|:)\s*(\{[\s\S]*\})', raw)
            if args_match:
                try:
                    tool_args = json.loads(args_match.group(1))
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

        if tool_name:
            results.append(ToolUseBlock(
                id=f"text-tc-{uuid.uuid4().hex[:8]}",
                name=tool_name,
                input=tool_args,
            ))
    return results


async def run_query(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Run the conversation loop until the model stops requesting tools.

    Auto-compaction is checked at the start of each turn.  When the
    estimated token count exceeds the model's auto-compact threshold,
    the engine first tries a cheap microcompact (clearing old tool result
    content) and, if that is not enough, performs a full LLM-based
    summarization of older messages.
    """
    from ephemeralos.utils.compact import (
        SessionState,
        auto_compact_if_needed,
    )

    compact_state = context.session_state or SessionState()

    for _ in range(context.max_turns):
        # --- auto-compact check before calling the model ---------------
        messages, was_compacted = await auto_compact_if_needed(
            messages,
            api_client=context.api_client,
            model=context.model,
            system_prompt=context.system_prompt,
            state=compact_state,
        )
        # ---------------------------------------------------------------

        final_message: ConversationMessage | None = None
        usage = UsageSnapshot()

        async for event in context.api_client.stream_message(
            ApiMessageRequest(
                model=context.model,
                messages=messages,
                system_prompt=context.system_prompt,
                max_tokens=context.max_tokens,
                tools=context.tool_registry.to_api_schema(),
            )
        ):
            if isinstance(event, ApiThinkingDeltaEvent):
                yield ThinkingDelta(text=event.text), None
                continue

            if isinstance(event, ApiTextDeltaEvent):
                yield AssistantTextDelta(text=event.text), None
                continue

            if isinstance(event, ApiMessageCompleteEvent):
                final_message = event.message
                usage = event.usage

        if final_message is None:
            raise RuntimeError("Model stream finished without a final message")

        messages.append(final_message)
        yield AssistantTurnComplete(message=final_message, usage=usage), usage

        # --- Text-based tool calls ([TOOL_CALL]...[/TOOL_CALL]) ----------
        # Models like MiniMax embed tool calls as text markers instead of
        # using the structured function-calling API.  We parse those markers,
        # execute the tools, and feed results back as a user text message so
        # the model sees them in its own format.
        if not final_message.tool_uses and final_message.text:
            text_tool_calls = _parse_text_tool_calls(final_message.text)
            if text_tool_calls:
                result_parts: list[str] = []
                for tc in text_tool_calls:
                    yield ToolExecutionStarted(
                        tool_name=tc.name, tool_input=tc.input,
                    ), None
                    result = await _execute_tool_call(
                        context, tc.name, tc.id, tc.input,
                    )
                    yield ToolExecutionCompleted(
                        tool_name=tc.name,
                        output=result.content,
                        is_error=result.is_error,
                    ), None
                    result_parts.append(
                        f"[TOOL_RESULT]\n"
                        f"tool: {tc.name}\n"
                        f"{'error: true' + chr(10) if result.is_error else ''}"
                        f"{result.content}\n"
                        f"[/TOOL_RESULT]"
                    )
                # Feed results back as a user message in text format
                messages.append(
                    ConversationMessage.from_user_text("\n\n".join(result_parts))
                )
                continue  # next turn — model will see the results

        if not final_message.tool_uses:
            return

        tool_calls = final_message.tool_uses

        if len(tool_calls) == 1:
            # Single tool: sequential (stream events immediately)
            tc = tool_calls[0]
            yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None
            result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
            yield ToolExecutionCompleted(
                tool_name=tc.name,
                output=result.content,
                is_error=result.is_error,
            ), None
            tool_results = [result]
        else:
            # Multiple tools: execute concurrently, emit events after
            for tc in tool_calls:
                yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None

            async def _run(tc):
                return await _execute_tool_call(context, tc.name, tc.id, tc.input)

            results = await asyncio.gather(*[_run(tc) for tc in tool_calls])
            tool_results = list(results)

            for tc, result in zip(tool_calls, tool_results):
                yield ToolExecutionCompleted(
                    tool_name=tc.name,
                    output=result.content,
                    is_error=result.is_error,
                ), None

        messages.append(ConversationMessage(role="user", content=tool_results))

    raise RuntimeError(f"Exceeded maximum turn limit ({context.max_turns})")


async def _execute_tool_call(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, object],
) -> ToolResultBlock:
    if context.hook_executor is not None:
        pre_hooks = await context.hook_executor.execute(
            HookEvent.PRE_TOOL_USE,
            {"tool_name": tool_name, "tool_input": tool_input, "event": HookEvent.PRE_TOOL_USE.value},
        )
        if pre_hooks.blocked:
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=pre_hooks.reason or f"pre_tool_use hook blocked {tool_name}",
                is_error=True,
            )

    tool = context.tool_registry.get(tool_name)
    if tool is None:
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=f"Unknown tool: {tool_name}",
            is_error=True,
        )

    try:
        parsed_input = tool.input_model.model_validate(tool_input)
    except Exception as exc:
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=f"Invalid input for {tool_name}: {exc}",
            is_error=True,
        )

    result = await tool.execute(
        parsed_input,
        ToolExecutionContext(
            cwd=context.cwd,
            metadata={
                "tool_registry": context.tool_registry,
                **(context.tool_metadata or {}),
            },
        ),
    )
    tool_result = ToolResultBlock(
        tool_use_id=tool_use_id,
        content=result.output,
        is_error=result.is_error,
    )
    if context.hook_executor is not None:
        await context.hook_executor.execute(
            HookEvent.POST_TOOL_USE,
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": tool_result.content,
                "tool_is_error": tool_result.is_error,
                "event": HookEvent.POST_TOOL_USE.value,
            },
        )
    return tool_result
