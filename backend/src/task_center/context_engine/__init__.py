"""Context engine — flexible composition of agent / system / user prompts.

Public surface (per plan §3.1):

* :class:`ContextPacket`, :class:`ContextBlock` — typed packet schema.
* :class:`ContextScope` — the discriminated-union surface every recipe sees.
* :class:`ContextRecipe`, :class:`RecipeRegistry` — recipe dispatch.
* :class:`ContextEngine`, :class:`ContextEngineDeps` — packet builder.
* :class:`PromptRenderer`, :class:`MarkdownPromptRenderer` — pure renderer.
* :class:`ContextComposer`, :class:`LaunchBundle` — single launch entry point.
"""

from __future__ import annotations

from task_center.context_engine.errors import (
    AgentDefinitionValidationError,
    ContextEngineError,
    MissingContextRecipeError,
    RecipeScopeError,
)
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.scope import ContextScope

__all__ = [
    "AgentDefinitionValidationError",
    "ContextBlock",
    "ContextBlockKind",
    "ContextEngineError",
    "ContextPacket",
    "ContextPriority",
    "ContextRefs",
    "ContextScope",
    "MissingContextRecipeError",
    "RecipeScopeError",
]
