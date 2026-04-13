"""Core types and shared LLM call helper for ephemeral tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class EphemeralTaskResult:
    """Result of a single-shot EphemeralTask LLM call."""

    text: str
    timed_out: bool = False
    tool_input: dict[str, Any] | None = None


@dataclass
class Snapshot:
    """Frozen point-in-time view of an agent's conversation."""

    task_id: str
    agent_run_id: str
    messages: list[dict]
    system_prompt: str

    async def ask(
        self,
        prompt: str,
        *,
        api_client: Any,
        max_tokens: int = 500,
        model: str | None = None,
    ) -> EphemeralTaskResult:
        """Append a question and get a one-shot LLM answer (free text)."""
        full_messages = list(self.messages) + [{"role": "user", "content": prompt}]
        return await call_llm(
            messages=full_messages,
            system_prompt=self.system_prompt,
            api_client=api_client,
            max_tokens=max_tokens,
            model=model,
        )

    async def ask_tool(
        self,
        prompt: str,
        *,
        tool: dict[str, Any],
        api_client: Any,
        max_tokens: int = 500,
        model: str | None = None,
    ) -> EphemeralTaskResult:
        """Append a question and force a single tool call response.

        Uses tool_choice="any" so the model MUST call a tool.
        Single turn — no timeout wrapper needed.
        """
        full_messages = list(self.messages) + [{"role": "user", "content": prompt}]
        return await call_llm_tool(
            messages=full_messages,
            system_prompt=self.system_prompt,
            tool=tool,
            api_client=api_client,
            max_tokens=max_tokens,
            model=model,
        )


async def call_llm(
    *,
    messages: list[dict],
    system_prompt: str,
    api_client: Any,
    max_tokens: int = 500,
    model: str | None = None,
) -> EphemeralTaskResult:
    """Single-turn LLM call. Free-text response."""
    try:
        response = await api_client.create_message(
            model=model or "claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
    except Exception:
        return EphemeralTaskResult(text="", timed_out=False)

    text = (response.content[0].text if response.content else "").strip()
    return EphemeralTaskResult(text=text, timed_out=False)


async def call_llm_tool(
    *,
    messages: list[dict],
    system_prompt: str,
    tool: dict[str, Any],
    api_client: Any,
    max_tokens: int = 500,
    model: str | None = None,
) -> EphemeralTaskResult:
    """Single-turn forced tool call. tool_choice='any' guarantees the model
    calls the tool in its first response."""
    try:
        response = await api_client.create_message(
            model=model or "claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            tools=[tool],
            tool_choice={"type": "any"},
        )
    except Exception:
        return EphemeralTaskResult(text="", timed_out=False)

    # Extract tool_use block from response
    tool_input: dict[str, Any] | None = None
    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            tool_input = block.input
            text = json.dumps(block.input)
            break
        elif getattr(block, "text", None):
            text = block.text.strip()

    return EphemeralTaskResult(text=text, timed_out=False, tool_input=tool_input)
