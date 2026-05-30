"""Agent-entry composer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center.agent_launch.entry_messages import AgentEntryMessages
from task_center.context_engine.skill_message import (
    _wrap_task_guidance,
    build_skill_message,
)
from task_center.context_engine.engine import ContextEngine
from task_center.context_engine.task_guidance import render_task_guidance
from task_center.context_engine.xml import render_context_xml

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.terminal_routing import TerminalToolRouter
    from task_center.context_engine.scope import ContextScope


@dataclass(frozen=True, slots=True)
class AgentEntryComposer:
    """Single launch entry point. Frozen so dependencies are explicit."""

    router: TerminalToolRouter
    engine: ContextEngine

    @classmethod
    def default(cls, engine: ContextEngine) -> AgentEntryComposer:
        from task_center._core.terminal_routing import TerminalToolRouter

        return cls(router=TerminalToolRouter(), engine=engine)

    def compose(self, *, base_agent_name: str, scope: ContextScope) -> AgentEntryMessages:
        selection = self.router.resolve(
            base_agent_name=base_agent_name,
            scope=scope,
            deps=self.engine.deps,
        )
        context = self.engine.build(selection.context_recipe, scope)

        agent_def = selection.agent_def
        context_message = render_context_xml(context)
        task_guidance = _wrap_task_guidance(render_task_guidance(context), agent_def)
        skill_message = build_skill_message(agent_def.skill, agent_def)
        return AgentEntryMessages(
            agent_def=agent_def,
            context=context_message,
            task_guidance=task_guidance,
            skill=skill_message,
            packet=context,
        )


__all__ = ["AgentEntryComposer"]
