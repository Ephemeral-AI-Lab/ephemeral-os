"""Tests for prompt.runtime_prompt and background-related toolkit guidance."""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from prompt.environment import EnvironmentInfo
from prompt.runtime_prompt import (
    build_runtime_context_message,
    build_runtime_system_prompt,
    build_termination_condition_prompt,
)
from tools.builtins.background import make_background_toolkit
from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolRegistry, ToolResult
from tools.subagent import SubagentToolkit


class _EmptyInput(BaseModel):
    pass


class _DemoTool(BaseTool):
    name = "demo_tool"
    description = (
        "Inspect the current target and summarize the next safe action. "
        "Use only when the demo toolkit is active."
    )
    short_description = "Inspect the current target."
    input_model = _EmptyInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        return ToolResult(output="ok")


def test_termination_condition_prompt_returns_empty_without_terminal_tools():
    prompt = build_termination_condition_prompt()

    assert prompt == ""
    assert "<Toolkit Instructions>" not in prompt
    assert "<Available Skills>" not in prompt
    assert "<Background Tasks>" not in prompt


def test_subagent_toolkit_treats_spawned_workers_as_background():
    toolkit = SubagentToolkit()

    assert "workers always run in the background" in toolkit.instructions
    assert "Do not immediately block on the new task" in toolkit.instructions
    assert "call `check_background_progress(task_id=...)` only when live status will change your next action" in toolkit.instructions
    assert "Do not poll for reassurance or to satisfy an ordering ritual" in toolkit.instructions
    assert "stop polling that task id" in toolkit.instructions
    assert "Background status tools will only repeat the delivery envelope" in toolkit.instructions
    assert toolkit.get("run_subagent").short_description == "Spawn a subagent in the background."


def test_background_toolkit_says_progress_checks_are_decision_driven():
    toolkit = make_background_toolkit(["run_subagent"])

    assert "do not immediately block on the new task unless it is the only blocker left" in toolkit.instructions
    assert "call `check_background_progress` only when live status will change" in toolkit.instructions
    assert "Do not poll for reassurance" in toolkit.instructions
    assert (
        "Treat `delivered`, `[COMPLETED]`, `[ALREADY_COMPLETED]`, and `[NO TASKS RUNNING]` "
        "as terminal signals"
    ) in toolkit.instructions
    assert "background tools will only repeat the delivery envelope" in toolkit.instructions
    assert 'read_file_note(file_path="...")' in toolkit.instructions
    assert "Use `wait_for_background_task` when you are otherwise idle or blocked on the result" in toolkit.instructions


def test_termination_condition_prompt_omits_tool_call_notes_and_background_section():
    prompt = build_termination_condition_prompt(terminal_tools=["submit_plan"])

    assert "Tool Call Notes" not in prompt
    assert "<Background Tasks>" not in prompt
    assert "Background-capable tools: `run_subagent`." not in prompt
    assert "check_background_progress" not in prompt
    assert "<Termination Condition>" in prompt
    assert "- `submit_plan`" in prompt
    assert "WARNING: These are one-way exit tools." in prompt
    assert "Your lifecycle ends at that moment" in prompt
    assert "</Termination Condition>" in prompt


def test_termination_condition_prompt_only_renders_termination_condition():
    prompt = build_termination_condition_prompt(terminal_tools=["submit_plan"])

    assert "<Toolkit Instructions>" not in prompt
    assert "<Available Skills>" not in prompt
    assert "<Background Tasks>" not in prompt
    assert prompt.startswith("<Termination Condition>")
    assert "- `submit_plan`" in prompt


def test_tool_registry_remove_tools_filters_toolkits_too():
    registry = ToolRegistry()
    toolkit = BaseToolkit(
        name="demo",
        description="Demo toolkit",
        tools=[_DemoTool()],
    )
    registry.register_toolkit(toolkit)

    registry.remove_tools(["demo_tool"])

    assert registry.get("demo_tool") is None
    assert toolkit.list_tools() == []


def test_tool_registry_restrict_to_tools_filters_toolkits_too():
    registry = ToolRegistry()
    toolkit = BaseToolkit(
        name="demo",
        description="Demo toolkit",
        tools=[_DemoTool()],
    )
    registry.register_toolkit(toolkit)

    registry.restrict_to_tools(["missing_tool"])

    assert registry.get("demo_tool") is None
    assert registry.get_toolkit("demo") is None


def test_runtime_context_message_contains_environment(monkeypatch):
    monkeypatch.setattr(
        "prompt.runtime_prompt.get_environment_info",
        lambda cwd=None: EnvironmentInfo(
            os_name="Linux",
            os_version="6.8.0",
            platform_machine="x86_64",
            shell="zsh",
            cwd=str(cwd or "/tmp/project"),
            home_dir="/home/user",
            date="2026-04-16",
            python_version="3.12.0",
            is_git_repo=True,
            git_branch="main",
            hostname="testhost",
        ),
    )

    prompt = build_runtime_context_message(cwd="/tmp/project")

    assert "# Environment" in prompt
    assert "Linux 6.8.0" in prompt
    assert "branch: main" in prompt


def test_runtime_system_prompt_omits_reasoning_settings():
    settings = SimpleNamespace(system_prompt="base prompt", fast_mode=False, effort="medium", passes=1)

    prompt = build_runtime_system_prompt(settings, cwd="/tmp/project")

    assert "base prompt" in prompt
    assert "# Reasoning Settings" not in prompt
    assert "- Effort:" not in prompt
    assert "- Passes:" not in prompt
