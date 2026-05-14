"""Built-in context recipes.

Adding a new recipe is a single step: write a new builder module under this
package that exposes a module-level ``<NAME>_RECIPE`` attribute referencing
a :class:`ContextRecipe`. :func:`register_builtin_recipes` walks every
submodule once at startup and registers every ``*_RECIPE`` it finds — no
edit to this file required (unless the recipe lives in this ``__init__``).
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes_registry import (
    ContextRecipe,
    RecipeRegistry,
)
from task_center.context_engine.scope import ContextScope

# ---------------------------------------------------------------------------
# Shared helper: latest summary text
# ---------------------------------------------------------------------------


def latest_summary_text(summaries: list[Any] | None) -> str:
    """Return the most recent summary string from a task's summaries list.

    Tasks carry a ``summaries`` list of dicts. Both the generator (dependency
    summaries) and evaluator (completed-task summaries) want the latest entry,
    preferring ``summary`` then ``outcome``, falling back to a placeholder.
    Centralized here so the policy can't drift between recipes.
    """
    if not summaries:
        return "(no summary recorded)"
    last = summaries[-1]
    if not isinstance(last, dict):
        return str(last)
    return str(last.get("summary") or last.get("outcome") or "(empty)")


# ---------------------------------------------------------------------------
# entry_executor recipe
# ---------------------------------------------------------------------------

ENTRY_EXECUTOR_ID = "entry_executor"
_ENTRY_EXECUTOR_REQUIRED_FIELDS = frozenset({"task_id"})


def _entry_executor_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    # Engine pre-validates required scope fields via ``assert_fields``; this
    # explicit guard makes the recipe self-defending under ``python -O`` where
    # ``assert`` would be stripped.
    if scope.task_id is None:
        raise ContextEngineError(
            "entry_executor requires scope.task_id."
        )
    task = deps.task_store.get_task(scope.task_id)
    if task is None:
        raise ContextEngineError(
            f"Entry task {scope.task_id!r} not found"
        )
    text = str(task.get("rendered_prompt") or "")
    block = ContextBlock(
        kind=ContextBlockKind.ENTRY_REQUEST,
        priority=ContextPriority.REQUIRED,
        text=text,
        source_id=scope.task_id,
        source_kind="task_center_task",
    )
    return ContextPacket(
        target_role="executor",
        target_id=scope.task_id,
        canonical_refs=ContextRefs(
            task_id=scope.task_id,
        ),
        blocks=[block],
        source_ids=[scope.task_id],
    )


ENTRY_EXECUTOR_RECIPE = ContextRecipe(
    id=ENTRY_EXECUTOR_ID,
    required_scope_fields=_ENTRY_EXECUTOR_REQUIRED_FIELDS,
    build=_entry_executor_build,
)


# ---------------------------------------------------------------------------
# Auto-discovery + explicit registration
# ---------------------------------------------------------------------------


def register_builtin_recipes() -> None:
    """Discover and register every built-in recipe. Idempotent.

    Walks every submodule of :mod:`task_center.context_engine.recipes` and
    registers any module attribute matching ``*_RECIPE`` that is a
    :class:`ContextRecipe` instance.  Also registers recipes defined directly
    in this ``__init__`` (e.g. ``ENTRY_EXECUTOR_RECIPE``) which the submodule
    scan cannot reach.
    """
    # Register recipes defined in this __init__ directly.
    RecipeRegistry.register(ENTRY_EXECUTOR_RECIPE)

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
