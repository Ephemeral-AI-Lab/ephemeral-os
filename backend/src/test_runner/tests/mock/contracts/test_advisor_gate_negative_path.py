"""Focused negative-path: wrong-tool approval blocks via dispatch path.

The ScenarioLoopRunner path gets real ``ask_advisor`` transcript entries from
the query loop. This focused test uses the same transcript helper to thread an
approval pair for the *wrong* terminal and verifies ``AdvisorApprovalPreHook``
still fires when dispatch reaches the gated terminal.

This is the focused-test variant of testing-plan §3.3.1 (4): rather than
driving a full scenario, we hit the same dispatch surface
(``execute_tool_once`` + ``ToolExecutionContextService``) the runner uses, with
wrong-tool metadata produced by the shared advisor-approval transcript helper.

A reviewer tempted to "fix" this by adding scenario plumbing should not:
the focused shape was chosen deliberately to keep the runner's failure
semantics simple.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from test_runner.agent.mock._advisor_approval import (
    build_advisor_approval_messages,
)
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.runtime import ExecutionMetadata
from tools._framework.execution.tool_call import execute_tool_once
from tools.submission.generator import submit_generator_outcome


async def _noop_emit(_event: Any) -> None:
    return None


def _gate_reason_metadata(result: Any) -> dict[str, Any]:
    """Pull the ``advisor_approval`` pre-hook trace entry's metadata.

    The framework stamps per-hook reasons under ``hook_trace[i].metadata``,
    not directly on ``result.metadata``. This helper isolates the trace
    entry produced by ``AdvisorApprovalPreHook`` so the assertions stay
    readable.
    """
    trace = (result.metadata or {}).get("hook_trace") or []
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        hook_name = str(entry.get("hook_name") or "")
        if hook_name.startswith("advisor_approval:"):
            return dict(entry.get("metadata") or {})
    return {}


def _metadata_with_advisor_approval(
    metadata: ExecutionMetadata,
    *,
    tool_name: str,
) -> ExecutionMetadata:
    gated = metadata.copy()
    existing = list(metadata.get("conversation_messages") or [])
    gated["conversation_messages"] = build_advisor_approval_messages(tool_name=tool_name) + existing
    return gated


@pytest.mark.asyncio
async def test_wrong_tool_approval_blocks_terminal_dispatch() -> None:
    """Approve a different terminal → submit ``submit_generator_outcome``.

    The gate must reject with the canonical ``BLOCKED`` prose. Verifies that
    the transcript helper produces metadata the gate reads correctly, and
    that the gate's wrong-tool branch fires when the approval names a
    different terminal than the one being submitted.
    """
    gated_metadata = _metadata_with_advisor_approval(
        ExecutionMetadata(),
        tool_name="submit_reducer_outcome",
    )
    context = ToolExecutionContextService(cwd=Path("/tmp"), services=gated_metadata)

    result = await execute_tool_once(
        submit_generator_outcome,
        {"status": "failed", "outcome": "negative-path probe"},
        context,
        emit=_noop_emit,
        emit_started=False,
    )

    assert result.is_error, f"gate failed to block; result={result!r}"
    assert "BLOCKED" in result.output, f"expected BLOCKED prose in output; got {result.output!r}"
    # The hook stamps a structured reason on the per-hook trace entry; verify
    # the ops/observability surface fires as designed.
    reason_meta = _gate_reason_metadata(result)
    assert reason_meta.get("policy") == "advisor_approval", (
        f"expected policy=advisor_approval; got {reason_meta!r}"
    )
    assert reason_meta.get("reason") == "wrong_tool", (
        f"expected reason=wrong_tool; got {reason_meta!r}"
    )


@pytest.mark.asyncio
async def test_no_approval_blocks_terminal_dispatch() -> None:
    """Empty ``conversation_messages`` → gate fails with ``missing`` reason.

    ScenarioLoopRunner terminal submissions should be preceded by a real
    ``ask_advisor`` call. Locking in the missing-approval branch keeps the
    contract explicit for direct dispatch.
    """
    metadata = ExecutionMetadata()  # no conversation_messages
    context = ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)

    result = await execute_tool_once(
        submit_generator_outcome,
        {"status": "failed", "outcome": "negative-path probe"},
        context,
        emit=_noop_emit,
        emit_started=False,
    )

    assert result.is_error, f"gate failed to block; result={result!r}"
    assert "BLOCKED" in result.output
    assert _gate_reason_metadata(result).get("reason") == "missing"
