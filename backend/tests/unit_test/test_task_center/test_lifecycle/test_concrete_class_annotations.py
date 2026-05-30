"""Phase 5f regression test - single-impl Protocol -> concrete class (lever #16).

After deleting AgentResolver/PromptRenderer/AttemptAgentLauncher
Protocols and re-typing their consumers with concrete-class refs,
this test pins:

1. The Protocol names are NOT importable (deletions stick).
2. The concrete class names ARE importable + instantiable.

Plan: .omc/plans/task-center-folder-reframe-20260514.md (lever #16a/b/c)
"""

from __future__ import annotations


def test_agent_resolver_protocol_gone() -> None:
    import task_center._core.terminal_routing as mod

    assert not hasattr(mod, "AgentResolver")


def test_prompt_renderer_protocol_gone() -> None:
    import task_center.context_engine.renderer as mod

    assert not hasattr(mod, "PromptRenderer")


def test_attempt_agent_launcher_protocol_gone() -> None:
    import task_center.attempt.launch as mod

    assert not hasattr(mod, "AttemptAgentLauncher")


def test_concrete_classes_importable() -> None:
    from task_center._core.terminal_routing import TerminalToolRouter
    from task_center.attempt.launch import EphemeralAttemptAgentLauncher
    from task_center.context_engine.renderer import XmlPromptRenderer

    assert TerminalToolRouter is not None
    assert XmlPromptRenderer is not None
    assert EphemeralAttemptAgentLauncher is not None
