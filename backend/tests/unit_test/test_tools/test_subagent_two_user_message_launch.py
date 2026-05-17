"""Two-user-message launch shape for the explorer subagent.

Subagents have NO ContextScope and NO composer involvement (isolation
contract). The two-user-message shape is achieved directly inside
``run_subagent.py``: the caller's free-text task prompt becomes
``initial_messages[0]`` (user msg 1) and ``explorer_instruction().text``
becomes the spawn ``prompt`` argument (user msg 2).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents import (
    AgentDefinition,
    AgentKind,
    register_definition,
    unregister_definition,
)
from engine.agent.lifecycle import EphemeralRunResult
from message.messages import ConversationMessage
from task_center.context_engine.packet import (
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes.role_instruction import (
    explorer_instruction,
)
from tools._framework.core.base import ExecutionMetadata, ToolResult
from tools._framework.core.context import ToolExecutionContextService
from tools.subagent.run_subagent import run_subagent


@pytest.fixture
def fake_subagent_definition() -> Any:
    name = "test_explorer_for_two_user_message"
    register_definition(
        AgentDefinition(
            name=name,
            description="Test subagent used by two-user-message shape suite.",
            agent_type="subagent",
            agent_kind=AgentKind.EXPLORER,
            context_recipe="subagent_recipe",
            terminals=["submit_exploration_result"],
        )
    )
    try:
        yield name
    finally:
        unregister_definition(name)


def _make_context() -> ToolExecutionContextService:
    metadata = ExecutionMetadata()
    metadata.runtime_config = SimpleNamespace(cwd=Path("/tmp"))
    metadata.sandbox_id = ""
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


# ---------------------------------------------------------------------------
# explorer_instruction() text pin.
# ---------------------------------------------------------------------------


def test_explorer_instruction_text_pin() -> None:
    block = explorer_instruction()
    assert block.kind == ContextBlockKind.ROLE_INSTRUCTION.value
    assert block.priority == ContextPriority.REQUIRED
    text = block.text
    assert len(text.strip()) > 0
    # Anchor the "concrete findings" requirement — these substrings carry
    # the semantic; if they regress, the role instruction has drifted.
    lowered = text.lower()
    assert "file paths" in lowered
    assert "line numbers" in lowered


# ---------------------------------------------------------------------------
# Launch shape: user msg 1 = caller's prompt; user msg 2 = role text.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_subagent_launches_explorer_with_two_user_messages(
    monkeypatch: pytest.MonkeyPatch, fake_subagent_definition: str
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def _fake(*args: Any, **kwargs: Any) -> EphemeralRunResult:
        calls.append((args, kwargs))
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output="ok", is_error=False, does_terminate=True
            ),
            agent_name=fake_subagent_definition,
            event_count=1,
        )

    monkeypatch.setattr("engine.api.run_ephemeral_agent", _fake, raising=False)
    monkeypatch.setattr(
        "engine.agent.lifecycle.run_ephemeral_agent", _fake, raising=False
    )

    caller_prompt = "Find every callsite of foo.bar in the repo."
    context = _make_context()
    result = await run_subagent._entrypoint(
        agent_name=fake_subagent_definition,
        prompt=caller_prompt,
        context=context,
    )
    assert result.is_error is False

    assert len(calls) == 1
    args, kwargs = calls[0]
    # Spawn prompt (positional arg #2 after parent_cfg) is the role text,
    # NOT the caller's prompt.
    assert args[1] == explorer_instruction().text

    # initial_messages carries the caller's free-text prompt as user msg 1.
    initial = kwargs.get("initial_messages")
    assert isinstance(initial, list) and len(initial) == 1
    msg = initial[0]
    assert isinstance(msg, ConversationMessage)
    assert msg.role == "user"
    # Concatenate any text blocks defensively (ConversationMessage.content is
    # a list of content blocks).
    rendered_text = "".join(
        getattr(block, "text", "") for block in msg.content
    )
    assert rendered_text == caller_prompt


# ---------------------------------------------------------------------------
# Composer isolation guard: run_subagent must NOT call context.composer.
# ---------------------------------------------------------------------------


def test_run_subagent_does_not_call_composer_compose() -> None:
    import inspect as _inspect

    from tools.subagent import run_subagent as module

    source = _inspect.getsource(module)
    assert "composer.compose" not in source, (
        "run_subagent must NOT call composer.compose — subagents are "
        "isolated (no ContextScope) by design."
    )


# ---------------------------------------------------------------------------
# Static guard: enumerate every registered subagent class. Today this MUST
# be exactly one ('explorer'). A second subagent registration forces a
# revisit of run_subagent.py to decide whether the static
# explorer_instruction() text still applies or per-class dispatch is
# needed.
# ---------------------------------------------------------------------------


def test_only_one_subagent_class_registered() -> None:
    """Load the subagent profile directory directly; the registry is lazy.

    Bypasses the global registry (which is not auto-populated outside the
    runtime bootstrap path) and asks the loader to report exactly which
    subagent .md files exist on disk. Today the answer must be {explorer};
    adding a second subagent .md fails this test on the same PR.
    """
    from agents.definition.loader import load_agents_tree

    backend_root = Path(__file__).resolve().parents[3]
    subagent_root = backend_root / "src" / "agents" / "profile" / "subagent"
    assert subagent_root.is_dir(), f"Missing subagent profile root: {subagent_root}"
    loaded = list(load_agents_tree(subagent_root))
    subagents = [d for d in loaded if d.agent_type == "subagent"]
    assert len(subagents) == 1, (
        f"Expected exactly one subagent class on disk (explorer); got "
        f"{[d.name for d in subagents]}. Adding a new subagent class "
        "requires revisiting run_subagent.py to decide whether the static "
        "explorer_instruction() text still applies or per-class dispatch "
        "is needed."
    )
    assert subagents[0].name == "explorer"
