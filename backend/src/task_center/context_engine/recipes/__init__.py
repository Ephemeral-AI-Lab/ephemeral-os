"""Built-in context recipes.

Adding a new recipe is two steps: write the builder in its own module under
this package, then call :func:`register_builtin_recipes` (idempotent) at
startup. The engine itself owns no recipe knowledge.
"""

from __future__ import annotations

from task_center.context_engine.recipes.entry_executor import (
    ENTRY_EXECUTOR_RECIPE,
)
from task_center.context_engine.recipes.evaluator import (
    EVALUATOR_RECIPE,
)
from task_center.context_engine.recipes.generator import (
    GENERATOR_RECIPE,
)
from task_center.context_engine.recipes.helper import (
    ADVISOR_RECIPE,
    RESOLVER_RECIPE,
)
from task_center.context_engine.recipes.planner import (
    PLANNER_RECIPE,
)
from task_center.context_engine.recipes_registry import RecipeRegistry

_BUILTIN_RECIPES = (
    PLANNER_RECIPE,
    GENERATOR_RECIPE,
    EVALUATOR_RECIPE,
    ENTRY_EXECUTOR_RECIPE,
    ADVISOR_RECIPE,
    RESOLVER_RECIPE,
)


def register_builtin_recipes() -> None:
    """Register every built-in recipe. Idempotent — safe to call repeatedly."""
    for recipe in _BUILTIN_RECIPES:
        RecipeRegistry.register(recipe)
