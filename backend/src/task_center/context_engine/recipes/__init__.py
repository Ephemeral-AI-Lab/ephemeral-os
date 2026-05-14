"""Built-in context recipes.

Adding a new recipe is a single step: write a new builder module under this
package that exposes a module-level ``<NAME>_RECIPE`` attribute referencing
a :class:`ContextRecipe`. :func:`register_builtin_recipes` walks every
submodule once at startup and registers every ``*_RECIPE`` it finds — no
edit to this file required.
"""

from __future__ import annotations

import importlib
import pkgutil

from task_center.context_engine.recipes_registry import (
    ContextRecipe,
    RecipeRegistry,
)


def register_builtin_recipes() -> None:
    """Discover and register every built-in recipe. Idempotent.

    Walks every submodule of :mod:`task_center.context_engine.recipes` and
    registers any module attribute matching ``*_RECIPE`` that is a
    :class:`ContextRecipe` instance. Helper submodules without a recipe
    attribute (e.g. ``summaries``, ``mission_episode``) are imported but
    contribute nothing to the registry.
    """
    for module_info in pkgutil.iter_modules(
        __path__, prefix=f"{__name__}."
    ):
        module = importlib.import_module(module_info.name)
        for attr_name in dir(module):
            if not attr_name.endswith("_RECIPE"):
                continue
            value = getattr(module, attr_name)
            if isinstance(value, ContextRecipe):
                RecipeRegistry.register(value)
