"""Agent-entry composer (formerly ``ContextComposer``).

Threads ``base_agent_name`` + :class:`ContextScope` through the terminal-tool
router, the context engine, and the renderer to produce an
:class:`AgentEntryMessages` — the four-row launch wire shape: system
(elsewhere), ``<context>``, ``<Task Guidance>``, and ``Load skill:``. Recipe
ids are looked up at call time; the task-guidance dispatch picks the builder
by exact agent name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center.agent_launch.entry_messages import AgentEntryMessages
from task_center.agent_launch.skill_message import (
    _wrap_task_guidance,
    build_skill_message,
)
from task_center.agent_launch.task_guidance_dispatch import (
    task_guidance_builder_for,
)
from task_center.context_engine.core import ContextEngine
from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.renderer import XmlPromptRenderer

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.terminal_tool_routing import TerminalToolRouter
    from task_center.context_engine.scope import ContextScope


_CONTEXT_CLOSER = "</context>"


@dataclass(frozen=True, slots=True)
class AgentEntryComposer:
    """Single launch entry point. Frozen so dependencies are explicit."""

    router: TerminalToolRouter
    engine: ContextEngine
    renderer: XmlPromptRenderer

    @classmethod
    def default(cls, engine: ContextEngine) -> AgentEntryComposer:
        # Lazy import: _core.terminal_tool_routing imports ContextEngineDeps from
        # context_engine.core, which would round-trip through agent_launch.
        from task_center._core.terminal_tool_routing import TerminalToolRouter

        return cls(
            router=TerminalToolRouter(),
            engine=engine,
            renderer=XmlPromptRenderer(),
        )

    def compose(
        self, *, base_agent_name: str, scope: ContextScope
    ) -> AgentEntryMessages:
        # ``router.resolve`` enforces context_recipe presence and returns an
        # effective copy whose terminal list is launch-specific.
        selection = self.router.resolve(
            base_agent_name=base_agent_name,
            scope=scope,
            deps=self.engine.deps,
        )
        packet = self.engine.build(selection.context_recipe, scope)
        store = self.engine.deps.context_packet_store
        context_packet_id = store.insert(packet) if store is not None else None

        agent_def = selection.agent_def
        rendered_body = self.renderer.render_context(packet)
        context_message = _wrap_context(rendered_body)

        builder = task_guidance_builder_for(agent_def.name)
        if builder is not None:
            prose = builder(agent_def=agent_def, packet=packet, scope=scope)
        else:
            prose = None
        task_guidance = _wrap_task_guidance(prose, agent_def)

        skill_message = build_skill_message(agent_def.skill, agent_def)
        return AgentEntryMessages(
            agent_def=agent_def,
            context=context_message,
            task_guidance=task_guidance,
            skill=skill_message,
            packet=packet,
            context_packet_id=context_packet_id,
        )


def _wrap_context(rendered_body: str) -> str:
    """Wrap rendered renderer body in ``<context>...</context>``.

    Empty body → ``""`` (no envelope; AC #11). Non-empty body → newline
    layout ``"<context>\\n{body}</context>\\n"``. The renderer already
    appends ``"\\n"`` to its body, so the closer sits on its own line.

    ``</context>`` inside user-supplied text would tear the envelope; we
    refuse rather than silently escape.
    """
    if not rendered_body:
        return ""
    if _CONTEXT_CLOSER in rendered_body:
        raise ContextEngineError(
            "Rendered context body contains structural closer '</context>'; "
            "rewrite the offending block body or use a different "
            "ContextBlockKind for this content."
        )
    return "<context>\n" + rendered_body + "</context>\n"


__all__ = ["AgentEntryComposer"]
