"""Tests for spawn_agent toolkit instantiation and skill/toolkit awareness.

These tests verify that:
1. Toolkits listed in agent_def.toolkits are instantiated via the factory
2. restrict_to_toolkits is applied correctly after instantiation
3. Skills and toolkit awareness sections are injected into the system prompt
4. Factory context propagates agent metadata correctly
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Stub out heavy dependencies that aren't installed in the test env
# ---------------------------------------------------------------------------

_STUB_MODULES = [
    "anthropic",
    "anthropic.types",
    "openai",
    "openai.types",
    "openai.types.chat",
    "httpx",
]


@pytest.fixture(autouse=True)
def _stub_missing_modules():
    """Insert stub modules so imports don't crash on missing anthropic/openai."""
    originals = {}
    for mod_name in _STUB_MODULES:
        if mod_name not in sys.modules:
            originals[mod_name] = None
            stub = types.ModuleType(mod_name)
            # Add common names that importing code expects
            stub.__dict__.setdefault("APIError", type("APIError", (Exception,), {}))
            stub.__dict__.setdefault("APIStatusError", type("APIStatusError", (Exception,), {}))
            stub.__dict__.setdefault("AsyncAnthropic", MagicMock)
            stub.__dict__.setdefault("AsyncOpenAI", MagicMock)
            sys.modules[mod_name] = stub
    yield
    for mod_name, original in originals.items():
        if original is None:
            sys.modules.pop(mod_name, None)


# ---------------------------------------------------------------------------
# Now safe to import project modules
# ---------------------------------------------------------------------------

from agents.types import AgentDefinition  # noqa: E402
from engine.runtime.agent import (  # noqa: E402
    _register_additional_allowed_tools,
    finalize_tool_registry_and_prompt,
)
from tools.core.base import (  # noqa: E402
    BaseTool,
    BaseToolkit,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)
from tools.core.factory import ToolkitContext, _factories, register_toolkit_factory  # noqa: E402
from tools.submission.toolkit import SubmissionToolkit  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DummyInput(BaseModel):
    arg: str = ""


class _DummyTool(BaseTool):
    name = "dummy_tool"
    description = "A dummy tool for testing"
    input_model = _DummyInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        return ToolResult(output="ok")


class _DummyTool2(BaseTool):
    name = "dummy_tool_2"
    description = "Another dummy tool"
    input_model = _DummyInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        return ToolResult(output="ok2")


class _DummyToolkit(BaseToolkit):
    def __init__(self, name: str = "dummy_toolkit", tools=None) -> None:
        super().__init__(
            name=name, description=f"Dummy toolkit: {name}", tools=tools or [_DummyTool()]
        )


def _make_agent_def(**overrides: Any) -> AgentDefinition:
    """Create a minimal AgentDefinition with sensible defaults."""
    defaults = {
        "name": "test-agent",
        "description": "A test agent",
        "model_key": "test-model",
    }
    defaults.update(overrides)
    return AgentDefinition(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_factories():
    """Snapshot and restore the global factory registry around each test."""
    original = dict(_factories)
    yield
    _factories.clear()
    _factories.update(original)


@pytest.fixture()
def _register_dummy_factory():
    """Register a 'dummy_toolkit' factory for tests."""
    register_toolkit_factory("dummy_toolkit", lambda ctx: _DummyToolkit())


# ---------------------------------------------------------------------------
# Tests — ToolRegistry.restrict_to_toolkits (unit)
# ---------------------------------------------------------------------------


class TestRestrictToToolkits:
    """Verify restrict_to_toolkits keeps only requested toolkits."""

    def test_restrict_keeps_named_toolkits(self):
        registry = ToolRegistry()
        tk_a = _DummyToolkit(name="alpha")
        tk_b = _DummyToolkit(name="beta", tools=[_DummyTool2()])
        registry.register_toolkit(tk_a)
        registry.register_toolkit(tk_b)

        registry.restrict_to_toolkits(["alpha"])

        assert registry.get_toolkit("alpha") is not None
        assert registry.get_toolkit("beta") is None
        assert registry.get("dummy_tool") is not None
        assert registry.get("dummy_tool_2") is None

    def test_restrict_to_empty_clears_all(self):
        registry = ToolRegistry()
        registry.register_toolkit(_DummyToolkit(name="alpha"))

        registry.restrict_to_toolkits([])

        assert len(registry.list_tools()) == 0
        assert len(registry.list_toolkits()) == 0

    def test_restrict_to_unknown_clears_all(self):
        registry = ToolRegistry()
        registry.register_toolkit(_DummyToolkit(name="alpha"))

        registry.restrict_to_toolkits(["nonexistent"])

        assert len(registry.list_tools()) == 0

    def test_restrict_to_tools_prunes_toolkits_too(self):
        registry = ToolRegistry()
        tk_a = _DummyToolkit(name="alpha")
        tk_b = _DummyToolkit(name="beta", tools=[_DummyTool2()])
        registry.register_toolkit(tk_a)
        registry.register_toolkit(tk_b)

        registry.restrict_to_tools(["dummy_tool_2"])

        assert registry.get("dummy_tool") is None
        assert registry.get("dummy_tool_2") is not None
        assert registry.get_toolkit("alpha") is None
        assert registry.get_toolkit("beta") is not None


# ---------------------------------------------------------------------------
# Tests — Toolkit factory instantiation logic
# ---------------------------------------------------------------------------


class TestToolkitFactoryInstantiation:
    """Test the toolkit instantiation logic extracted from spawn_agent."""

    def _apply_toolkit_instantiation(
        self,
        agent_def: AgentDefinition | None,
        sandbox_id: str | None = None,
    ) -> ToolRegistry:
        """Replicate the toolkit instantiation logic from spawn_agent."""
        from tools import create_default_tool_registry
        from tools.core.factory import create_toolkit, has_factory

        tool_registry = create_default_tool_registry()
        agent_name = agent_def.name if agent_def else "default"

        toolkit_ctx = ToolkitContext(
            metadata={
                "agent_name": agent_name,
                "cwd": "/tmp/test",
                "sandbox_id": sandbox_id or "",
            },
        )

        if agent_def and agent_def.toolkits:
            for tk_name in agent_def.toolkits:
                if tool_registry.get_toolkit(tk_name) is not None:
                    continue
                if has_factory(tk_name):
                    try:
                        tk = create_toolkit(tk_name, toolkit_ctx)
                        tool_registry.register_toolkit(tk)
                    except Exception:
                        pass
                # (unknown toolkit warning omitted for brevity)

        if agent_def and agent_def.toolkits:
            tool_registry.restrict_to_toolkits(agent_def.toolkits)

        return tool_registry

    @pytest.mark.usefixtures("_register_dummy_factory")
    def test_toolkit_created_via_factory(self):
        agent_def = _make_agent_def(toolkits=["dummy_toolkit"])
        registry = self._apply_toolkit_instantiation(agent_def)

        assert registry.get_toolkit("dummy_toolkit") is not None
        assert registry.get("dummy_tool") is not None

    @pytest.mark.usefixtures("_register_dummy_factory")
    def test_restrict_removes_non_requested_toolkits(self):
        agent_def = _make_agent_def(toolkits=["dummy_toolkit"])
        registry = self._apply_toolkit_instantiation(agent_def)

        # discovery toolkit should be removed by restrict
        assert registry.get_toolkit("discovery") is None
        assert registry.get_toolkit("dummy_toolkit") is not None

    def test_no_toolkits_no_restriction(self):
        """When agent_def.toolkits=[], restrict_toolkits([]) is NOT called, so registry stays as-is."""
        agent_def = _make_agent_def(toolkits=[])
        registry = self._apply_toolkit_instantiation(agent_def)

        # Default registry starts empty; empty list doesn't trigger restriction
        assert len(registry.list_toolkits()) == 0

    def test_no_agent_def_no_restriction(self):
        """When agent_def is None, restriction is never applied."""
        registry = self._apply_toolkit_instantiation(None)

        # Default registry starts empty; no restriction applied
        assert len(registry.list_toolkits()) == 0

    def test_unknown_toolkit_does_not_crash(self):
        agent_def = _make_agent_def(toolkits=["nonexistent_toolkit"])
        # Should not raise
        registry = self._apply_toolkit_instantiation(agent_def)
        # Everything restricted away since nonexistent was never registered
        assert len(registry.list_toolkits()) == 0

    def test_factory_error_does_not_crash(self):
        def _broken_factory(ctx: ToolkitContext) -> BaseToolkit:
            raise RuntimeError("factory broke")

        register_toolkit_factory("broken_toolkit", _broken_factory)
        agent_def = _make_agent_def(toolkits=["broken_toolkit"])

        # Should not raise
        registry = self._apply_toolkit_instantiation(agent_def)
        assert registry.get_toolkit("broken_toolkit") is None

    def test_already_registered_toolkit_not_duplicated(self):
        """If a toolkit is already in the registry, the factory should not be called again."""
        call_count = 0

        def _counting_factory(ctx: ToolkitContext) -> BaseToolkit:
            nonlocal call_count
            call_count += 1
            return _DummyToolkit(name="counted_toolkit")

        register_toolkit_factory("counted_toolkit", _counting_factory)

        from tools import create_default_tool_registry
        from tools.core.factory import create_toolkit as _ct, has_factory as _hf

        registry = create_default_tool_registry()
        # Pre-register so the factory shouldn't be called
        registry.register_toolkit(_DummyToolkit(name="counted_toolkit"))

        agent_def = _make_agent_def(toolkits=["counted_toolkit"])

        # Simulate the loop
        for tk_name in agent_def.toolkits:
            if registry.get_toolkit(tk_name) is not None:
                continue
            if _hf(tk_name):
                tk = _ct(tk_name, ToolkitContext())
                registry.register_toolkit(tk)

        assert call_count == 0

    @pytest.mark.usefixtures("_register_dummy_factory")
    def test_multiple_toolkits(self):
        register_toolkit_factory(
            "second_toolkit",
            lambda ctx: _DummyToolkit(name="second_toolkit", tools=[_DummyTool2()]),
        )

        agent_def = _make_agent_def(toolkits=["dummy_toolkit", "second_toolkit"])
        registry = self._apply_toolkit_instantiation(agent_def)

        assert registry.get_toolkit("dummy_toolkit") is not None
        assert registry.get_toolkit("second_toolkit") is not None
        assert registry.get("dummy_tool") is not None
        assert registry.get("dummy_tool_2") is not None
        # discovery should be restricted away
        assert registry.get_toolkit("discovery") is None

    @pytest.mark.usefixtures("_register_dummy_factory")
    def test_allowed_tools_add_tools_from_other_toolkits(self):
        register_toolkit_factory(
            "second_toolkit",
            lambda ctx: _DummyToolkit(name="second_toolkit", tools=[_DummyTool2()]),
        )

        agent_def = _make_agent_def(
            toolkits=["dummy_toolkit"],
            allowed_tools=["dummy_tool_2"],
        )
        registry = self._apply_toolkit_instantiation(agent_def)
        _register_additional_allowed_tools(
            registry,
            agent_def.allowed_tools,
            ToolkitContext(),
        )

        assert registry.get("dummy_tool") is not None
        assert registry.get("dummy_tool_2") is not None
        assert registry.get_toolkit("dummy_toolkit") is not None
        assert registry.get_toolkit("second_toolkit") is not None

    def test_role_policy_hides_non_summary_submission_tools_for_developer(self):
        registry = ToolRegistry()
        registry.register_toolkit(SubmissionToolkit.from_context(object()))

        prompt, _ = finalize_tool_registry_and_prompt(
            registry,
            "Base prompt.",
            role="developer",
            terminal_tools={"submit_task_summary"},
        )

        assert registry.get("submit_task_summary") is not None
        assert registry.get("draft_task_plan") is None
        assert registry.get("submit_task_plan") is None
        assert registry.get("declare_blocker") is None
        assert "1. submit_task_summary - Submit task outcome." in prompt
        assert "1. submit_task_plan - Submit a task plan." not in prompt
        assert "1. draft_task_plan - Validate a draft task plan." not in prompt

    def test_role_policy_keeps_plan_tools_for_planner(self):
        registry = ToolRegistry()
        registry.register_toolkit(SubmissionToolkit.from_context(object()))

        prompt, _ = finalize_tool_registry_and_prompt(
            registry,
            "Base prompt.",
            role="planner",
            terminal_tools={"submit_task_plan"},
        )

        assert registry.get("submit_task_plan") is not None
        assert registry.get("submit_task_summary") is None
        assert registry.get("declare_blocker") is None
        assert registry.get("draft_task_plan") is None
        assert "1. submit_task_plan - Submit a task plan." in prompt
        assert "draft_task_plan" not in prompt
        assert "1. submit_task_summary - Submit task outcome." not in prompt

    def test_blocked_tools_apply_after_role_policy(self):
        registry = ToolRegistry()
        registry.register_toolkit(SubmissionToolkit.from_context(object()))

        prompt, _ = finalize_tool_registry_and_prompt(
            registry,
            "Base prompt.",
            role="planner",
            blocked_tools=["draft_task_plan"],
            terminal_tools={"submit_task_plan"},
        )

        assert registry.get("submit_task_plan") is not None
        assert registry.get("draft_task_plan") is None
        assert registry.get("submit_task_summary") is None
        assert "1. submit_task_plan - Submit a task plan." in prompt
        assert "1. draft_task_plan - Validate a draft task plan." not in prompt


# ---------------------------------------------------------------------------
# Tests — Factory context propagation
# ---------------------------------------------------------------------------


class TestFactoryContext:
    """The ToolkitContext passed to factories should carry agent metadata."""

    def test_factory_receives_agent_name_and_sandbox_id(self):
        captured_ctx: list[ToolkitContext] = []

        def _capturing_factory(ctx: ToolkitContext) -> BaseToolkit:
            captured_ctx.append(ctx)
            return _DummyToolkit(name="capturing_toolkit")

        register_toolkit_factory("capturing_toolkit", _capturing_factory)
        agent_def = _make_agent_def(name="my-agent", toolkits=["capturing_toolkit"])

        from tools import create_default_tool_registry
        from tools.core.factory import create_toolkit, has_factory

        registry = create_default_tool_registry()
        ctx = ToolkitContext(
            metadata={
                "agent_name": "my-agent",
                "cwd": "/tmp/test",
                "sandbox_id": "sb-123",
            },
        )

        for tk_name in agent_def.toolkits:
            if registry.get_toolkit(tk_name) is None and has_factory(tk_name):
                tk = create_toolkit(tk_name, ctx)
                registry.register_toolkit(tk)

        assert len(captured_ctx) == 1
        assert captured_ctx[0].metadata["agent_name"] == "my-agent"
        assert captured_ctx[0].metadata["cwd"] == "/tmp/test"
        assert captured_ctx[0].metadata["sandbox_id"] == "sb-123"


# ---------------------------------------------------------------------------
# Tests — System prompt awareness injection
# ---------------------------------------------------------------------------


class TestSystemPromptAwareness:
    """Test that awareness sections are correctly built and appended."""

    def _build_awareness(
        self,
        tool_registry: ToolRegistry,
        agent_def: AgentDefinition | None = None,
        base_prompt: str = "Base system prompt.",
    ) -> str:
        """Replicate the awareness injection logic from spawn_agent."""
        system_prompt = base_prompt

        awareness_sections: list[str] = []

        # Skills awareness
        if agent_def and agent_def.skills:
            from skills.core.registry import SkillRegistry
            from skills.core.types import SkillDefinition

            registry = SkillRegistry()
            registry.register(
                SkillDefinition(
                    name="code-review",
                    description="Review code for quality",
                    content="...",
                    source="test",
                )
            )
            registry.register(
                SkillDefinition(
                    name="test-gen",
                    description="Generate tests",
                    content="...",
                    source="test",
                )
            )

            skill_lines = []
            for slug in agent_def.skills:
                skill = registry.get(slug)
                if skill:
                    skill_lines.append(f"- **{skill.name}**: {skill.description}")
            if skill_lines:
                awareness_sections.append(
                    "# Available Skills\n\n"
                    "The following skills are available via the `skill` tool. "
                    "When a task matches a skill, invoke it to load detailed instructions.\n\n"
                    + "\n".join(skill_lines)
                )

        # Toolkit awareness
        registered_toolkits = tool_registry.list_toolkits()
        if registered_toolkits:
            tk_lines = []
            for tk in registered_toolkits:
                tool_names = ", ".join(tk.tool_names())
                tk_lines.append(f"- **{tk.name}**: {tool_names}")
            awareness_sections.append(
                "# Available Toolkits\n\n"
                "You have the following toolkits and tools available:\n\n" + "\n".join(tk_lines)
            )

        if awareness_sections:
            system_prompt = system_prompt + "\n\n" + "\n\n".join(awareness_sections)

        return system_prompt

    def test_toolkit_awareness_lists_tools(self):
        registry = ToolRegistry()
        registry.register_toolkit(_DummyToolkit(name="sandbox_operations"))

        prompt = self._build_awareness(registry)

        assert "# Available Toolkits" in prompt
        assert "sandbox_operations" in prompt
        assert "dummy_tool" in prompt

    def test_no_toolkits_no_section(self):
        registry = ToolRegistry()

        prompt = self._build_awareness(registry)

        assert "# Available Toolkits" not in prompt
        assert prompt == "Base system prompt."

    def test_skills_awareness_lists_matching_skills(self):
        registry = ToolRegistry()
        agent_def = _make_agent_def(skills=["code-review"])

        prompt = self._build_awareness(registry, agent_def=agent_def)

        assert "# Available Skills" in prompt
        assert "code-review" in prompt
        assert "Review code for quality" in prompt

    def test_unknown_skill_silently_skipped(self):
        registry = ToolRegistry()
        agent_def = _make_agent_def(skills=["nonexistent-skill"])

        prompt = self._build_awareness(registry, agent_def=agent_def)

        # No skills section since none were found in the registry
        assert "# Available Skills" not in prompt

    def test_no_skills_no_section(self):
        registry = ToolRegistry()
        agent_def = _make_agent_def(skills=[])

        prompt = self._build_awareness(registry, agent_def=agent_def)

        assert "# Available Skills" not in prompt

    def test_custom_system_prompt_gets_awareness_appended(self):
        registry = ToolRegistry()
        registry.register_toolkit(_DummyToolkit(name="my_tools"))

        prompt = self._build_awareness(
            registry,
            base_prompt="You are a custom agent.",
        )

        assert prompt.startswith("You are a custom agent.")
        assert "# Available Toolkits" in prompt
        assert "my_tools" in prompt

    def test_both_skills_and_toolkits(self):
        registry = ToolRegistry()
        registry.register_toolkit(_DummyToolkit(name="my_tools"))
        agent_def = _make_agent_def(skills=["code-review", "test-gen"])

        prompt = self._build_awareness(registry, agent_def=agent_def)

        assert "# Available Skills" in prompt
        assert "# Available Toolkits" in prompt
        assert "code-review" in prompt
        assert "test-gen" in prompt
        assert "my_tools" in prompt

    def test_multiple_toolkits_all_listed(self):
        registry = ToolRegistry()
        registry.register_toolkit(_DummyToolkit(name="alpha"))
        registry.register_toolkit(_DummyToolkit(name="beta", tools=[_DummyTool2()]))

        prompt = self._build_awareness(registry)

        assert "alpha" in prompt
        assert "beta" in prompt
        assert "dummy_tool" in prompt
        assert "dummy_tool_2" in prompt


# ---------------------------------------------------------------------------
# Tests — to_api_schema includes factory-created tools
# ---------------------------------------------------------------------------


class TestApiSchemaOutput:
    """Verify that factory-created toolkits produce correct API schemas."""

    @pytest.mark.usefixtures("_register_dummy_factory")
    def test_factory_toolkit_tools_appear_in_schema(self):
        from tools import create_default_tool_registry
        from tools.core.factory import create_toolkit

        registry = create_default_tool_registry()
        tk = create_toolkit("dummy_toolkit", ToolkitContext())
        registry.register_toolkit(tk)
        registry.restrict_to_toolkits(["dummy_toolkit"])

        schema = registry.to_api_schema()
        tool_names = [t["name"] for t in schema]

        assert "dummy_tool" in tool_names
        # discovery tools should be gone after restriction
        assert all(t["name"] != "skill" for t in schema)

    @pytest.mark.usefixtures("_register_dummy_factory")
    def test_schema_has_correct_shape(self):
        from tools.core.factory import create_toolkit

        tk = create_toolkit("dummy_toolkit", ToolkitContext())
        tool = tk.list_tools()[0]
        schema = tool.to_api_schema()

        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema
        assert schema["name"] == "dummy_tool"
