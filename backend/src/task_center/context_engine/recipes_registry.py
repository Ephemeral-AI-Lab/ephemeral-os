"""Recipe registry + the ``ContextRecipe`` value type.

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

from task_center.context_engine.core import ContextEngineError
from task_center.context_engine.packet import ContextPacket
from task_center.context_engine.scope import ContextScope

if TYPE_CHECKING:  # pragma: no cover - typing-only; engine imports this module
    from task_center.context_engine.core import ContextEngineDeps

# The engine imports this module at runtime to wire ``ContextRecipe``; the
# deps type is imported only for typing so the cycle stays static.
RecipeBuild = Callable[[ContextScope, "ContextEngineDeps"], ContextPacket]


@dataclass(frozen=True, slots=True)
class ContextRecipe:
    """One registered recipe."""

    id: str
    required_scope_fields: frozenset[str]
    build: RecipeBuild


class RecipeRegistry:
    """Process-global recipe registry indexed by ``recipe.id``."""

    _registry: ClassVar[dict[str, ContextRecipe]] = {}

    @classmethod
    def register(cls, recipe: ContextRecipe) -> None:
        cls._registry[recipe.id] = recipe

    @classmethod
    def get(cls, key: str) -> ContextRecipe:
        try:
            return cls._registry[key]
        except KeyError as exc:
            raise ContextEngineError(
                f"RecipeRegistry: {key!r} is not registered. "
                f"Known: {sorted(cls._registry)!r}"
            ) from exc

    @classmethod
    def has(cls, key: str) -> bool:
        return key in cls._registry

    @classmethod
    def list_ids(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()
