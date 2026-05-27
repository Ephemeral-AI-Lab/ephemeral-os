"""Focused negative-path: wrong-tool approval blocks via dispatch path.

``MockSquadRunner._approve_terminal`` synthesizes an ``ask_advisor`` approval
pair targeting a specific terminal. If a test (or a future divergence)
threads an approval pair for the *wrong* terminal, ``AdvisorApprovalPreHook``
must still fire when dispatch reaches the gated terminal.

This is the focused-test variant of testing-plan §3.3.1 (4): rather than
driving a full scenario — which would require ``MockSquadRunner._call_tool``
to grow a "gate-block is non-fatal" code path because today it raises on
``is_error=True`` — we hit the same dispatch surface (``execute_tool_once``
+ ``ToolExecutionContextService``) the runner uses, with wrong-tool metadata
produced by the same ``_approve_terminal`` helper used by every happy-path
scenario.

A reviewer tempted to "fix" this by adding scenario plumbing should not:
the focused shape was chosen deliberately to keep the runner's failure
semantics simple.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from task_center_runner.agent.mock.runner import MockSquadRunner
from task_center_runner.audit.bus import AuditEventBus
from task_center_runner.hooks.registry import MutableMockState
from task_center_runner.scenarios.correctness_testing import CorrectnessTesting
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.runtime import ExecutionMetadata
from tools._framework.execution.tool_call import execute_tool_once
from tools.submission.executor.submit_execution_blocker import (
    submit_execution_blocker,
)
from tools.submission.executor.submit_execution_success import (
    submit_execution_success,
)


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


def _runner() -> MockSquadRunner:
    return MockSquadRunner(
        repo_dir="/tmp/advisor_gate_focused",
        bus=AuditEventBus(),
        scenario=CorrectnessTesting(),
        mutable_state=MutableMockState(),
    )


@pytest.mark.asyncio
async def test_wrong_tool_approval_blocks_terminal_dispatch() -> None:
    """Approve ``submit_execution_success`` → submit ``submit_execution_blocker``.

    The gate must reject with the canonical ``BLOCKED`` prose. Verifies that
    ``_approve_terminal`` produces metadata the gate reads correctly, and
    that the gate's wrong-tool branch fires when the approval names a
    different terminal than the one being submitted.
    """
    runner = _runner()
    base_metadata = ExecutionMetadata()
    gated_metadata = runner._approve_terminal(  # noqa: SLF001 — focused contract
        base_metadata, submit_execution_success
    )
    context = ToolExecutionContextService(
        cwd=Path("/tmp"), services=gated_metadata
    )

    result = await execute_tool_once(
        submit_execution_blocker,
        {"summary": "negative-path probe"},
        context,
        emit=_noop_emit,
        emit_started=False,
    )

    assert result.is_error, f"gate failed to block; result={result!r}"
    assert "BLOCKED" in result.output, (
        f"expected BLOCKED prose in output; got {result.output!r}"
    )
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

    The mock runner today always calls ``_approve_terminal``, so this path
    only fires if a future change drops the shim. Locking it in keeps the
    contract explicit.
    """
    metadata = ExecutionMetadata()  # no conversation_messages
    context = ToolExecutionContextService(
        cwd=Path("/tmp"), services=metadata
    )

    result = await execute_tool_once(
        submit_execution_blocker,
        {"summary": "negative-path probe"},
        context,
        emit=_noop_emit,
        emit_started=False,
    )

    assert result.is_error, f"gate failed to block; result={result!r}"
    assert "BLOCKED" in result.output
    assert _gate_reason_metadata(result).get("reason") == "missing"
