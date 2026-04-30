"""Soft reminder tests for Phase 03 submission rules."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from message.messages import ConversationMessage, ToolResultBlock, ToolUseBlock
from notification.rules import dispatch_rules
from notification.service import SystemNotificationService
from task_center.harness_graph.orchestrator import HarnessGraphOrchestrator
from task_center.segment.segment import TaskSegmentCreationReason
from task_center.task import (
    PlannedGeneratorTask,
    PlannerSubmission,
    generator_task_id,
    planner_task_id,
)
from tools.core.runtime import ExecutionMetadata
from tools.submission.notification_triggers import (
    make_recursive_partial_plan_reminder,
    make_request_after_edit_reminder,
    make_resolver_limit_reminder,
    resolve_harness_notification_triggers,
)

from .submission_test_utils import build_harness_fixture

pytestmark = pytest.mark.asyncio


def _edit_messages() -> list[ConversationMessage]:
    return [
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="toolu_edit", name="shell", input={})],
        )
    ]


def _resolver_messages(count: int) -> list[ConversationMessage]:
    messages: list[ConversationMessage] = []
    for index in range(count):
        tool_id = f"toolu_resolver_{index}"
        messages.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=tool_id, name="ask_resolver", input={})],
            )
        )
        messages.append(
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id=tool_id,
                        content="not resolved",
                        metadata={"resolver": {"resolved": False}},
                    )
                ],
            )
        )
    return messages


async def _dispatch(rule, messages, context):
    service = SystemNotificationService()
    await dispatch_rules([rule], messages, context, service, set())
    return service.pop_pending_notifications()


def _apply_partial_parent_plan(fixture) -> str:
    planner_id = planner_task_id(fixture.graph_id)
    fixture.orchestrator.start()
    fixture.orchestrator.apply_plan_submission(
        PlannerSubmission(
            graph_id=fixture.graph_id,
            planner_task_id=planner_id,
            kind="partial",
            task_specification="parent partial spec",
            evaluation_criteria=("parent criterion",),
            tasks=(
                PlannedGeneratorTask(
                    local_id="a",
                    agent_name="executor",
                    deps=(),
                    task_spec="request child work",
                ),
            ),
            continuation_goal="continue parent request later",
            summary="parent partial plan",
        )
    )
    return generator_task_id(fixture.graph_id, "a")


def _create_child_graph(fixture, *, requested_by_task_id: str):
    child_request = fixture.runtime.request_store.insert(
        task_center_run_id="run1",
        requested_by_task_id=requested_by_task_id,
        goal="child request",
    )
    child_segment = fixture.runtime.segment_store.insert(
        complex_task_request_id=child_request.id,
        sequence_no=1,
        creation_reason=TaskSegmentCreationReason.INITIAL,
        goal="child request",
        attempt_budget=2,
    )
    fixture.runtime.request_store.append_segment_id(
        child_request.id, child_segment.id
    )
    child_graph = fixture.runtime.graph_store.insert(
        task_segment_id=child_segment.id,
        graph_sequence_no=1,
    )
    fixture.runtime.segment_store.append_graph_id(child_segment.id, child_graph.id)
    child_orchestrator = HarnessGraphOrchestrator(
        harness_graph=child_graph,
        on_graph_closed=lambda graph_id: None,
        runtime=fixture.runtime,
    )
    fixture.runtime.orchestrator_registry.register(child_orchestrator)
    child_orchestrator.start()
    return child_graph


async def test_after_edit_reminder_fires_once() -> None:
    ctx = SimpleNamespace(tool_metadata=None, cwd="/tmp")

    notifications = await _dispatch(
        make_request_after_edit_reminder(),
        _edit_messages(),
        ctx,
    )

    assert len(notifications) == 1
    assert "request_complex_task_solution is disabled" in notifications[0].text


async def test_resolver_limit_reminder_fires_at_four() -> None:
    ctx = SimpleNamespace(tool_metadata=None, cwd="/tmp")

    notifications = await _dispatch(
        make_resolver_limit_reminder(),
        _resolver_messages(4),
        ctx,
    )

    assert len(notifications) == 1
    assert "One unresolved resolver call remains" in notifications[0].text


async def test_recursive_partial_plan_reminder_does_not_fire_for_same_request_continuation(
    request_store, segment_store, graph_store, task_store
) -> None:
    fixture = build_harness_fixture(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
    )
    segment_store.set_continuation_goal(fixture.segment_id, "continue")
    segment2 = segment_store.insert(
        complex_task_request_id=fixture.request_id,
        sequence_no=2,
        creation_reason=TaskSegmentCreationReason.PARTIAL_CONTINUATION,
        goal="next segment",
        attempt_budget=2,
    )
    request_store.append_segment_id(fixture.request_id, segment2.id)
    graph2 = graph_store.insert(task_segment_id=segment2.id, graph_sequence_no=1)
    segment_store.append_graph_id(segment2.id, graph2.id)
    orchestrator2 = HarnessGraphOrchestrator(
        harness_graph=graph2,
        on_graph_closed=lambda graph_id: None,
        runtime=fixture.runtime,
    )
    fixture.runtime.orchestrator_registry.register(orchestrator2)
    orchestrator2.start()
    metadata = ExecutionMetadata(
        task_center_task_id=planner_task_id(graph2.id),
        task_center_harness_graph_id=graph2.id,
        harness_graph_runtime=fixture.runtime,
    )
    ctx = SimpleNamespace(tool_metadata=metadata, cwd="/tmp")

    notifications = await _dispatch(
        make_recursive_partial_plan_reminder(),
        [],
        ctx,
    )

    assert notifications == []


async def test_recursive_partial_plan_reminder_fires_for_child_of_partial_graph(
    request_store, segment_store, graph_store, task_store
) -> None:
    fixture = build_harness_fixture(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
    )
    parent_generator_id = _apply_partial_parent_plan(fixture)
    child_graph = _create_child_graph(
        fixture, requested_by_task_id=parent_generator_id
    )
    metadata = ExecutionMetadata(
        task_center_task_id=planner_task_id(child_graph.id),
        task_center_harness_graph_id=child_graph.id,
        harness_graph_runtime=fixture.runtime,
    )
    ctx = SimpleNamespace(tool_metadata=metadata, cwd="/tmp")

    notifications = await _dispatch(
        make_recursive_partial_plan_reminder(),
        [],
        ctx,
    )

    assert len(notifications) == 1
    assert "submit_partial_plan is disabled" in notifications[0].text


async def test_resolve_harness_notification_triggers_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        resolve_harness_notification_triggers(["missing"])
