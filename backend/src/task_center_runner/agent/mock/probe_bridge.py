"""Queue-bridge: run an imperative ``call_tool``-based probe through the REAL loop.

The heavy probe modules (``high_concurrency_probe``, ``heavy_io_zoned_probe``,
``complex_project_build_probe``, …) were written for the old ``MockSquadRunner``:
they accept an injected ``call_tool(tool_obj, raw_input, metadata, emit, *,
allow_error=...)`` and call it many times deep in their bodies. To run those
bodies through the real ``query.py`` loop WITHOUT rewriting them as async
generators, this module injects a *bridging* ``call_tool`` that hands each call
to the driving role ``TurnScript`` — one :class:`Turn` per call — so the
``ScenarioEventSource`` emits it as a scripted ``tool_use`` and the **real loop
dispatches it**. The bridge changes nothing about how tools execute; it only
adapts an imperative body into the scripted event stream. Mock vs. real still
differ *only* in the event source.

This is the "two-level coroutine bridge": the probe runs as a concurrent task;
:func:`bridge_turns` pulls each tool request off a queue and ``yield``s a
``Turn`` at the top level of the role ``TurnScript`` (Python forbids hiding an
async-generator yield inside a helper), resolving the probe's awaited future
with the loop-normalized :class:`~tools.ToolResult`.

Budget: a single agent is capped at its ``tool_call_limit`` (executor=75, hard
ceiling 1.5×). Heavy probes exceed that, so the scenario planner fans the work
out into a generator DAG (see [[mock_event_source_heavy_probe_fanout_decision]]);
each generator's tool stream is budget-sized and routes through the loop here.
Background dispatch (``background_task_id``) is fire-and-forget through the loop
and cannot satisfy the old probes' blocking-await contract, so the bridge
rejects it — those probes are rewritten to the real-agent background model.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from message.message import ToolResultBlock
from tools._framework.core.results import ToolResult

from task_center_runner.agent.mock.event_source import ToolCall, Turn

# A probe coroutine factory: given the bridging call_tool, returns the probe
# coroutine (which returns the artifact path string).
ProbeFactory = Callable[[Callable[..., Awaitable[ToolResult]]], Awaitable[str]]

_DONE = object()


async def _noop_emit(_event: Any) -> None:
    return None


class _CallToolBridge:
    """Provides the bridging ``call_tool`` + a request queue the driver drains."""

    __slots__ = ("_queue",)

    def __init__(self) -> None:
        # items: ("call", tool_name, raw_input, future) | ("done", None, None, None)
        self._queue: asyncio.Queue[tuple[str, str | None, dict | None, Any]] = (
            asyncio.Queue()
        )

    async def call_tool(
        self,
        tool_obj: Any,
        raw_input: dict[str, Any],
        metadata: Any = None,  # noqa: ARG002 — loop owns tool_metadata
        emit: Any = None,  # noqa: ARG002 — loop owns the event stream
        *,
        allow_error: bool = False,
        background_task_id: str | None = None,
        sandbox_invocation_id: str | None = None,  # noqa: ARG002
        **_kwargs: Any,
    ) -> ToolResult:
        if background_task_id is not None:
            raise NotImplementedError(
                "Background tool dispatch is not expressible through the query "
                "loop bridge (the loop's background path is fire-and-forget). "
                "Background probes must use the real-agent background model "
                "(shell(background=True) + wait_background_tasks / "
                "cancel_background_task). See the heavy-probe fan-out decision."
            )
        fut: asyncio.Future[ToolResult] = asyncio.get_running_loop().create_future()
        await self._queue.put(("call", tool_obj.name, dict(raw_input), fut))
        result = await fut
        # Mirror MockSquadRunner._call_tool: raise unless the caller opted in to
        # tolerate errors (probe bodies rely on this to fail fast).
        if result.is_error and not allow_error:
            raise RuntimeError(f"{tool_obj.name} failed: {result.output}")
        return result


async def bridge_turns(
    factory: ProbeFactory,
    *,
    artifact_out: list[str],
    normalize: Callable[[list[ToolResultBlock]], ToolResult],
) -> AsyncGenerator[Turn, list[ToolResultBlock]]:
    """Drive an imperative probe, yielding one :class:`Turn` per tool call.

    ``factory(call_tool)`` builds the probe coroutine. Each ``await call_tool``
    inside it surfaces here as ``yield Turn(calls=(ToolCall,))``; the value sent
    back (the loop's trailing ``ToolResultBlock``s) is normalized and used to
    resolve the probe's awaited future. The probe's return value (artifact path)
    is appended to *artifact_out*. Probe exceptions propagate to the caller.
    """
    bridge = _CallToolBridge()

    async def _run() -> None:
        try:
            artifact_out.append(await factory(bridge.call_tool))
        finally:
            await bridge._queue.put(("done", None, None, None))  # noqa: SLF001

    probe_task = asyncio.create_task(_run())
    try:
        while True:
            kind, name, raw_input, fut = await bridge._queue.get()  # noqa: SLF001
            if kind == "done":
                break
            blocks = yield Turn(calls=(ToolCall(str(name), dict(raw_input or {})),))
            if fut is not None and not fut.done():
                fut.set_result(normalize(blocks))
        # Re-raise any exception the probe body raised (e.g. a failed sandbox
        # check or a fail-fast tool error).
        await probe_task
    finally:
        if not probe_task.done():
            probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await probe_task


def bridge_probe_for(
    action: str,
    *,
    probe_ctx: Any,
) -> tuple[ProbeFactory, str] | None:
    """Map a call_tool-based executor action to ``(probe_factory, summary)``.

    Returns ``None`` if *action* is not a bridge probe (the caller then tries
    the generator-style ``PROBE_BUILDERS`` or raises ``NotImplementedError``).
    Probe modules are imported lazily to keep the package import graph DAG-shaped.
    """
    metadata = probe_ctx.metadata

    if action == "high_concurrency_seed":
        def _seed(call_tool: Any) -> Awaitable[str]:
            from task_center_runner.agent.mock.high_concurrency_probe import (
                run_high_concurrency_seed_probe,
            )

            return run_high_concurrency_seed_probe(
                metadata=metadata,
                emit=_noop_emit,
                call_tool=call_tool,
                record_tool_check=probe_ctx.record_check,
            )

        return _seed, "High-concurrency sandbox seed passed."

    if action.startswith("high_concurrency_worker:"):
        index = int(action.split(":", 1)[1])

        def _worker(call_tool: Any) -> Awaitable[str]:
            from task_center_runner.agent.mock.high_concurrency_probe import (
                run_high_concurrency_worker_probe,
            )

            return run_high_concurrency_worker_probe(
                index=index,
                metadata=metadata,
                emit=_noop_emit,
                call_tool=call_tool,
                publish=probe_ctx.publish,
                publish_mock_record=probe_ctx.publish_mock_record,
                record_tool_check=probe_ctx.record_check,
            )

        return _worker, f"High-concurrency worker {index:02d} passed."

    if action == "high_concurrency_reconcile":
        def _reconcile(call_tool: Any) -> Awaitable[str]:
            from task_center_runner.agent.mock.high_concurrency_probe import (
                run_high_concurrency_reconcile_probe,
            )

            return run_high_concurrency_reconcile_probe(
                metadata=metadata,
                emit=_noop_emit,
                call_tool=call_tool,
                record_tool_check=probe_ctx.record_check,
            )

        return _reconcile, "High-concurrency sandbox reconciliation passed."

    return None


__all__ = ["bridge_probe_for", "bridge_turns"]
