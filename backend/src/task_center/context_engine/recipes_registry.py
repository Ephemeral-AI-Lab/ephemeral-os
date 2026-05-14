"""Recipe registry + class-based recipe base.

The registry is a *process-global* singleton. Tests should call
:meth:`RecipeRegistry.clear` in their teardown when registering ad-hoc
recipes; production startup calls
:func:`task_center.context_engine.recipes.register_builtin_recipes` exactly
once.

Two recipe shapes are supported:

- :class:`ContextRecipe` — frozen dataclass with an ``id``, required
  scope fields, and a ``build`` callable. Used by every built-in recipe
  today.
- :class:`Recipe` — abstract base class exposing the same contract via
  class attributes + a ``build`` method, plus a ``to_context_recipe``
  adapter. New recipes that benefit from OOP composition (shared
  block-inheritance helpers, default scope-validation) can inherit from
  :class:`Recipe`; the adapter keeps registration uniform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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


class Recipe(ABC):
    """OOP base class for context recipes.

    Subclasses set the :attr:`ID` and :attr:`REQUIRED_SCOPE_FIELDS` class
    attributes and implement :meth:`build`. Use :meth:`to_context_recipe`
    to adapt an instance to the legacy :class:`ContextRecipe` shape that
    :class:`RecipeRegistry` accepts.

    Subclasses can override or call ``inherit_parent_blocks`` to share the
    helper-recipe parent-frame inheritance logic instead of copy-pasting
    it across each helper builder.
    """

    ID: ClassVar[str]
    REQUIRED_SCOPE_FIELDS: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def build(
        self, scope: ContextScope, deps: ContextEngineDeps
    ) -> ContextPacket:
        """Construct the :class:`ContextPacket` for one agent spawn."""

    def to_context_recipe(self) -> ContextRecipe:
        """Wrap this instance for registration with :class:`RecipeRegistry`."""
        return ContextRecipe(
            id=type(self).ID,
            required_scope_fields=type(self).REQUIRED_SCOPE_FIELDS,
            build=self.build,
        )


class RecipeRegistry:
    """Process-global recipe registry indexed by ``recipe.id``.

    Accepts both :class:`ContextRecipe` instances and :class:`Recipe`
    subclass instances. :class:`Recipe` instances are wrapped via
    ``to_context_recipe()`` so the registry's internal type stays
    homogeneous.
    """

    _registry: ClassVar[dict[str, ContextRecipe]] = {}

    @classmethod
    def register(cls, recipe: ContextRecipe | Recipe) -> None:
        if isinstance(recipe, Recipe):
            recipe = recipe.to_context_recipe()
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
