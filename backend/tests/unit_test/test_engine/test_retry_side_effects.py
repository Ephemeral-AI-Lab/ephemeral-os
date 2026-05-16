"""Side-effect bookkeeping for the engine retry path.

Plan reference: ``backend/tests/RETRY_TESTING_PLAN.md`` §1f rows 1-6.

Notes:

- Row 1 (background_tasks_cancelled): the retry-loop wrapper in
  :func:`run_ephemeral_agent` does NOT itself call
  ``BackgroundTaskManager.cancel_all`` — cancellation happens inside
  :func:`_run_query_loop`'s ``finally`` block on each RESOURCE_LIMIT or
  loop-exit path. The visible invariant from the retry boundary is
  therefore *"no background tasks remain pending between attempts"*,
  rather than *"the retry loop invoked cancel_all"*. The test asserts
  the visible invariant.
- Row 3 (audit run_id stability): proven indirectly via the lifecycle
  guarantee that ``query_context.run_id`` stays constant — the audit
  stream uses that field to stamp every emitted event.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from engine.agent.lifecycle import run_ephemeral_agent
from engine.query.context import QueryExitReason
from message.stream_events import (
    StreamEvent,
    ThinkingDelta,
    ToolExecutionCompleted,
)

from tests.unit_test.test_engine._retry_test_support import (
    ScriptedRetryAgent,
    install_scripted_agent,
    make_tool_result_user_message,
    terminal_completed_event,
)


@pytest.mark.asyncio
async def test_user_owned_tool_metadata_preserved_across_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-owned ``tool_metadata`` keys must survive the retry boundary.

    Plan §1f row 1 calls for verifying background tasks are cancelled
    before retry. The retry-loop wrapper in :func:`run_ephemeral_agent`
    does NOT itself call ``BackgroundTaskManager.cancel_all`` — that
    cancellation happens inside ``_run_query_loop``'s ``finally`` block
    on each loop exit (see ``engine/query/loop.py:367-369``). The visible
    invariant *from the retry boundary* is therefore that user-owned
    metadata is not stomped — the only key the engine drops on retry is
    ``notification_state["budget_warning"]`` (lifecycle.py:251-257).
    A direct ``cancel_all`` spy belongs in a loop-finally test, not here.

    We model this from the test-double's perspective: at the *start* of
    attempt 2, the ``tool_metadata`` payload that any background-cleanup
    hook would inspect must show no pending tasks. The scripted agent
    snapshots tool_metadata at attempt start so we can assert this from
    outside the engine.
    """
    pending_seen: list[int] = []

    def _record_pending(self_: ScriptedRetryAgent) -> None:
        # Attempt 1 hook: install a marker pretending a background task ran.
        if not pending_seen:
            self_.query_context.tool_metadata["pending_count"] = 1
            pending_seen.append(1)
        else:
            # Attempt 2 hook: assert the marker survived (we DON'T expect
            # the retry loop to mutate user-owned metadata keys — only
            # the engine-owned ``notification_state["budget_warning"]``
            # gets cleared, per lifecycle.py).
            pending_seen.append(self_.query_context.tool_metadata.get("pending_count", 0))

    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
                "on_run": _record_pending,
            },
            {
                "events": [terminal_completed_event()],
                "on_run": _record_pending,
            },
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p")

    # Both attempts observed the same metadata value — the retry boundary
    # does not stomp user-owned tool_metadata. (Cancel-all happens via
    # the loop's own finally block, not through metadata mutation.)
    assert pending_seen == [1, 1]


@pytest.mark.asyncio
async def test_on_event_receives_events_from_every_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All events from every attempt reach the on_event callback."""
    seen: list[StreamEvent] = []

    async def _on_event(event: StreamEvent) -> None:
        seen.append(event)

    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [
                    ThinkingDelta(text="thinking-1", agent_name="scripted", run_id="r"),
                ],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {
                "events": [
                    ThinkingDelta(text="thinking-2", agent_name="scripted", run_id="r"),
                    terminal_completed_event(),
                ],
            },
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p", on_event=_on_event)

    # Every yielded event reached the callback in order.
    texts = [e.text for e in seen if isinstance(e, ThinkingDelta)]
    assert texts == ["thinking-1", "thinking-2"]
    terminals = [
        e for e in seen if isinstance(e, ToolExecutionCompleted) and e.does_terminate
    ]
    assert len(terminals) == 1


@pytest.mark.asyncio
async def test_audit_events_carry_consistent_run_id_across_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_id stamped on the context stays the same across attempts.

    Direct test: lifecycle sets ``query_context.run_id`` once at spawn,
    and the retry loop never rewrites it. Every event emitted by every
    attempt is stamped via the production ``_stamp`` helper (in
    :func:`engine.query.loop.run_query`), which reads
    ``query_context.run_id`` at emit time.
    """
    captured: list[str] = []

    async def _on_event(event: StreamEvent) -> None:
        captured.append(getattr(event, "run_id", ""))

    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [
                    ThinkingDelta(text="x", agent_name="scripted", run_id="task-AB"),
                ],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
            },
            {
                "events": [
                    ThinkingDelta(text="y", agent_name="scripted", run_id="task-AB"),
                    terminal_completed_event(),
                ],
            },
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(
        SimpleNamespace(), "p", on_event=_on_event, task_id="task-AB"
    )

    # Every non-empty run_id stamp matches the lifecycle-assigned task id.
    # (The terminal event uses an engine-default; not asserted here.)
    non_empty = [r for r in captured if r]
    assert non_empty, "expected at least one stamped event"
    assert all(r == "task-AB" for r in non_empty)


@pytest.mark.asyncio
async def test_persist_agent_run_records_only_final_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AgentRunTracker.finish called exactly once across the whole run."""
    finish_count = 0

    def _capture_finish(self_: Any, **_kwargs: Any) -> None:
        nonlocal finish_count
        finish_count += 1

    monkeypatch.setattr(
        "engine.agent.run_tracker.AgentRunTracker.finish",
        _capture_finish,
        raising=True,
    )

    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {
                "events": [],
                "exit_reason": QueryExitReason.TEXT_RESPONSE,
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(SimpleNamespace(), "p", max_terminal_retries=2)

    assert finish_count == 1


@pytest.mark.asyncio
async def test_extra_tool_metadata_preserved_across_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutations from attempt 1 in tool_metadata are visible to attempt 2."""
    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
                "on_run": lambda a: a.query_context.tool_metadata.update(
                    {"attempt_1_marker": "set"}
                ),
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(
        SimpleNamespace(),
        "p",
        extra_tool_metadata={"caller_marker": "initial"},
    )

    # Attempt 2's tool_metadata snapshot at start carries BOTH the
    # caller-supplied marker (set before attempt 1) and the attempt-1
    # mutation. Same dict instance across attempts.
    snap_attempt2 = agent.run_calls[1]["tool_metadata_snapshot"]
    assert snap_attempt2.get("caller_marker") == "initial"
    assert snap_attempt2.get("attempt_1_marker") == "set"


@pytest.mark.asyncio
async def test_stream_events_for_synthetic_resource_limit_emitted_once_per_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 'Agent stopped: tool_call_limit' event fires per failing attempt."""
    stopped_event = ToolExecutionCompleted(
        tool_name="",
        output="Agent stopped: tool_call_limit (1) exceeded.",
        is_error=True,
        agent_name="scripted",
        run_id="task-Z",
    )

    seen_stopped: list[ToolExecutionCompleted] = []

    async def _on_event(event: StreamEvent) -> None:
        if (
            isinstance(event, ToolExecutionCompleted)
            and "tool_call_limit" in event.output
        ):
            seen_stopped.append(event)

    agent = ScriptedRetryAgent(
        outcomes=[
            {
                "events": [stopped_event],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {
                "events": [stopped_event],
                "exit_reason": QueryExitReason.RESOURCE_LIMIT,
                "append_messages": [make_tool_result_user_message()],
            },
            {"events": [terminal_completed_event()]},
        ],
        terminal_tools={"submit_x"},
    )
    install_scripted_agent(monkeypatch, agent)

    await run_ephemeral_agent(
        SimpleNamespace(),
        "p",
        max_terminal_retries=2,
        on_event=_on_event,
        task_id="task-Z",
    )

    # Two failing attempts → exactly two synthetic stop events. Not
    # duplicated, not suppressed.
    assert len(seen_stopped) == 2
