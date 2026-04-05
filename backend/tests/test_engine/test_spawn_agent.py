"""Tests for spawn_agent toolkit instantiation and skill/toolkit awareness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ephemeralos.agents.types import AgentDefinition
from ephemeralos.tools.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult
from ephemeralos.tools.factory import ToolkitContext, register_toolkit_factory, _factories

from pydantic import BaseModel


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


class _DummyToolkit(BaseToolkit):
    def __init__(self, name: str = "dummy_toolkit") -> None:
        super().__init__(name=name, description="Dummy toolkit", tools=[_DummyTool()])


def _make_agent_def(**overrides: Any) -> AgentDefinition:
    """Create a minimal AgentDefinition with sensible defaults."""
    defaults = {
        "name": "test-agent",
        "description": "A test agent",
        "model_key": "test-model",
    }
    defaults.update(overrides)
    return AgentDefinition(**defaults)


@dataclass
class _FakeSessionConfig:
    """Minimal stand-in for SessionConfig."""

    cwd: str
    session_id: str = "test-session"
    model_override: str | None = None
    base_url_override: str | None = None
    api_key_override: str | None = None
    api_format_override: str | None = None
    external_api_client: Any = None

    def resolve_settings(self):
        from ephemeralos.config.settings import Settings
        return Settings(model="fallback-model")


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


@pytest.fixture()
def _patch_externals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Patch away external dependencies so spawn_agent doesn't need a real DB or API."""
    # Patch model_store
    fake_model_store = MagicMock()
    fake_model_store.is_available = False
    monkeypatch.setattr("ephemeralos.engine.agent.make_api_client", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("ephemeralos.engine.agent.make_hook_executor", lambda *a, **kw: None)
    monkeypatch.setattr(
        "ephemeralos.engine.agent.build_runtime_system_prompt",
        lambda *a, **kw: "default system prompt",
    )


# ---------------------------------------------------------------------------
# Tests — Toolkit instantiation
# ---------------------------------------------------------------------------


class TestToolkitInstantiation:
    """spawn_agent should instantiate toolkits via the factory before restricting."""

    def _spawn(self, tmp_path: Path, agent_def: AgentDefinition | None = None, sandbox_id: str | None = None):
        from ephemeralos.engine.agent import spawn_agent
        config = _FakeSessionConfig(cwd=str(tmp_path))
        return spawn_agent(config, [], agent_def=agent_def, sandbox_id=sandbox_id)

    @pytest.mark.usefixtures("_patch_externals", "_register_dummy_factory")
    def test_toolkit_created_via_factory(self, tmp_path: Path):
        """Toolkits listed in agent_def.toolkits should be created via the factory."""
        agent_def = _make_agent_def(toolkits=["dummy_toolkit"])
        agent = self._spawn(tmp_path, agent_def=agent_def)

        registry = agent.engine.tool_registry
        assert registry.get_toolkit("dummy_toolkit") is not None
        assert registry.get("dummy_tool") is not None

    @pytest.mark.usefixtures("_patch_externals", "_register_dummy_factory")
    def test_restrict_keeps_only_requested_toolkits(self, tmp_path: Path):
        """After instantiation, restrict_to_toolkits removes toolkits not in the list."""
        agent_def = _make_agent_def(toolkits=["dummy_toolkit"])
        agent = self._spawn(tmp_path, agent_def=agent_def)

        registry = agent.engine.tool_registry
        # discovery toolkit should be gone (not in agent_def.toolkits)
        assert registry.get_toolkit("discovery") is None
        # dummy_toolkit should remain
        assert registry.get_toolkit("dummy_toolkit") is not None

    @pytest.mark.usefixtures("_patch_externals")
    def test_no_toolkits_specified_keeps_defaults(self, tmp_path: Path):
        """When agent_def.toolkits is empty, all default toolkits remain."""
        agent_def = _make_agent_def(toolkits=[])
        agent = self._spawn(tmp_path, agent_def=agent_def)

        registry = agent.engine.tool_registry
        # discovery toolkit should still be there (no restriction applied)
        assert registry.get_toolkit("discovery") is not None

    @pytest.mark.usefixtures("_patch_externals")
    def test_no_agent_def_keeps_defaults(self, tmp_path: Path):
        """When no agent_def is provided, default toolkits remain."""
        agent = self._spawn(tmp_path)

        registry = agent.engine.tool_registry
        assert registry.get_toolkit("discovery") is not None

    @pytest.mark.usefixtures("_patch_externals")
    def test_unknown_toolkit_logs_warning(self, tmp_path: Path, caplog):
        """Requesting a toolkit with no factory should log a warning, not crash."""
        agent_def = _make_agent_def(toolkits=["nonexistent_toolkit"])
        agent = self._spawn(tmp_path, agent_def=agent_def)

        assert "No factory for toolkit" in caplog.text

    @pytest.mark.usefixtures("_patch_externals")
    def test_factory_error_logs_warning(self, tmp_path: Path, caplog):
        """If a factory raises, the agent should still spawn with remaining toolkits."""
        def _broken_factory(ctx: ToolkitContext) -> BaseToolkit:
            raise RuntimeError("factory broke")

        register_toolkit_factory("broken_toolkit", _broken_factory)
        agent_def = _make_agent_def(toolkits=["broken_toolkit"])

        # Should not raise
        agent = self._spawn(tmp_path, agent_def=agent_def)
        assert "Failed to create toolkit" in caplog.text

    @pytest.mark.usefixtures("_patch_externals", "_register_dummy_factory")
    def test_sandbox_id_creates_daytona_if_not_in_toolkits(self, tmp_path: Path):
        """When sandbox_id is provided but sandbox_operations isn't in agent_def.toolkits,
        DaytonaToolkit should still be registered as a fallback (but may be restricted away)."""
        # No agent_def — no restriction, sandbox tools should be registered
        with patch("ephemeralos.engine.agent.DaytonaToolkit", create=True) as mock_cls:
            # Simulate the import path within agent.py
            mock_tk = _DummyToolkit(name="sandbox_operations")
            mock_cls.return_value = mock_tk

            # Patch the import to succeed
            with patch.dict("sys.modules", {"ephemeralos.tools.daytona_toolkit": MagicMock(DaytonaToolkit=mock_cls)}):
                agent = self._spawn(tmp_path, sandbox_id="test-sandbox")
                registry = agent.engine.tool_registry
                assert registry.get_toolkit("sandbox_operations") is not None

    @pytest.mark.usefixtures("_patch_externals")
    def test_sandbox_operations_via_factory_not_double_registered(self, tmp_path: Path):
        """When sandbox_operations is in agent_def.toolkits AND sandbox_id is provided,
        it should only be registered once (via factory), not double-registered."""
        created_count = 0

        def _counting_factory(ctx: ToolkitContext) -> BaseToolkit:
            nonlocal created_count
            created_count += 1
            return _DummyToolkit(name="sandbox_operations")

        register_toolkit_factory("sandbox_operations", _counting_factory)
        agent_def = _make_agent_def(toolkits=["sandbox_operations"])

        agent = self._spawn(tmp_path, agent_def=agent_def, sandbox_id="test-sandbox")
        # Factory should create it once, fallback should skip (already registered)
        assert created_count == 1
        assert agent.engine.tool_registry.get_toolkit("sandbox_operations") is not None


# ---------------------------------------------------------------------------
# Tests — Skills & toolkit awareness in system prompt
# ---------------------------------------------------------------------------


class TestSystemPromptAwareness:
    """spawn_agent should inject skills and toolkit awareness into the system prompt."""

    def _spawn(self, tmp_path: Path, agent_def: AgentDefinition | None = None):
        from ephemeralos.engine.agent import spawn_agent
        config = _FakeSessionConfig(cwd=str(tmp_path))
        return spawn_agent(config, [], agent_def=agent_def)

    @pytest.mark.usefixtures("_patch_externals", "_register_dummy_factory")
    def test_toolkit_awareness_in_system_prompt(self, tmp_path: Path):
        """The system prompt should list available toolkits and their tool names."""
        agent_def = _make_agent_def(toolkits=["dummy_toolkit"])
        agent = self._spawn(tmp_path, agent_def=agent_def)

        assert "# Available Toolkits" in agent.engine.system_prompt
        assert "dummy_toolkit" in agent.engine.system_prompt
        assert "dummy_tool" in agent.engine.system_prompt

    @pytest.mark.usefixtures("_patch_externals")
    def test_toolkit_awareness_with_defaults(self, tmp_path: Path):
        """Default toolkits (discovery) should appear in awareness section."""
        agent = self._spawn(tmp_path)

        assert "# Available Toolkits" in agent.engine.system_prompt
        assert "discovery" in agent.engine.system_prompt

    @pytest.mark.usefixtures("_patch_externals")
    def test_skills_awareness_in_system_prompt(self, tmp_path: Path):
        """Skills listed in agent_def.skills should appear in the system prompt."""
        from ephemeralos.skills.types import SkillDefinition
        from ephemeralos.skills.registry import SkillRegistry

        fake_registry = SkillRegistry()
        fake_registry.register(SkillDefinition(
            name="test-skill",
            description="A test skill for unit tests",
            content="skill content here",
        ))

        agent_def = _make_agent_def(skills=["test-skill"])

        with patch("ephemeralos.engine.agent.load_skill_registry", return_value=fake_registry):
            agent = self._spawn(tmp_path, agent_def=agent_def)

        assert "# Available Skills" in agent.engine.system_prompt
        assert "test-skill" in agent.engine.system_prompt
        assert "A test skill for unit tests" in agent.engine.system_prompt

    @pytest.mark.usefixtures("_patch_externals")
    def test_no_skills_section_when_empty(self, tmp_path: Path):
        """When agent_def.skills is empty, no skills section should be added."""
        agent_def = _make_agent_def(skills=[])
        agent = self._spawn(tmp_path, agent_def=agent_def)

        assert "# Available Skills" not in agent.engine.system_prompt

    @pytest.mark.usefixtures("_patch_externals")
    def test_unknown_skill_silently_skipped(self, tmp_path: Path):
        """Skills not found in the registry should be silently skipped."""
        from ephemeralos.skills.registry import SkillRegistry

        fake_registry = SkillRegistry()  # empty — no skills registered
        agent_def = _make_agent_def(skills=["nonexistent-skill"])

        with patch("ephemeralos.engine.agent.load_skill_registry", return_value=fake_registry):
            agent = self._spawn(tmp_path, agent_def=agent_def)

        # No skills section since none were found
        assert "# Available Skills" not in agent.engine.system_prompt

    @pytest.mark.usefixtures("_patch_externals", "_register_dummy_factory")
    def test_custom_system_prompt_still_gets_awareness(self, tmp_path: Path):
        """Even when agent_def has a custom system_prompt, awareness sections are appended."""
        agent_def = _make_agent_def(
            system_prompt="You are a custom agent.",
            toolkits=["dummy_toolkit"],
        )
        agent = self._spawn(tmp_path, agent_def=agent_def)

        assert agent.engine.system_prompt.startswith("You are a custom agent.")
        assert "# Available Toolkits" in agent.engine.system_prompt
        assert "dummy_toolkit" in agent.engine.system_prompt


# ---------------------------------------------------------------------------
# Tests — Factory context propagation
# ---------------------------------------------------------------------------


class TestFactoryContext:
    """The ToolkitContext passed to factories should carry agent metadata."""

    @pytest.mark.usefixtures("_patch_externals")
    def test_factory_receives_agent_name_and_sandbox_id(self, tmp_path: Path):
        """The factory should receive agent_name and sandbox_id in context."""
        captured_ctx: list[ToolkitContext] = []

        def _capturing_factory(ctx: ToolkitContext) -> BaseToolkit:
            captured_ctx.append(ctx)
            return _DummyToolkit(name="capturing_toolkit")

        register_toolkit_factory("capturing_toolkit", _capturing_factory)
        agent_def = _make_agent_def(name="my-agent", toolkits=["capturing_toolkit"])

        from ephemeralos.engine.agent import spawn_agent
        config = _FakeSessionConfig(cwd=str(tmp_path))
        spawn_agent(config, [], agent_def=agent_def, sandbox_id="sb-123")

        assert len(captured_ctx) == 1
        assert captured_ctx[0].agent_name == "my-agent"
        assert captured_ctx[0].cwd == str(tmp_path)
        assert captured_ctx[0].metadata["sandbox_id"] == "sb-123"
