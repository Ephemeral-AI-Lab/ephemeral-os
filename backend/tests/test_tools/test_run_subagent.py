"""Unit tests for run_subagent and the progress-provider plumbing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agents import get_definition as get_agent_definition
from engine.runtime.background_tasks import BackgroundTaskManager
from message.messages import (
    ConversationMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from tools.core.base import ToolExecutionContext, ToolResult
from tools.subagent.run_subagent_tool import (
    PEEK_MESSAGE_COUNT,
    format_last_n_messages,
    run_subagent,
)


# ---------------------------------------------------------------------------
# format_last_n_messages
# ---------------------------------------------------------------------------


def _make_messages() -> list[ConversationMessage]:
    return [
        ConversationMessage(
            role="user",
            content=[TextBlock(text="please refactor the parser")],
        ),
        ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="I should read the file first"),
                ToolUseBlock(name="read_file", input={"path": "src/parser.py"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="t1", content="def parse(s): return s.split()"
                )
            ],
        ),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="parser is trivial — adding type hints"),
                ToolUseBlock(
                    name="edit_file",
                    input={"path": "src/parser.py", "old": "def parse(s)", "new": "def parse(s: str) -> list[str]"},
                ),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="t2", content="OK")],
        ),
    ]


def test_format_last_n_messages_renders_each_block_type():
    out = format_last_n_messages(_make_messages(), n=PEEK_MESSAGE_COUNT)
    assert "[text]" in out
    assert "[think]" in out
    assert "[tool] read_file" in out
    assert "[result]" in out
    # Cap respected (should not blow past total cap).
    assert len(out) <= 2048


def test_format_last_n_messages_empty():
    assert format_last_n_messages([], n=5) == "(no messages yet)"


def test_format_last_n_messages_truncates_long_blocks():
    long_text = "x" * 5000
    msgs = [ConversationMessage(role="assistant", content=[TextBlock(text=long_text)])]
    out = format_last_n_messages(msgs, n=5)
    # The single rendered block must be truncated to ~_PEEK_BLOCK_CHAR_CAP.
    assert len(out) < 500
    assert "…" in out


def test_format_last_n_messages_only_returns_last_n():
    msgs = [
        ConversationMessage(
            role="assistant", content=[TextBlock(text=f"msg-{i}")]
        )
        for i in range(20)
    ]
    out = format_last_n_messages(msgs, n=3)
    assert "msg-19" in out
    assert "msg-17" in out
    assert "msg-16" not in out


# ---------------------------------------------------------------------------
# Builtin subagent definition is registered
# ---------------------------------------------------------------------------


def test_builtin_subagent_is_registered():
    defn = get_agent_definition("subagent")
    assert defn is not None
    assert defn.agent_type == "subagent"
    assert defn.name == "subagent"
    assert defn.system_prompt
    assert "subagent" not in defn.toolkits  # cannot nest


def test_run_subagent_tool_flags():
    assert run_subagent.supports_background is True
    assert getattr(run_subagent, "force_background", False) is True


# ---------------------------------------------------------------------------
# run_subagent end-to-end with a stub spawn_agent
# ---------------------------------------------------------------------------


class _StubAgent:
    def __init__(self, scripted_messages: list[ConversationMessage]) -> None:
        self._messages: list[ConversationMessage] = []
        self._scripted = scripted_messages
        self.total_usage = None
        # Used by the test to inspect that progress provider sees live state.
        self.peek_calls: list[str] = []

    async def run(self, prompt: str):
        for msg in self._scripted:
            self._messages.append(msg)
            await asyncio.sleep(0)  # yield to allow inter-message peeks
            yield ("event",)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_run_subagent_registers_provider_and_returns_final_text(monkeypatch):
    scripted = [
        ConversationMessage(role="user", content=[TextBlock(text="task")]),
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(name="read_file", input={"path": "x"}),
            ],
        ),
        ConversationMessage(
            role="assistant",
            content=[TextBlock(text="DONE: refactored module X")],
        ),
    ]

    stub_agent = _StubAgent(scripted)

    def _fake_spawn_agent(*args, **kwargs):
        return stub_agent

    monkeypatch.setattr(
        "engine.runtime.agent.spawn_agent", _fake_spawn_agent, raising=True
    )

    bg = BackgroundTaskManager()

    async def _noop_coro() -> ToolResult:
        return ToolResult(output="placeholder")

    bg.launch(
        task_id="bg_test",
        tool_name="run_subagent",
        tool_input={"prompt": "task"},
        coro=_noop_coro(),
        task_note="test",
    )

    class _StubConfig:
        cwd = Path("/tmp")

    ctx = ToolExecutionContext(
        cwd=Path("/tmp"),
        metadata={
            "session_config": _StubConfig(),
            "background_task_manager": bg,
            "background_task_id": "bg_test",
            "sandbox_id": "",
        },
    )

    result = await run_subagent.execute(
        run_subagent.input_model(prompt="task"), ctx
    )

    assert result.is_error is False
    assert "DONE" in result.output
    assert "refactored module X" in result.output

    # Provider should have been registered.
    tracked = bg._tasks["bg_test"]
    assert tracked.progress_provider is not None
    snapshot = tracked.progress_provider()
    assert isinstance(snapshot, str)
    assert "[text]" in snapshot or "[tool]" in snapshot


@pytest.mark.asyncio
async def test_run_subagent_missing_session_config_returns_error():
    ctx = ToolExecutionContext(cwd=Path("/tmp"), metadata={})
    result = await run_subagent.execute(
        run_subagent.input_model(prompt="task"), ctx
    )
    assert result.is_error is True
    assert "session_config" in result.output


@pytest.mark.asyncio
async def test_run_subagent_provider_error_is_caught():
    # Verify the bg manager swallows progress provider exceptions and surfaces
    # them as a [progress provider error] string instead of crashing.
    bg = BackgroundTaskManager()

    async def _noop_coro() -> ToolResult:
        return ToolResult(output="placeholder")

    bg.launch(
        task_id="bg_err",
        tool_name="x",
        tool_input={},
        coro=_noop_coro(),
        task_note="test",
    )

    def _bad_provider() -> str:
        raise RuntimeError("boom")

    bg.set_progress_provider("bg_err", _bad_provider)
    statuses = bg.get_status("bg_err")
    assert len(statuses) == 1
    assert "[progress provider error" in statuses[0]["output"]
    assert "boom" in statuses[0]["output"]
