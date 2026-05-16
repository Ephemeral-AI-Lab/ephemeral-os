"""End-to-end retry coverage through a real query loop + fake provider.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §1a (fake-provider
infra) and §1b rows 1-7.

These tests exercise the full provider stream → tool dispatch → budget
check → transcript fix → retry path. Lifecycle-only tests live in
``test_lifecycle.py``; this file is the only one that hits real
:func:`run_query` for retry scenarios.

Why pre-seed ``tool_calls_used``? With a non-empty ``terminal_tools``
set, the reserved-slot rule in ``_consume_tool_budget_or_reject`` blocks
non-terminal calls at ``tool_calls_used == tool_call_limit - 1``, so
non-terminal calls alone cannot push the counter past the limit. The
RESOURCE_LIMIT post-dispatch branch fires when the counter is already at
the limit *before* the next batch arrives — modelled here by
pre-seeding ``tool_calls_used = tool_call_limit`` to represent an
attempt that exhausted its budget on the prior turn.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.agent.lifecycle import run_ephemeral_agent
from message.messages import (
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from notification import make_budget_warning
from providers.types import UsageSnapshot

from tests.unit_test.test_engine._fake_provider import (
    FakeProviderClient,
    ScriptedToolUse,
    ScriptedTurn,
)
from tests.unit_test.test_engine._retry_test_support import (
    build_real_loop_agent,
    install_scripted_agent,
)


# ---------------------------------------------------------------------------
# R1 smoke test — proves the fake provider actually drives the real loop.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_provider_drives_real_loop_with_terminal_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-turn scripted terminal call should exit via TOOL_STOP."""
    client = FakeProviderClient(
        turns=[
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(id="tu_1", name="submit_x", input={"payload": "ok"}),
                ),
                usage=UsageSnapshot(input_tokens=5, output_tokens=3),
            ),
        ]
    )
    agent = build_real_loop_agent(client, tool_call_limit=5)
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "hello")

    assert result.status == "completed"
    assert result.terminal_result is not None
    assert result.terminal_result.output == "ok"
    assert client.remaining_turns == 0
    # One provider call total — no retry needed when the first turn
    # terminates cleanly.
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# R2 — plan §1b end-to-end coverage matrix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_provider_stream_resource_limit_then_terminal_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RESOURCE_LIMIT on Turn 1 → retry → Turn 2 terminal delivers result."""
    client = FakeProviderClient(
        turns=[
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(
                        id="tu_1", name="read_file_stub", input={"path": "a.txt"}
                    ),
                ),
                usage=UsageSnapshot(input_tokens=4, output_tokens=2),
            ),
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(
                        id="tu_2", name="submit_x", input={"payload": "delivered"}
                    ),
                ),
                usage=UsageSnapshot(input_tokens=3, output_tokens=2),
            ),
        ]
    )
    # Pre-seed used==limit so a non-terminal call hits the budget guard.
    agent = build_real_loop_agent(
        client,
        tool_call_limit=1,
        tool_calls_used=1,
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "do the thing")

    assert result.status == "completed"
    assert result.terminal_result is not None
    assert result.terminal_result.output == "delivered"
    # Both turns ran (Turn 1 plus the retry).
    assert len(client.calls) == 2

    # Transcript invariant: after Turn 1 the assistant tool_use must be
    # paired with a ToolResultBlock (the loop's RESOURCE_LIMIT branch
    # appends one even when the call was rejected by the budget guard).
    assistant_msg_idx = next(
        i for i, m in enumerate(agent.messages)
        if m.role == "assistant" and any(
            isinstance(b, ToolUseBlock) and b.id == "tu_1" for b in m.content
        )
    )
    paired_user = agent.messages[assistant_msg_idx + 1]
    assert paired_user.role == "user"
    pair_ids = [
        b.tool_use_id for b in paired_user.content if isinstance(b, ToolResultBlock)
    ]
    assert "tu_1" in pair_ids


@pytest.mark.asyncio
async def test_full_provider_stream_text_response_then_terminal_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEXT_RESPONSE on Turn 1 → retry → Turn 2 terminal."""
    client = FakeProviderClient(
        turns=[
            ScriptedTurn(text_deltas=("I am done.",)),
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(id="tu_term", name="submit_x", input={"payload": "fin"}),
                ),
            ),
        ]
    )
    agent = build_real_loop_agent(
        client, tool_call_limit=5, terminal_tools={"submit_x"}
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "go")

    assert result.status == "completed"
    assert result.terminal_result is not None
    assert result.terminal_result.output == "fin"
    assert len(client.calls) == 2

    # Retry transcript: original assistant text reply must still be in
    # the message history before the retry nudge user message.
    text_present = any(
        msg.role == "assistant" and any(
            isinstance(b, TextBlock) and "I am done." in b.text for b in msg.content
        )
        for msg in agent.messages
    )
    assert text_present
    # Last user message before the retry stream call is the nudge — it
    # must mention the terminal tool name.
    second_request = client.calls[1]
    last_user_text = " ".join(
        block.text
        for msg in second_request.messages
        if msg.role == "user"
        for block in msg.content
        if isinstance(block, TextBlock)
    )
    assert "submit_x" in last_user_text
    assert "plain text" in last_user_text


@pytest.mark.asyncio
async def test_provider_stream_terminal_reserved_slot_reapplies_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reserved-slot rule re-arms after retry because tool_calls_used resets.

    Reserved-slot rule (``tools/_framework/execution/tool_call.py:
    _consume_tool_budget_or_reject``): rejects non-terminal calls at
    ``tool_calls_used == limit - 1`` when ``terminal_tools`` is non-empty;
    terminal tools are exempt and may run at the boundary.

    Setup: ``limit=2``, ``terminal_tools={"submit_x"}``.

    - Attempt 1 iter 1: model emits two non-terminals. First increments
      ``used 0→1``; second hits the reserved-slot boundary and is rejected
      (rejection ToolResultBlock with the canonical wording lands in the
      transcript). Post-dispatch: ``used=1<2`` so loop continues.
    - Attempt 1 iter 2: model gives up via plain text → TEXT_RESPONSE.
    - Retry resets ``tool_calls_used`` to 0 → reserved-slot rule re-arms.
    - Attempt 2 iter 1: one non-terminal pushes ``used 0→1``.
    - Attempt 2 iter 2: ``submit_x`` is called at the boundary
      (``used == limit - 1``); the terminal-exemption short-circuit
      allows it (a non-terminal would have been rejected here).
      Terminal succeeds → TOOL_STOP.
    """
    client = FakeProviderClient(
        turns=[
            # Attempt 1 iter 1: two non-terminals — second hits reserved-slot.
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(
                        id="tu_1a", name="read_file_stub", input={"path": "a.txt"}
                    ),
                    ScriptedToolUse(
                        id="tu_1b", name="read_file_stub", input={"path": "b.txt"}
                    ),
                ),
            ),
            # Attempt 1 iter 2: model gives up.
            ScriptedTurn(text_deltas=("nothing more to do",)),
            # Attempt 2 iter 1: burn budget back to the boundary.
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(
                        id="tu_2a", name="read_file_stub", input={"path": "c.txt"}
                    ),
                ),
            ),
            # Attempt 2 iter 2: terminal-at-boundary, exempt from reserved-slot.
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(
                        id="tu_term", name="submit_x", input={"payload": "ok"}
                    ),
                ),
            ),
        ]
    )
    agent = build_real_loop_agent(
        client, tool_call_limit=2, terminal_tools={"submit_x"}
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "p")

    assert result.status == "completed"
    assert result.terminal_result is not None
    # Four provider calls — both attempts iterate twice.
    assert len(client.calls) == 4
    # Final terminal consumed one tool call from the fresh-on-retry budget,
    # leaving the counter at the boundary (limit-1) plus one terminal.
    assert agent.query_context.tool_calls_used == 2

    # The reserved-slot rejection for tu_1b must appear in the transcript
    # with the canonical wording from ``_build_terminal_budget_reserved_error``.
    rejection_results = [
        block
        for msg in agent.messages
        for block in msg.content
        if isinstance(block, ToolResultBlock) and block.tool_use_id == "tu_1b"
    ]
    assert rejection_results, "tu_1b must have been dispatched (rejected) and paired"
    assert rejection_results[0].is_error is True
    assert "reserved for terminal submission" in str(rejection_results[0].content)

    # The terminal-at-boundary call on Attempt 2 must NOT be rejected: a
    # successful terminal_result on the run proves the terminal-exemption
    # fired (a reserved-slot rejection of ``submit_x`` would have left
    # ``result.terminal_result`` at None). The dispatch branch returns
    # before appending tool_results to messages on TOOL_STOP, so we check
    # the engine's terminal output directly rather than the transcript.
    assert result.terminal_result is not None
    assert result.terminal_result.does_terminate is True
    assert result.terminal_result.is_error is False
    assert result.terminal_result.output == "ok"


@pytest.mark.asyncio
async def test_provider_stream_orphan_tool_uses_paired_on_resource_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every assistant tool_use must have a matching ToolResultBlock after
    RESOURCE_LIMIT.

    Pre-seed ``tool_calls_used = tool_call_limit`` so the streaming budget
    check rejects the assistant's non-terminal tool_use with a
    :class:`ToolResultBlock`. The dispatch branch then appends the result
    set to the transcript so the next provider call (or any caller
    reading ``agent.messages``) sees no orphan tool_uses.
    """
    client = FakeProviderClient(
        turns=[
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(
                        id="tu_a", name="read_file_stub", input={"path": "a"}
                    ),
                    ScriptedToolUse(
                        id="tu_b", name="read_file_stub", input={"path": "b"}
                    ),
                ),
            ),
            # Retry succeeds so the run terminates; the assertion below
            # is about the transcript state after Turn 1.
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(id="tu_term", name="submit_x"),
                ),
            ),
        ]
    )
    agent = build_real_loop_agent(
        client,
        tool_call_limit=1,
        tool_calls_used=1,
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    # Locate the assistant message that emitted tu_a + tu_b.
    assistant_idx = next(
        i for i, m in enumerate(agent.messages)
        if m.role == "assistant"
        and {b.id for b in m.content if isinstance(b, ToolUseBlock)} == {"tu_a", "tu_b"}
    )
    paired = agent.messages[assistant_idx + 1]
    assert paired.role == "user"
    pair_ids = {
        b.tool_use_id for b in paired.content if isinstance(b, ToolResultBlock)
    }
    assert pair_ids == {"tu_a", "tu_b"}


@pytest.mark.asyncio
async def test_provider_stream_does_not_retry_when_terminal_tools_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without terminal_tools the retry path is disabled."""
    client = FakeProviderClient(
        turns=[
            ScriptedTurn(text_deltas=("just chatting",)),
        ]
    )
    # ``include_terminal_tool=False`` keeps ``submit_x`` out of the
    # registry so loop-start auto-population finds no terminal tools,
    # and ``terminal_tools=set()`` keeps the override explicit at
    # construction time.
    agent = build_real_loop_agent(
        client,
        tool_call_limit=3,
        terminal_tools=set(),
        include_terminal_tool=False,
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "p")

    assert result.status == "completed"
    assert result.terminal_result is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_provider_stream_budget_warning_notification_re_fires_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """budget_warning fires on EVERY attempt because state clears on retry.

    Setup:
    - ``tool_call_limit=2`` with ``terminal_tools={"submit_x"}``.
    - Pre-seed ``tool_calls_used=1`` so Attempt 1 iteration 1 enters the
      loop already at fraction 0.5 → rule fires on the first dispatch.
    - Attempt 1: model exits via TEXT_RESPONSE after the warning lands.
    - Retry resets ``tool_calls_used`` to 0 AND pops the
      ``budget_warning`` bookkeeping key.
    - Attempt 2 iter 1: rules dispatch with used=0 → does NOT fire.
      Model emits one non-terminal to push used back to 1. Iter 2: rules
      dispatch with used=1 → fires AGAIN from the cleared state. Model
      then emits ``submit_x`` to terminate cleanly.

    The body text of ``make_budget_warning`` includes
    ``"Tool-call budget at"`` — count occurrences in the final transcript
    to prove the rule fired exactly twice (once per attempt).
    """
    from message.messages import SystemNotificationBlock

    rule = make_budget_warning(thresholds=(0.5,))
    client = FakeProviderClient(
        turns=[
            # Attempt 1 iter 1: model gives up after seeing the warning.
            ScriptedTurn(text_deltas=("nothing useful to do",)),
            # Attempt 2 iter 1: burn a non-terminal to push budget to 50%.
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(
                        id="tu_burn", name="read_file_stub", input={"path": "x"}
                    ),
                ),
            ),
            # Attempt 2 iter 2: terminal succeeds (terminal-at-boundary).
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(id="tu_term", name="submit_x"),
                ),
            ),
        ]
    )
    agent = build_real_loop_agent(
        client,
        tool_call_limit=2,
        tool_calls_used=1,
        terminal_tools={"submit_x"},
        notification_rules=[rule],
    )
    install_scripted_agent(monkeypatch, agent)

    result = await run_ephemeral_agent(SimpleNamespace(), "p")

    assert result.status == "completed"
    assert result.terminal_result is not None
    assert len(client.calls) == 3

    # Count SystemNotificationBlocks anywhere in the final transcript.
    notification_blocks = [
        block
        for msg in agent.messages
        for block in msg.content
        if isinstance(block, SystemNotificationBlock)
    ]
    budget_warnings = [
        block for block in notification_blocks if "Tool-call budget at" in block.text
    ]
    # Two firings: one per attempt. If state had leaked, attempt 2 would
    # not have re-fired and this count would be 1.
    assert len(budget_warnings) == 2, (
        f"Expected budget_warning to fire on each attempt (2 total); got "
        f"{len(budget_warnings)} firings. State leaked across retry."
    )

    # Cross-check: the warning visible in the SECOND attempt's first
    # provider request must be the Attempt-1 firing only; the Attempt-2
    # firing is appended at the top of iteration 2 and is visible in
    # client.calls[2].messages.
    def _count_in(req_messages: list) -> int:
        return sum(
            1
            for msg in req_messages
            for block in msg.content
            if isinstance(block, SystemNotificationBlock)
            and "Tool-call budget at" in block.text
        )

    assert _count_in(client.calls[0].messages) == 1  # attempt 1 iter 1 fire
    assert _count_in(client.calls[1].messages) == 1  # attempt 2 iter 1 — only old block
    assert _count_in(client.calls[2].messages) == 2  # attempt 2 iter 2 — new block too


@pytest.mark.asyncio
async def test_provider_stream_assistant_tool_uses_preserved_across_retry_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original assistant tool_use message must remain in the transcript on retry."""
    client = FakeProviderClient(
        turns=[
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(
                        id="tu_orig", name="read_file_stub", input={"path": "k.txt"}
                    ),
                ),
            ),
            ScriptedTurn(
                tool_uses=(
                    ScriptedToolUse(id="tu_term", name="submit_x", input={"payload": "v"}),
                ),
            ),
        ]
    )
    agent = build_real_loop_agent(
        client,
        tool_call_limit=1,
        tool_calls_used=1,
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    preserved = any(
        msg.role == "assistant"
        and any(isinstance(b, ToolUseBlock) and b.id == "tu_orig" for b in msg.content)
        for msg in agent.messages
    )
    assert preserved
    # The retry stream call's request must include the preserved
    # assistant tool_use in its prior conversation.
    second_request = client.calls[1]
    preserved_in_request = any(
        msg.role == "assistant"
        and any(isinstance(b, ToolUseBlock) and b.id == "tu_orig" for b in msg.content)
        for msg in second_request.messages
    )
    assert preserved_in_request
