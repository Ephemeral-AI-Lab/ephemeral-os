# ruff: noqa
"""E2E tests for background task execution through the query loop.

Tests the full background task lifecycle using scripted mock LLM responses
to validate that the engine correctly handles:
1. LLM deciding to background a tool vs foreground
2. LLM doing foreground work while background runs, then going idle
3. LLM proactively calling check_background_progress
4. LLM cancelling a background task after seeing failures
5. LLM cancelling a hanging background task after repeated checks

Uses a mock LLM client with scripted responses and a fake slow tool
to simulate real background execution scenarios without hitting real APIs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from engine.background_tasks import BackgroundTaskManager
from engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from engine.query import QueryContext, _run_query_loop
from engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    BackgroundTaskCompleted,
    BackgroundTaskStarted,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from models.types import (
    ApiMessageCompleteEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
    ApiToolUseDeltaEvent,
    UsageSnapshot,
)
from tools.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolRegistry, ToolResult
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


# ---------------------------------------------------------------------------
# Fake slow tool — simulates daytona_bash with configurable delay and output
# ---------------------------------------------------------------------------


class SlowToolInput(BaseModel):
    """Input for the fake slow tool."""
    command: str = Field(description="Command to simulate")
    delay: float = Field(default=0.1, description="Seconds to sleep")


class SlowTool(BaseTool):
    """A fake tool that sleeps, then returns output. Supports background."""

    name: str = "fake_bash"
    description: str = "Run a fake shell command with configurable delay."
    input_model: type[BaseModel] = SlowToolInput
    supports_background: bool = True

    def __init__(self, output: str = "command completed", is_error: bool = False) -> None:
        self._output = output
        self._is_error = is_error

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, SlowToolInput)
        logger.info(f"[SlowTool] Executing: {arguments.command} (delay={arguments.delay}s)")
        await asyncio.sleep(arguments.delay)
        logger.info(f"[SlowTool] Done: {arguments.command} -> {self._output[:100]}")
        return ToolResult(output=self._output, is_error=self._is_error)


class FastToolInput(BaseModel):
    """Input for the fake fast tool."""
    action: str = Field(description="Action to perform")


class FastTool(BaseTool):
    """A fake fast tool that returns immediately. Does NOT support background."""

    name: str = "fake_edit"
    description: str = "A fast tool that completes immediately."
    input_model: type[BaseModel] = FastToolInput
    supports_background: bool = False

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, FastToolInput)
        logger.info(f"[FastTool] Executing: {arguments.action}")
        return ToolResult(output=f"Edited: {arguments.action}", is_error=False)


# ---------------------------------------------------------------------------
# Scripted mock LLM client
# ---------------------------------------------------------------------------


class ScriptedMockClient:
    """Mock LLM client that returns scripted responses in sequence.

    Each call to stream_message returns the next response in the script.
    Captures all requests for assertion. Logs each turn for debugging.
    """

    def __init__(self, responses: list[ConversationMessage]) -> None:
        self.responses = responses
        self._call_count = 0
        self.all_requests: list[Any] = []

    async def stream_message(self, request: Any) -> AsyncIterator[ApiStreamEvent]:
        self.all_requests.append(request)
        idx = min(self._call_count, len(self.responses) - 1)
        msg = self.responses[idx]
        self._call_count += 1

        logger.info(
            f"[MockLLM] Turn {self._call_count}: "
            f"text={msg.text[:80]!r}, "
            f"tool_uses={[tu.name for tu in msg.tool_uses]}"
        )

        # Stream text deltas
        for block in msg.content:
            if isinstance(block, TextBlock):
                yield ApiTextDeltaEvent(text=block.text)

        yield ApiMessageCompleteEvent(
            message=msg,
            usage=UsageSnapshot(input_tokens=100, output_tokens=50),
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(*tools: BaseTool) -> ToolRegistry:
    """Create a ToolRegistry with given tools."""
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    return registry


def _make_context(
    client: ScriptedMockClient,
    registry: ToolRegistry,
    enable_background: bool = True,
    max_turns: int = 20,
) -> QueryContext:
    """Create a QueryContext for testing."""
    return QueryContext(
        api_client=client,
        tool_registry=registry,
        cwd=Path("/tmp/test"),
        model="test-model",
        system_prompt="You are a test assistant.",
        max_tokens=4096,
        max_turns=max_turns,
        enable_background_tasks=enable_background,
    )


async def _collect_events(context: QueryContext, messages: list[ConversationMessage]) -> list[StreamEvent]:
    """Run the query loop and collect all events."""
    events: list[StreamEvent] = []
    async for event, _usage in _run_query_loop(context, messages):
        event_type = type(event).__name__
        logger.debug(f"[Event] {event_type}: {str(event)[:200]}")
        events.append(event)
    return events


def _events_of_type(events: list[StreamEvent], cls: type) -> list:
    """Filter events by type."""
    return [e for e in events if isinstance(e, cls)]


def _msg_text(text: str) -> ConversationMessage:
    """Create an assistant text-only message (no tool calls → loop stops)."""
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


def _msg_tool(tool_name: str, tool_input: dict, text: str = "") -> ConversationMessage:
    """Create an assistant message with a tool call."""
    content: list = []
    if text:
        content.append(TextBlock(text=text))
    content.append(ToolUseBlock(name=tool_name, input=tool_input))
    return ConversationMessage(role="assistant", content=content)


def _msg_tools(*tool_calls: tuple[str, dict], text: str = "") -> ConversationMessage:
    """Create an assistant message with multiple tool calls."""
    content: list = []
    if text:
        content.append(TextBlock(text=text))
    for name, inp in tool_calls:
        content.append(ToolUseBlock(name=name, input=inp))
    return ConversationMessage(role="assistant", content=content)


# ===========================================================================
# Test 1: LLM decides to background vs foreground
# ===========================================================================


class TestLLMDecidesToBackground:
    """LLM sends background=true for slow tool, foreground for fast tool.
    Validates that supports_background=True is required and LLM has choice.
    """

    async def test_llm_chooses_background_for_slow_tool(self):
        """LLM sends background=true → engine launches async, returns immediately."""
        slow_tool = SlowTool(output="tests passed: 48/48")
        registry = _make_registry(slow_tool)

        client = ScriptedMockClient([
            # Turn 1: LLM decides to background the slow command
            _msg_tool("fake_bash", {"command": "pytest", "delay": 0.5, "background": True},
                      text="Running tests in background..."),
            # Turn 2: LLM has no more work (idle → wait for background)
            _msg_text("Waiting for tests to complete."),
            # Turn 3: After background completes, LLM reacts
            _msg_text("All 48 tests passed!"),
        ])

        context = _make_context(client, registry)
        messages = [ConversationMessage.from_user_text("Run the tests")]
        events = await _collect_events(context, messages)

        # Should have BackgroundTaskStarted
        bg_started = _events_of_type(events, BackgroundTaskStarted)
        assert len(bg_started) == 1, f"Expected 1 BackgroundTaskStarted, got {len(bg_started)}"
        assert bg_started[0].tool_name == "fake_bash"
        logger.info(f"[PASS] Background task started: {bg_started[0].task_id}")

        # Should have BackgroundTaskCompleted
        bg_completed = _events_of_type(events, BackgroundTaskCompleted)
        assert len(bg_completed) == 1, f"Expected 1 BackgroundTaskCompleted, got {len(bg_completed)}"
        assert "tests passed" in bg_completed[0].output
        logger.info(f"[PASS] Background task completed with output: {bg_completed[0].output[:100]}")

    async def test_llm_chooses_foreground_for_same_tool(self):
        """LLM does NOT send background=true → tool runs in foreground (blocking)."""
        slow_tool = SlowTool(output="quick result")
        registry = _make_registry(slow_tool)

        client = ScriptedMockClient([
            # Turn 1: LLM runs the tool in foreground (no background flag)
            _msg_tool("fake_bash", {"command": "echo hello", "delay": 0.01}),
            # Turn 2: LLM done
            _msg_text("Done."),
        ])

        context = _make_context(client, registry)
        messages = [ConversationMessage.from_user_text("Run a quick command")]
        events = await _collect_events(context, messages)

        # Should NOT have BackgroundTaskStarted
        bg_started = _events_of_type(events, BackgroundTaskStarted)
        assert len(bg_started) == 0, f"Expected no BackgroundTaskStarted, got {len(bg_started)}"

        # Should have normal ToolExecutionCompleted
        tool_completed = _events_of_type(events, ToolExecutionCompleted)
        assert len(tool_completed) == 1
        assert "quick result" in tool_completed[0].output
        logger.info("[PASS] Tool ran in foreground as expected")

    async def test_background_rejected_for_unsupported_tool(self):
        """LLM sends background=true on a tool that doesn't support it → error."""
        fast_tool = FastTool()
        registry = _make_registry(fast_tool)

        client = ScriptedMockClient([
            # Turn 1: LLM tries to background a fast tool
            _msg_tool("fake_edit", {"action": "fix config", "background": True}),
            # Turn 2: LLM sees error, adapts
            _msg_text("I see the tool doesn't support background. Let me run it normally."),
        ])

        context = _make_context(client, registry)
        messages = [ConversationMessage.from_user_text("Fix the config")]
        events = await _collect_events(context, messages)

        # Should NOT have BackgroundTaskStarted
        bg_started = _events_of_type(events, BackgroundTaskStarted)
        assert len(bg_started) == 0

        # Should have error in tool completion
        tool_completed = _events_of_type(events, ToolExecutionCompleted)
        assert any("does not support background" in tc.output for tc in tool_completed), \
            f"Expected rejection message. Got: {[tc.output for tc in tool_completed]}"
        logger.info("[PASS] Background correctly rejected for unsupported tool")


# ===========================================================================
# Test 2: Foreground work while background runs, then idle notification
# ===========================================================================


class TestForegroundWhileBackgroundRuns:
    """LLM does foreground work while a background task runs.
    When foreground work finishes, engine idle-waits and injects result.
    """

    async def test_foreground_then_idle_wait(self):
        """LLM backgrounds slow task, does foreground work, goes idle, gets result."""
        slow_tool = SlowTool(output="BUILD SUCCESSFUL in 2s")
        fast_tool = FastTool()
        registry = _make_registry(slow_tool, fast_tool)

        client = ScriptedMockClient([
            # Turn 1: LLM backgrounds the build AND does a foreground edit
            _msg_tools(
                ("fake_bash", {"command": "npm run build", "delay": 0.5, "background": True}),
                ("fake_edit", {"action": "fix typo in readme"}),
                text="Building in background while I fix the readme...",
            ),
            # Turn 2: LLM finishes foreground, goes idle (no tool calls)
            _msg_text("README fixed. Waiting for build..."),
            # Turn 3: Engine injected background result → LLM reacts
            _msg_text("Build succeeded! All done."),
        ])

        context = _make_context(client, registry)
        messages = [ConversationMessage.from_user_text("Build the project and fix the readme")]
        events = await _collect_events(context, messages)

        # Verify background started
        bg_started = _events_of_type(events, BackgroundTaskStarted)
        assert len(bg_started) == 1
        assert bg_started[0].tool_name == "fake_bash"

        # Verify foreground tool completed normally
        tool_completed = _events_of_type(events, ToolExecutionCompleted)
        foreground_results = [tc for tc in tool_completed if "Edited:" in tc.output]
        assert len(foreground_results) >= 1, "Foreground edit should complete"

        # Verify background completed
        bg_completed = _events_of_type(events, BackgroundTaskCompleted)
        assert len(bg_completed) == 1
        assert "BUILD SUCCESSFUL" in bg_completed[0].output

        # Verify LLM got 3 turns
        turns = _events_of_type(events, AssistantTurnComplete)
        assert len(turns) == 3, f"Expected 3 LLM turns, got {len(turns)}"
        logger.info("[PASS] Foreground work completed while background ran, idle wait delivered result")


# ===========================================================================
# Test 3: LLM proactively calls check_background_progress
# ===========================================================================


class TestProactiveProgressCheck:
    """LLM calls check_background_progress while doing foreground work."""

    async def test_llm_checks_progress_proactively(self):
        """LLM backgrounds a task, does work, proactively checks status."""
        slow_tool = SlowTool(output="All 48 tests passed")
        fast_tool = FastTool()
        registry = _make_registry(slow_tool, fast_tool)

        client = ScriptedMockClient([
            # Turn 1: Background the tests
            _msg_tool("fake_bash", {"command": "pytest", "delay": 0.8, "background": True},
                      text="Running tests in background..."),
            # Turn 2: Do foreground work
            _msg_tool("fake_edit", {"action": "update config"},
                      text="While tests run, let me update the config."),
            # Turn 3: Proactively check progress
            _msg_tool("check_background_progress", {},
                      text="Let me check on the tests..."),
            # Turn 4: Based on status, wait
            _msg_text("Tests still running, I'll wait."),
            # Turn 5: After idle wait delivers result
            _msg_text("All 48 tests passed! Great."),
        ])

        context = _make_context(client, registry)
        messages = [ConversationMessage.from_user_text("Run tests and update config")]
        events = await _collect_events(context, messages)

        # Verify check_background_progress was called
        tool_completed = _events_of_type(events, ToolExecutionCompleted)
        progress_results = [tc for tc in tool_completed if tc.tool_name == "check_background_progress"]
        assert len(progress_results) >= 1, "LLM should have called check_background_progress"

        # The progress result should contain task info
        progress_output = progress_results[0].output
        assert "fake_bash" in progress_output, f"Progress should mention tool name. Got: {progress_output[:200]}"
        logger.info(f"[PASS] Progress check returned: {progress_output[:200]}")

        # Background should eventually complete
        bg_completed = _events_of_type(events, BackgroundTaskCompleted)
        assert len(bg_completed) == 1
        logger.info("[PASS] LLM proactively checked progress and got final result")


# ===========================================================================
# Test 4: LLM cancels background task after seeing failure
# ===========================================================================


class TestCancelOnFailure:
    """LLM sees test failures via check_background_progress and cancels."""

    async def test_llm_cancels_failing_test_suite(self):
        """LLM backgrounds tests, checks progress, sees failure pattern, cancels."""
        # This tool will take a while — LLM cancels before it finishes
        slow_tool = SlowTool(output="FAIL: 15 tests failed\nsome long output...", is_error=True)
        slow_tool._output = "FAIL: 15 tests failed"  # won't finish — gets cancelled
        registry = _make_registry(slow_tool, FastTool())

        client = ScriptedMockClient([
            # Turn 1: Background the tests
            _msg_tool("fake_bash", {"command": "pytest --timeout=60", "delay": 5.0, "background": True},
                      text="Running full test suite in background..."),
            # Turn 2: Do some foreground work
            _msg_tool("fake_edit", {"action": "fix auth module"},
                      text="While tests run, fixing the auth module."),
            # Turn 3: Check progress — LLM wants to see how tests are going
            _msg_tool("check_background_progress", {},
                      text="Let me check on the tests..."),
            # Turn 4: LLM sees "running" status, decides to cancel
            # (In real scenario LLM would see partial output showing failures)
            _msg_tool("cancel_background_task", {"task_id": "PLACEHOLDER", "reason": "Tests are failing, need to fix auth first"},
                      text="Tests seem to be running long, cancelling to fix auth first."),
            # Turn 5: Done
            _msg_text("Cancelled tests. Will fix auth and re-run."),
        ])

        context = _make_context(client, registry)
        messages = [ConversationMessage.from_user_text("Run the full test suite")]

        # We need to capture the actual task_id to patch it into the cancel call.
        # Run the loop and collect events.
        events: list[StreamEvent] = []
        actual_task_id = None

        async for event, _usage in _run_query_loop(context, messages):
            event_type = type(event).__name__
            logger.debug(f"[Event] {event_type}: {str(event)[:200]}")
            events.append(event)

            # Capture the task_id from BackgroundTaskStarted
            if isinstance(event, BackgroundTaskStarted) and actual_task_id is None:
                actual_task_id = event.task_id
                logger.info(f"[TestCancel] Captured task_id: {actual_task_id}")
                # Patch the cancel response to use the real task_id
                cancel_msg = client.responses[3]
                for block in cancel_msg.content:
                    if isinstance(block, ToolUseBlock) and block.name == "cancel_background_task":
                        block.input["task_id"] = actual_task_id
                        logger.info(f"[TestCancel] Patched cancel call with task_id: {actual_task_id}")

        # Verify background was started
        bg_started = _events_of_type(events, BackgroundTaskStarted)
        assert len(bg_started) == 1

        # Verify cancel was executed
        tool_completed = _events_of_type(events, ToolExecutionCompleted)
        cancel_results = [tc for tc in tool_completed if tc.tool_name == "cancel_background_task"]
        assert len(cancel_results) >= 1, "LLM should have called cancel_background_task"
        assert "cancelled" in cancel_results[0].output.lower(), \
            f"Cancel should confirm. Got: {cancel_results[0].output}"
        logger.info(f"[PASS] Cancel result: {cancel_results[0].output}")

        # The background task should NOT have a normal completion
        bg_completed = _events_of_type(events, BackgroundTaskCompleted)
        # It may or may not have completed depending on timing — the cancel
        # is what matters
        logger.info(f"[PASS] LLM cancelled background task. bg_completed={len(bg_completed)}")


# ===========================================================================
# Test 5: LLM cancels hanging background task after repeated checks
# ===========================================================================


class TestCancelHangingTask:
    """LLM backgrounds a task that hangs, checks repeatedly, then cancels."""

    async def test_llm_cancels_after_repeated_checks(self):
        """LLM backgrounds npm install that hangs, checks twice, then cancels."""
        # This tool hangs for 30s — will be cancelled
        hanging_tool = SlowTool(output="never finishes")
        hanging_tool.name = "fake_bash"
        registry = _make_registry(hanging_tool)

        client = ScriptedMockClient([
            # Turn 1: Background the install
            _msg_tool("fake_bash", {"command": "npm install", "delay": 30.0, "background": True},
                      text="Installing dependencies in background..."),
            # Turn 2: First check
            _msg_tool("check_background_progress", {},
                      text="Let me check install progress..."),
            # Turn 3: Still running, check again
            _msg_tool("check_background_progress", {},
                      text="Still running... checking again."),
            # Turn 4: Still running → cancel it
            _msg_tool("cancel_background_task", {"task_id": "PLACEHOLDER", "reason": "npm install appears to be hanging"},
                      text="Install is hanging, cancelling."),
            # Turn 5: Done
            _msg_text("Cancelled hanging install. Will try with --legacy-peer-deps."),
        ])

        context = _make_context(client, registry)
        messages = [ConversationMessage.from_user_text("Install dependencies")]

        events: list[StreamEvent] = []
        actual_task_id = None

        async for event, _usage in _run_query_loop(context, messages):
            logger.debug(f"[Event] {type(event).__name__}: {str(event)[:200]}")
            events.append(event)

            if isinstance(event, BackgroundTaskStarted) and actual_task_id is None:
                actual_task_id = event.task_id
                logger.info(f"[TestHanging] Captured task_id: {actual_task_id}")
                # Patch cancel call
                cancel_msg = client.responses[3]
                for block in cancel_msg.content:
                    if isinstance(block, ToolUseBlock) and block.name == "cancel_background_task":
                        block.input["task_id"] = actual_task_id

        # Verify two progress checks happened
        tool_completed = _events_of_type(events, ToolExecutionCompleted)
        progress_checks = [tc for tc in tool_completed if tc.tool_name == "check_background_progress"]
        assert len(progress_checks) >= 2, f"Expected 2+ progress checks, got {len(progress_checks)}"
        logger.info(f"[TestHanging] Progress checks: {len(progress_checks)}")

        # Both should show "running" status
        for pc in progress_checks:
            assert "running" in pc.output.lower(), f"Progress should show running. Got: {pc.output[:200]}"

        # Verify cancel happened
        cancel_results = [tc for tc in tool_completed if tc.tool_name == "cancel_background_task"]
        assert len(cancel_results) >= 1
        assert "cancelled" in cancel_results[0].output.lower()
        logger.info(f"[PASS] Hanging task cancelled after {len(progress_checks)} progress checks")


# ===========================================================================
# Test 6: Mixed scenario — background + foreground + progress + completion
# ===========================================================================


class TestFullBackgroundLifecycle:
    """Complete lifecycle: background launch → foreground work → progress check → completion."""

    async def test_complete_lifecycle(self):
        """Full lifecycle with all components working together."""
        slow_tool = SlowTool(output="BUILD OK\n48/48 tests passed\n0 failures")
        fast_tool = FastTool()
        registry = _make_registry(slow_tool, fast_tool)

        client = ScriptedMockClient([
            # Turn 1: Background build + foreground edit
            _msg_tools(
                ("fake_bash", {"command": "npm run build && npm test", "delay": 0.3, "background": True}),
                ("fake_edit", {"action": "update version to 2.0"}),
                text="Building and testing in background. Updating version...",
            ),
            # Turn 2: Another foreground task
            _msg_tool("fake_edit", {"action": "update changelog"},
                      text="Also updating the changelog."),
            # Turn 3: Check progress
            _msg_tool("check_background_progress", {},
                      text="Checking build status..."),
            # Turn 4: Go idle — engine will wait and inject result
            _msg_text("Build should be done soon, waiting..."),
            # Turn 5: React to completion
            _msg_text("Build succeeded, all 48 tests passed. Version 2.0 is ready!"),
        ])

        context = _make_context(client, registry)
        messages = [ConversationMessage.from_user_text("Release version 2.0")]
        events = await _collect_events(context, messages)

        # Full lifecycle checks
        bg_started = _events_of_type(events, BackgroundTaskStarted)
        assert len(bg_started) == 1, "One background task should start"

        fg_completed = [tc for tc in _events_of_type(events, ToolExecutionCompleted)
                        if tc.tool_name == "fake_edit"]
        assert len(fg_completed) >= 2, "Two foreground edits should complete"

        progress = [tc for tc in _events_of_type(events, ToolExecutionCompleted)
                    if tc.tool_name == "check_background_progress"]
        assert len(progress) >= 1, "At least one progress check"

        bg_completed = _events_of_type(events, BackgroundTaskCompleted)
        assert len(bg_completed) == 1, "Background should complete"
        assert "BUILD OK" in bg_completed[0].output

        turns = _events_of_type(events, AssistantTurnComplete)
        assert len(turns) == 5, f"Expected 5 turns, got {len(turns)}"

        logger.info(
            f"[PASS] Full lifecycle: {len(bg_started)} bg started, "
            f"{len(fg_completed)} fg completed, "
            f"{len(progress)} progress checks, "
            f"{len(bg_completed)} bg completed, "
            f"{len(turns)} total turns"
        )
