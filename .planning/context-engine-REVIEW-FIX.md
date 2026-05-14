---
status: fixes_applied
target: backend/src/task_center/context_engine
fixes_applied: 7
fixes_skipped: 2
loc_before: 1718
loc_after: 1544
loc_delta: -174 (-10%)
tests_run: backend/tests/unit_test/test_task_center/test_context_engine/ (61), test_task_center/* + test_tools/* (415)
tests_status: all green
preexisting_failures_unrelated: 12 (task_center.agent_routing.predicates module missing — parallel refactor, not in scope)
---

# Context-Engine Review — Fix Summary

## Fixes applied

### WR-02 — Deleted dead `Recipe` ABC
**File:** `recipes_registry.py` (117 → 66 LOC, −51)

Removed the `Recipe(ABC)` class, its `to_context_recipe()` adapter, the `isinstance(recipe, Recipe)` branch in `RecipeRegistry.register`, and the surrounding multi-paragraph docstring justifying the two-shape design. Verified zero subclasses anywhere (`grep` repo-wide).

### WR-01 — Extracted shared frame helpers to `recipes/_shared.py`
**Files:** `recipes/_shared.py` (new, 135 LOC), `recipes/generator.py` (257 → 120, −137), `recipes/evaluator.py` (updated import), `recipes/planner.py` (updated import), `recipes/attempt_landscape.py` (updated import)

Moved out of `generator.py`:
- `mission_episode_blocks` + `_episode_goal_block` + `_mission_goal_block` + `_previous_episode_result_blocks`
- The four heading constants (`MISSION_EPISODE_HEADING`, `MISSION_HEADING`, `CURRENT_EPISODE_HEADING`, `PREVIOUS_EPISODE_RESULTS_HEADING`)

Moved out of `recipes/__init__.py`:
- `latest_summary_text` (was the only reason `recipes/__init__.py` had to be imported by sibling modules)

**Result:** `evaluator.py`, `planner.py`, `attempt_landscape.py`, `generator.py` no longer import from each other. All shared frame logic now lives in one cohesive module.

### WR-06 — Moved advisor/resolver helper recipes to `recipes/helper.py`
**Files:** `recipes/helper.py` (new, 101 LOC), `recipes/planner.py` (220 → 66, −154 cumulative with WR-01/WR-03)

`planner.py` no longer mixes the planner recipe with unrelated parent-inheritance helpers (advisor + resolver). The helper recipes — including `_build_helper_packet`, `_advisor_build`, `_resolver_build`, `demote_priority`, `ADVISOR_RECIPE`, `RESOLVER_RECIPE` — live in their own module, auto-discovered by `register_builtin_recipes()` just like other recipes.

### WR-05 — Moved `entry_executor` to its own module
**Files:** `recipes/entry_executor.py` (new, 46 LOC), `recipes/__init__.py` (126 → 29, −97)

`recipes/__init__.py` is now pure auto-discovery (no special-case manual registration, no recipe definitions, no shared helpers). The auto-discovery walker picks up `entry_executor.py` along with the other recipes — the `RecipeRegistry.register(ENTRY_EXECUTOR_RECIPE)` workaround for "things in __init__.py" is gone.

### WR-03 — Dropped redundant `if scope.X is None` guards
**Files:** `recipes/evaluator.py`, `recipes/generator.py`, `recipes/planner.py`, `recipes/helper.py` (new file inherits the simpler shape), `recipes/entry_executor.py` (new file inherits the simpler shape)

Removed 5 instances of the duplicated `if scope.X is None: raise ContextEngineError(...)` guard with its misleading "`python -O`" justification. The engine's `scope.assert_fields(recipe.required_scope_fields)` (called before `recipe.build()`) is the single source of truth for required-field enforcement. `assert_fields` uses `raise`, not `assert`, so `-O` stripping was never a real concern.

### WR-07 — (partially) Consolidated advisor/resolver builders
**File:** `recipes/helper.py`

Both `_advisor_build` and `_resolver_build` are now one-line shims around `_build_helper_packet(target_role=..., scope=..., deps=...)`. Kept as named functions because `test_helper_recipes.py` calls them directly — collapsing further would require changing tests. The named functions are now genuinely one line each (was 3 lines with `return` on its own line).

### WR-04 — Rewrote `ContextScope` module docstring
**File:** `scope.py` (108 → 104 LOC)

Replaced the misleading "static error instead of runtime ``RecipeScopeError``" claim with an accurate description: "omitting one raises ``TypeError`` at call time, and strict mypy will narrow the kwargs to their declared ``str`` types."

## Fixes deferred (not applied)

### WR-08 — Simplify `HeadingTemplate` to `dict[str, str]`
**Why deferred:** The module docstring documents a public API contract — _"Templates are plain ``str.format`` strings receiving the block kind and optional metadata"_ — and the `register()` method is part of that contract. While no current code uses placeholder substitution, the contract is documented and the format-string machinery is opt-in (it costs nothing at default-template time). Skipping unless the user explicitly wants the contract narrowed.

### WR-09 — Inline single-call private helpers in `attempt_landscape.py`
**Why deferred:** Stylistic / readability tradeoff. The current decomposition reads as a clear top-down outline (`_render_failed_attempt` → `_render_accepted_plan` / `_render_generator_outcomes` / `_render_evaluator_judgment`). Inlining saves ~75 LOC but flattens an otherwise legible function-flow. Lower priority than the structural wins above.

## Test results

- **`backend/tests/unit_test/test_task_center/test_context_engine/` — 61/61 pass** (the directly-affected suite)
- **`backend/tests/unit_test/test_task_center/` + `test_tools/` — 415/415 pass** (broader downstream)
- **Pre-existing failures (12) in `test_agents/`** are unrelated: `ModuleNotFoundError: No module named 'task_center.agent_routing.predicates'`. Verified by stashing my changes and re-running — same failures on the clean branch. Caused by an in-flight refactor (`task_center.agent_routing` → `task_center._core.agent_routing`) by a parallel session.

## Test imports updated

- `test_helper_recipes.py`: `recipes.planner` → `recipes.helper` (advisor/resolver imports, 2 sites)
- `test_recipes_other.py`: `recipes import _entry_executor_build` → `recipes.entry_executor import _entry_executor_build`

## Before / After LOC by file

| File | Before | After | Δ |
|---|---|---|---|
| `__init__.py` | 1 | 1 | 0 |
| `core.py` | 189 | 189 | 0 (untouched; parallel-session diff visible) |
| `packet.py` | 94 | 94 | 0 |
| `recipes_registry.py` | 117 | 66 | **−51** (WR-02) |
| `renderer.py` | 246 | 246 | 0 (WR-08 deferred) |
| `scope.py` | 108 | 104 | −4 (WR-04) |
| `recipes/__init__.py` | 126 | 29 | **−97** (WR-05) |
| `recipes/_shared.py` | — | 135 | +135 (new; consolidates WR-01) |
| `recipes/entry_executor.py` | — | 46 | +46 (new; WR-05) |
| `recipes/helper.py` | — | 101 | +101 (new; WR-06/07) |
| `recipes/attempt_landscape.py` | 216 | 216 | 0 (import path only; WR-09 deferred) |
| `recipes/evaluator.py` | 144 | 131 | −13 (WR-03) |
| `recipes/generator.py` | 257 | 120 | **−137** (WR-01/WR-03) |
| `recipes/planner.py` | 220 | 66 | **−154** (WR-01/WR-03/WR-06) |
| **Total** | **1718** | **1544** | **−174 (−10%)** |

## Structural wins (qualitative, not in LOC)

1. **No more sibling-recipe imports.** Before: `planner.py` and `evaluator.py` both imported from `recipes.generator`; `planner.py` also imported from `recipes.attempt_landscape`. After: every recipe file imports only from `recipes._shared` or `recipes.attempt_landscape` (which itself imports only from `_shared`). Reading any one recipe no longer requires understanding two others.
2. **`recipes/__init__.py` is now 29 LOC of pure auto-discovery** (was 126 LOC mixing three concerns). New recipes drop in as files; no edits to `__init__.py` needed.
3. **`recipes_registry.py` is 66 LOC of one cohesive abstraction** (was 117 LOC with a dead ABC + adapter layer).
4. **Import-chain depth (criterion 3):** All remaining cross-recipe imports go through `recipes._shared` or `recipes.attempt_landscape`. The 4-segment depth (`task_center.context_engine.recipes.X`) is unchanged structurally but no module imports more than one sibling recipe.

## Commit suggestion

Suggest splitting into two commits for cleaner review:

1. `refactor(context_engine): extract shared frame helpers + helper recipes`
   - New: `recipes/_shared.py`, `recipes/helper.py`, `recipes/entry_executor.py`
   - Modified: `recipes/__init__.py`, `recipes/generator.py`, `recipes/planner.py`, `recipes/evaluator.py`, `recipes/attempt_landscape.py`
   - Test updates: `test_helper_recipes.py`, `test_recipes_other.py`

2. `refactor(context_engine): drop dead Recipe ABC + redundant guards`
   - Modified: `recipes_registry.py` (delete `Recipe` class)
   - Modified: same recipe files as above (drop `if scope.X is None:` guards)
   - Modified: `scope.py` (docstring correction)
