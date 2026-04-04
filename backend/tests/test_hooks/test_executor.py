"""Tests for hooks execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from ephemeralos.api.client import ApiMessageCompleteEvent
from ephemeralos.api.usage import UsageSnapshot
from ephemeralos.engine.messages import ConversationMessage, TextBlock
from ephemeralos.hooks import HookEvent, HookExecutionContext, HookExecutor
from ephemeralos.hooks.loader import HookRegistry
from ephemeralos.hooks.schemas import CommandHookDefinition, PromptHookDefinition


class FakeApiClient:
    """Minimal fake streaming client."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


@pytest.mark.asyncio
async def test_command_hook_executes(tmp_path: Path):
    registry = HookRegistry()
    registry.register(
        HookEvent.SESSION_START,
        CommandHookDefinition(command="printf 'booted'"),
    )
    executor = HookExecutor(
        registry,
        HookExecutionContext(cwd=tmp_path, api_client=FakeApiClient('{"ok": true}'), default_model="claude-test"),
    )

    result = await executor.execute(HookEvent.SESSION_START, {"event": "session_start"})

    assert result.blocked is False
    assert result.results[0].output == "booted"


@pytest.mark.asyncio
async def test_prompt_hook_can_block(tmp_path: Path):
    registry = HookRegistry()
    registry.register(
        HookEvent.PRE_TOOL_USE,
        PromptHookDefinition(prompt="Check tool call", matcher="bash"),
    )
    executor = HookExecutor(
        registry,
        HookExecutionContext(
            cwd=tmp_path,
            api_client=FakeApiClient('{"ok": false, "reason": "blocked by policy"}'),
            default_model="claude-test",
        ),
    )

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "bash", "tool_input": {"command": "rm -rf ."}},
    )

    assert result.blocked is True
    assert result.reason == "blocked by policy"
