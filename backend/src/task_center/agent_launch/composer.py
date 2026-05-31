"""Agent-entry composer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents import get_definition
from task_center.agent_launch.entry_messages import AgentEntryMessages
from task_center.context_engine.engine import (
    AgentDefinitionValidationError,
    MissingContextRecipeError,
)
from task_center.context_engine.skill_message import (
    wrap_task_guidance,
    build_skill_message,
)
from task_center.context_engine.engine import ContextEngine
from task_center.context_engine.task_guidance import render_task_guidance
from task_center.context_engine.xml import render_context_xml

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center.context_engine.scope import ContextScope


@dataclass(frozen=True, slots=True)
class AgentEntryComposer:
    """Single launch entry point. Frozen so dependencies are explicit."""

    engine: ContextEngine

    @classmethod
    def default(cls, engine: ContextEngine) -> AgentEntryComposer:
        return cls(engine=engine)

    def compose(self, *, base_agent_name: str, scope: ContextScope) -> AgentEntryMessages:
        agent_def = get_definition(base_agent_name)
        if agent_def is None:
            raise AgentDefinitionValidationError(
                f"Agent definition {base_agent_name!r} is not registered."
            )
        if not agent_def.context_recipe:
            raise MissingContextRecipeError(
                f"Agent {agent_def.name!r} has no context_recipe declared in "
                "frontmatter; it cannot be launched via AgentEntryComposer."
            )
        context = self.engine.build(agent_def.context_recipe, scope)
        context_message = render_context_xml(context)
        task_guidance = wrap_task_guidance(render_task_guidance(context), agent_def)
        skill_message = build_skill_message(agent_def.skill, agent_def)
        return AgentEntryMessages(
            agent_def=agent_def,
            context=context_message,
            task_guidance=task_guidance,
            skill=skill_message,
        )


__all__ = ["AgentEntryComposer"]
