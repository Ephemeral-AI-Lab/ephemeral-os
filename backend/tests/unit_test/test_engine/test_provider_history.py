"""Unit tests for durable message history vs provider message views."""

from __future__ import annotations

import asyncio
import copy

import pytest

from engine.query.provider_history import (
    prepare_provider_messages,
    reduce_background_task_history,
    sanitize_tool_sequence,
)
from engine.background.manager import BackgroundTaskManager
from engine.background.reminder import build_background_reminder
from message.messages import (
    BackgroundTaskStateBlock,
    ConversationMessage,
    SystemNotificationBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from tools.background._lib._common import (
    build_background_snapshot_metadata,
    render_background_snapshot,
)
from tools._framework.core.base import ToolResult


def _user(text: str) -> ConversationMessage:
    return ConversationMessage.from_user_text(text)


def _assistant(text: str) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


def _tool_use(id: str, name: str, input: dict) -> ConversationMessage:  # noqa: A002
    return ConversationMessage(
        role="assistant",
        content=[ToolUseBlock(id=id, name=name, input=input)],
    )


def _tool_result(tool_use_id: str, content: str) -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=content)],
    )


def _bg_state(
    task_id: str,
    tool_name: str,
    task_type: str,
    status: str,
    source: str,
    text: str,
) -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[
            BackgroundTaskStateBlock(
                task_id=task_id,
                tool_name=tool_name,
                task_type=task_type,
                status=status,
                source=source,
                text=text,
            )
        ],
    )


async def _slow_coro() -> ToolResult:
    """Coroutine that never completes naturally."""
    await asyncio.sleep(10)
    return ToolResult(output="done")


class TestPrepareProviderMessages:
    """Provider-history preparation must not mutate the display transcript."""

    def test_returns_fresh_provider_list(self) -> None:
        display = [_user("hello"), _user("world")]
        snapshot = copy.deepcopy(display)

        provider = prepare_provider_messages(display)

        assert display == snapshot
        assert provider is not display
        assert provider[0] is not display[0]
        assert [m.text for m in provider] == [m.text for m in display]

    def test_sanitize_tool_sequence_drops_orphaned_tool_results(self) -> None:
        messages = [
            _user("prompt"),
            _assistant("no tools here"),
            _tool_result("toolu_orphan", "stale result"),
        ]

        sanitized = sanitize_tool_sequence(messages)

        assert len(sanitized) == 2
        assert all(
            not any(isinstance(block, ToolResultBlock) for block in msg.content)
            for msg in sanitized
        )

    def test_prepare_provider_messages_sanitizes_invalid_history(self) -> None:
        display = [
            _user("older context"),
            _tool_use("toolu_pair", "echo", {"value": "x"}),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="toolu_pair",
                        content="expected tool result",
                    ),
                    ToolResultBlock(
                        tool_use_id="toolu_orphan",
                        content="unexpected extra tool result",
                    ),
                ],
            ),
            _user("newer context"),
        ]

        provider = prepare_provider_messages(display)

        result_ids = {
            block.tool_use_id
            for msg in provider
            for block in msg.content
            if isinstance(block, ToolResultBlock)
        }
        assert result_ids == {"toolu_pair"}

    def test_reduce_background_task_history_drops_stale_snapshot_pairs(self) -> None:
        display = [
            _tool_use("toolu_1", "wait_background_tasks", {"task_id": "all"}),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="toolu_1",
                        content=render_background_snapshot(
                            "progress",
                            [{"task_id": "bg_1", "status": "running", "output": "old"}],
                        ),
                        metadata=build_background_snapshot_metadata(
                            "progress",
                            "all",
                            [{"task_id": "bg_1", "status": "running", "output": "old"}],
                        ),
                    )
                ],
            ),
            _bg_state(
                "bg_1",
                "run_subagent",
                "subagent",
                "completed",
                "engine_terminal",
                "done",
            ),
        ]
        snapshot = copy.deepcopy(display)

        provider = reduce_background_task_history(display)

        assert display == snapshot
        assert any(
            isinstance(block, ToolUseBlock) and block.id == "toolu_1"
            for msg in display
            for block in msg.content
        )
        assert all(
            not any(
                isinstance(block, ToolUseBlock) and block.id == "toolu_1"
                for block in msg.content
            )
            for msg in provider
        )

    def test_reduce_background_task_history_prefers_delivered_snapshot(self) -> None:
        statuses = [
            {
                "task_id": "bg_1",
                "tool_name": "run_subagent",
                "task_type": "subagent",
                "status": "delivered",
                "output": '{"summary": "Posted."}',
            }
        ]
        display = [
            _tool_use("toolu_wait", "wait_background_tasks", {"task_id": "all"}),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="toolu_wait",
                        content=render_background_snapshot("wait_no_tasks", statuses),
                        metadata=build_background_snapshot_metadata(
                            "wait_no_tasks",
                            "all",
                            statuses,
                        ),
                    )
                ],
            ),
            _bg_state(
                "bg_1",
                "run_subagent",
                "subagent",
                "running",
                "engine_progress",
                "Running for 0s",
            ),
        ]

        provider = reduce_background_task_history(display)

        assert any(
            isinstance(block, ToolUseBlock) and block.id == "toolu_wait"
            for msg in provider
            for block in msg.content
        )
        result_contents = [
            block.content
            for msg in provider
            for block in msg.content
            if isinstance(block, ToolResultBlock)
        ]
        assert any("[NO TASKS]" in content for content in result_contents)
        assert all(
            not any(
                isinstance(block, BackgroundTaskStateBlock)
                and block.task_id == "bg_1"
                and block.status == "running"
                for block in msg.content
            )
            for msg in provider
        )


class TestBuildBackgroundReminder:
    """The reminder is a regular ConversationMessage in message history."""

    def test_returns_none_when_no_pending_tasks(self) -> None:
        mgr = BackgroundTaskManager()
        assert build_background_reminder(mgr) is None

    @pytest.mark.asyncio
    async def test_includes_task_id_and_progress(self) -> None:
        mgr = BackgroundTaskManager()
        mgr.launch(
            "bg_1",
            "shell",
            {"command": "sleep 10"},
            _slow_coro(),
        )
        mgr.append_progress("bg_1", "halfway there")

        msg = build_background_reminder(mgr)
        assert msg is not None
        assert msg.text == ""
        reminder_text = msg.background_task_state_text
        assert "bg_1" in reminder_text
        assert "halfway there" in reminder_text
        assert "Keep working on any other ready analysis or tool tasks first" in reminder_text
        assert "Only wait when this background task is the remaining blocker" in reminder_text
        assert "Do not recheck task ids after a terminal status" in reminder_text
        assert msg.background_task_states[0].status == "running"
        api_param = msg.to_api_param()
        assert "<background-task" in api_param["content"][0]["text"]

        msg2 = build_background_reminder(mgr)
        assert msg2 is not None
        assert "halfway there" not in msg2.background_task_state_text
        assert "No new output" in msg2.background_task_state_text
        assert "Only wait when this background task is the remaining blocker" in (
            msg2.background_task_state_text
        )

        await mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_appendable_to_messages_list(self) -> None:
        mgr = BackgroundTaskManager()
        mgr.launch("bg_1", "tool", {}, _slow_coro())
        display: list[ConversationMessage] = [_user("hi")]

        reminder = build_background_reminder(mgr)
        assert reminder is not None
        display.append(reminder)

        assert len(display) == 2
        assert display[1].role == "user"
        assert len(display[1].background_task_states) == 1
        assert display[1].text == ""

        await mgr.cancel_all()


class TestSystemNotificationBlock:
    """SystemNotificationBlock must round-trip and serialize as provider text."""

    def test_block_construction_and_defaults(self) -> None:
        block = SystemNotificationBlock(text="hello")
        assert block.type == "system_notification"
        assert block.text == "hello"

    def test_message_with_notification_text_excludes_notification(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[
                TextBlock(text="hi"),
                SystemNotificationBlock(text="background bg_1 still running"),
            ],
        )
        assert msg.text == "hi"
        assert msg.system_notification_text == "background bg_1 still running"
        assert len(msg.system_notifications) == 1

    def test_to_api_param_wraps_in_tags(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[SystemNotificationBlock(text="bg_1 done")],
        )
        api = msg.to_api_param()
        assert api["role"] == "user"
        assert len(api["content"]) == 1
        block = api["content"][0]
        assert block["type"] == "text"
        assert block["text"] == "<system-reminder>\nbg_1 done\n</system-reminder>"

    def test_to_api_param_mixed_content_preserves_order(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[
                TextBlock(text="user said"),
                SystemNotificationBlock(text="notification"),
                TextBlock(text="more"),
            ],
        )
        api = msg.to_api_param()
        types = [block["type"] for block in api["content"]]
        assert types == ["text", "text", "text"]
        assert api["content"][0]["text"] == "user said"
        assert "<system-reminder>" in api["content"][1]["text"]
        assert api["content"][2]["text"] == "more"

    def test_pydantic_round_trip(self) -> None:
        original = ConversationMessage(
            role="user",
            content=[
                SystemNotificationBlock(text="hi"),
            ],
        )
        dumped = original.model_dump()
        restored = ConversationMessage.model_validate(dumped)
        assert len(restored.content) == 1
        block = restored.content[0]
        assert isinstance(block, SystemNotificationBlock)
        assert block.text == "hi"

    def test_empty_notification_text(self) -> None:
        block = SystemNotificationBlock(text="")
        msg = ConversationMessage(role="user", content=[block])
        api = msg.to_api_param()
        assert api["content"][0]["text"] == "<system-reminder>\n\n</system-reminder>"

    def test_multiple_notifications_in_one_message(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[
                SystemNotificationBlock(text="first"),
                SystemNotificationBlock(text="second"),
            ],
        )
        assert len(msg.system_notifications) == 2
        assert msg.system_notification_text == "first\nsecond"
        api = msg.to_api_param()
        assert len(api["content"]) == 2
        assert "first" in api["content"][0]["text"]
        assert "second" in api["content"][1]["text"]


class TestBuildReminderEdgeCases:
    """Cover multi-task ordering and completed-task filtering."""

    @pytest.mark.asyncio
    async def test_multiple_pending_tasks_all_appear(self) -> None:
        mgr = BackgroundTaskManager()
        mgr.launch("bg_1", "tool_a", {}, _slow_coro())
        mgr.launch("bg_2", "tool_b", {}, _slow_coro())
        mgr.append_progress("bg_1", "alpha")
        mgr.append_progress("bg_2", "beta")

        msg = build_background_reminder(mgr)
        assert msg is not None
        text = msg.background_task_state_text
        assert "bg_1" in text and "tool_a" in text and "alpha" in text
        assert "bg_2" in text and "tool_b" in text and "beta" in text

        await mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_completed_tasks_excluded(self) -> None:
        mgr = BackgroundTaskManager()

        async def _quick() -> ToolResult:
            return ToolResult(output="finished")

        mgr.launch("bg_done", "tool_quick", {}, _quick())
        mgr.launch("bg_running", "tool_slow", {}, _slow_coro())
        await asyncio.sleep(0.05)

        msg = build_background_reminder(mgr)
        assert msg is not None
        text = msg.background_task_state_text
        assert "bg_running" in text
        assert "bg_done" not in text

        await mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_no_progress_branch_uses_seconds_since_format(self) -> None:
        mgr = BackgroundTaskManager()
        mgr.launch("bg_x", "tool", {}, _slow_coro())
        first = build_background_reminder(mgr)
        assert first is not None
        second = build_background_reminder(mgr)
        assert second is not None
        assert "No new output" in second.background_task_state_text

        await mgr.cancel_all()


class TestConversationMessageMixed:
    """SystemNotificationBlock must not interfere with other block accessors."""

    def test_text_property_only_returns_text_blocks(self) -> None:
        msg = ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="hello"),
                ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"}),
                TextBlock(text=" world"),
            ],
        )
        assert msg.text == "hello world"
        assert msg.system_notification_text == ""
        assert msg.system_notifications == []

    def test_tool_uses_property_unaffected(self) -> None:
        msg = ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"}),
                SystemNotificationBlock(text="ignore me"),
            ],
        )
        assert len(msg.tool_uses) == 1
        assert msg.tool_uses[0].name == "bash"

    def test_pydantic_discriminator_distinguishes_text_vs_notification(self) -> None:
        original = ConversationMessage(
            role="user",
            content=[
                TextBlock(text="real user input"),
                SystemNotificationBlock(text="engine note"),
            ],
        )
        dumped = original.model_dump()
        restored = ConversationMessage.model_validate(dumped)
        assert isinstance(restored.content[0], TextBlock)
        assert isinstance(restored.content[1], SystemNotificationBlock)
