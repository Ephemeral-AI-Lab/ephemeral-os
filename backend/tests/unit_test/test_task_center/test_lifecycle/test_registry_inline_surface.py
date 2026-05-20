"""Phase 5i regression test — recipe registry inline surface.

After deleting task_center/registry.py, pin the recipe registry public surface
so future edits cannot drop methods or change signatures silently.

Plan: .omc/plans/task-center-folder-reframe-20260514.md (lever #5)
"""

from __future__ import annotations

import inspect

from task_center.context_engine.recipes_registry import (
    RecipeRegistry,
)


_EXPECTED_SURFACE = {"register", "get", "has", "list_ids", "clear"}


def test_recipe_registry_public_surface_preserved() -> None:
    public = {name for name in vars(RecipeRegistry) if not name.startswith("_")}
    missing = _EXPECTED_SURFACE - public
    assert not missing, f"RecipeRegistry missing methods: {missing}"


def test_registry_get_raises_typed_error() -> None:
    from task_center.context_engine.core import ContextEngineError

    RecipeRegistry.clear()
    try:
        RecipeRegistry.get("nope")
    except ContextEngineError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected ContextEngineError")


def test_registry_register_signatures_distinct() -> None:
    # Recipe registers by single payload.
    r_sig = inspect.signature(RecipeRegistry.register)
    assert list(r_sig.parameters) == ["recipe"]


def test_old_registry_module_is_gone() -> None:
    import importlib

    try:
        importlib.import_module("task_center.registry")
    except ModuleNotFoundError:
        return
    raise AssertionError("task_center.registry should have been deleted")
