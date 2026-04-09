# ruff: noqa
"""Live E2E verification for eval-harness persistence parity.

These tests exercise the live EvalAgent path without Daytona. They verify that
the shared test harness persists:
1. top-level + subagent token usage keyed by run id
2. compacted history/session state when auto-compaction fires
"""

from __future__ import annotations

import pytest

from compaction import estimate_message_tokens, get_autocompact_threshold
from engine.testing.eval_agent import EvalAgent
from providers.types import ApiMessageCompleteEvent
from tests.test_e2e.conftest import (
    create_eval_agent,
    get_eval_persistence,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

ALLOWED_SUBAGENT_TOOLS = {
    "run_subagent",
    "check_background_progress",
    "wait_for_background_task",
    "cancel_background_task",
}

SUBAGENT_SYSTEM_PROMPT = (
    "Use only subagent background tools. Check progress before waiting. "
    "Ignore sandbox tools. Keep replies brief."
)
DETAIL_REPEAT = 32


def _message_text(message: dict) -> str:
    parts: list[str] = []
    for block in message.get("content", []) or []:
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _context_tokens_from_messages(messages) -> int:
    return estimate_message_tokens(list(messages or []))


def _assert_context_window(agent, messages, *, upper_bound: int | None = None) -> int:
    tokens = _context_tokens_from_messages(messages)
    limit = upper_bound or get_autocompact_threshold(agent.model)
    assert 0 < tokens < limit, {"context_tokens": tokens, "upper_bound": limit}
    return tokens


class _UsageCapturingClient:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.usage = None

    async def stream_message(self, request):
        async for event in self._inner.stream_message(request):
            if isinstance(event, ApiMessageCompleteEvent):
                self.usage = event.usage
            yield event


@pytest.mark.skipif(not EvalAgent.has_credentials(), reason="Live API credentials required")
@pytest.mark.asyncio
async def test_live_eval_persists_run_linked_subagent_usage():
    agent = create_eval_agent(
        system_prompt=SUBAGENT_SYSTEM_PROMPT,
        tool_call_limit=60,
    )

    result = await agent.invoke(
        "Launch one subagent that returns exactly two lines: SUBAGENT_TOKEN_OK on "
        "line 1 and a 12-word sentence about persistence on line 2. Check its "
        "progress once before waiting for it, then reply with only those two lines."
    )

    persisted = get_eval_persistence(agent)
    total_usage = agent._e2e_total_usage

    assert set(result.tool_names).issubset(ALLOWED_SUBAGENT_TOOLS), result.tool_names
    assert result.tool_count("run_subagent") == 1, result.tool_names
    assert result.has_tool("run_subagent"), f"Expected run_subagent in tool flow: {result.tool_names}"
    assert result.has_tool("check_background_progress"), (
        f"Expected check_background_progress in tool flow: {result.tool_names}"
    )
    assert result.has_tool("wait_for_background_task"), (
        f"Expected wait_for_background_task in tool flow: {result.tool_names}"
    )
    assert result.system_notifications(), "Expected background system notifications"
    assert "subagent_token_ok" in result.text.lower(), result.text

    assert persisted["run_id"], "Expected a persisted top-level run id"
    assert persisted["run_usage"] is not None, "Expected top-level run usage"
    assert persisted["run_usage"]["total_tokens"] > 0, persisted["run_usage"]
    assert total_usage is not None and total_usage.total_tokens > 0, total_usage
    assert persisted["run_usage"]["total_tokens"] == total_usage.total_tokens, (
        persisted["run_usage"],
        total_usage,
    )

    subagent_runs = persisted["subagent_runs"]
    assert len(subagent_runs) == 1, subagent_runs
    assert subagent_runs[0]["status"] == "completed", subagent_runs
    assert subagent_runs[0].get("usage"), subagent_runs
    assert subagent_runs[0]["usage"]["total_tokens"] > 0, subagent_runs

    session_usage = persisted["session_usage"]
    assert session_usage is not None, "Expected aggregate session usage"
    child_total = subagent_runs[0]["usage"]["total_tokens"]
    assert session_usage["total_tokens"] >= persisted["run_usage"]["total_tokens"] + child_total
    _assert_context_window(agent, agent._query_context.api_messages_snapshot, upper_bound=5_000)


@pytest.mark.skipif(not EvalAgent.has_credentials(), reason="Live API credentials required")
@pytest.mark.asyncio
async def test_live_eval_persists_compaction_artifacts(override_compaction_threshold):
    override_compaction_threshold(200)

    agent = create_eval_agent(
        system_prompt="You are a concise assistant.",
        tool_call_limit=20,
    )

    from compaction import SessionState, compact_for_api
    from compaction import get_autocompact_threshold as compact_threshold
    from message import ConversationMessage, TextBlock

    client = _UsageCapturingClient(agent.api_client)
    messages = [
        ConversationMessage(role="user", content=[TextBlock(text="User request: design a data platform plan.")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="Assistant notes on architecture and trade-offs. " * DETAIL_REPEAT)]),
        ConversationMessage(role="user", content=[TextBlock(text="Add more detail on storage, indexing, and retention. " * DETAIL_REPEAT)]),
        ConversationMessage(role="assistant", content=[TextBlock(text="Detailed response about storage tiers and operational concerns. " * DETAIL_REPEAT)]),
        ConversationMessage(role="user", content=[TextBlock(text="Now cover observability and incident response. " * DETAIL_REPEAT)]),
        ConversationMessage(role="assistant", content=[TextBlock(text="Observability guidance with metrics, logs, traces, and alerting. " * DETAIL_REPEAT)]),
        ConversationMessage(role="user", content=[TextBlock(text="Finish with deployment sequencing and risks. " * DETAIL_REPEAT)]),
        ConversationMessage(role="assistant", content=[TextBlock(text="Sequencing advice plus rollout risks and mitigations. " * DETAIL_REPEAT)]),
    ]
    state = SessionState()
    threshold = compact_threshold(agent.model)
    before_tokens = _context_tokens_from_messages(messages)

    compacted = await compact_for_api(
        messages,
        api_client=client,
        model=agent.model,
        system_prompt=agent.settings.system_prompt,
        state=state,
        preserve_recent=2,
    )
    after_tokens = _context_tokens_from_messages(compacted)

    assert state.compacted is True, state
    assert before_tokens > threshold, {"before_tokens": before_tokens, "threshold": threshold}
    assert client.usage is not None and client.usage.total_tokens > 0, client.usage
    assert len(compacted) < len(messages), (
        f"Compacted history should be shorter than original. compacted={len(compacted)} "
        f"original={len(messages)}"
    )
    assert after_tokens < before_tokens, {
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
    }
    assert any(
        "This session is being continued from a previous conversation" in _message_text(msg.model_dump(mode="json"))
        for msg in compacted
    ), [msg.model_dump(mode="json") for msg in compacted]
    _assert_context_window(agent, compacted, upper_bound=before_tokens)
