"""Recipe registry — dispatches :class:`ContextRecipe` builders by id.

The registry is a *process-global* singleton. Tests should call
:meth:`RecipeRegistry.clear` in their teardown when registering ad-hoc
recipes; production startup calls
:func:`task_center.context_engine.recipes.register_builtin_recipes` exactly
once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from task_center.context_engine.errors import ContextEngineError
from task_center.context_engine.packet import ContextPacket
from task_center.context_engine.scope import ContextScope
from task_center.registry import Registry

if TYPE_CHECKING:  # pragma: no cover - typing-only; engine imports this module
    from task_center.context_engine.engine import ContextEngineDeps

# The engine imports this module at runtime to wire ``ContextRecipe``; the
# deps type is imported only for typing so the cycle stays static.
RecipeBuild = Callable[[ContextScope, "ContextEngineDeps"], ContextPacket]


@dataclass(frozen=True, slots=True)
class ContextRecipe:
    """One registered recipe."""

    id: str
    required_scope_fields: frozenset[str]
    build: RecipeBuild


class RecipeRegistry(Registry[ContextRecipe]):
    """Process-global recipe registry indexed by ``recipe.id``."""

    _registry: ClassVar[dict[str, ContextRecipe]] = {}
    _missing_exc: ClassVar[type[Exception]] = ContextEngineError

    @classmethod
    def register(cls, recipe: ContextRecipe) -> None:
        cls._put(recipe.id, recipe)
