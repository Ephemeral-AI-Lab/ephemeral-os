---
status: issues_found
target: backend/src/task_center/context_engine
files_reviewed: 11
total_loc: 1718
estimated_reducible_loc: ~600 (35%)
findings:
  critical: 0
  warning: 9
  info: 8
  total: 17
review_focus:
  - naming semantics
  - implementation quality
  - simplicity / redundancy
  - import-chain depth
---

# Context-Engine Code Review

Scope: `backend/src/task_center/context_engine/` — 11 Python files, 1718 LOC.

Reviewed against four user-specified criteria: (0) naming, (1) implementation quality, (2) simplicity, (3) import-chain depth.

---

## TL;DR

The module is functionally clean but **organizationally noisy**. Three structural problems dominate everything else:

1. **`recipes/generator.py` is a god-module for shared helpers** — `mission_episode_blocks` + 4 heading constants are defined inside the generator recipe and re-imported by `planner.py` and `evaluator.py`. This is the worst single naming/organization issue in the directory.
2. **`Recipe` ABC in `recipes_registry.py` is dead code** — never subclassed anywhere in the codebase. ~30 LOC of pure YAGNI.
3. **Every recipe duplicates `assert_fields` as an `if x is None: raise` block** — the engine already pre-validates via `scope.assert_fields(...)`. The duplicated guard adds ~10–15 LOC per recipe with a misleading "under `python -O`" justification (`assert_fields` uses `raise`, not `assert`, so `-O` is irrelevant).

Together: ~600 LOC reducible without functional change. Best concrete reductions are in `scope.py` (108→~50 if `for_X` not exposed publicly — but they ARE used externally, see WR-04), `recipes_registry.py` (117→~60), `recipes/__init__.py` (126→~50), `recipes/generator.py` (257→~150), `recipes/planner.py` (220→~100).

---

## CR — Critical

_None._

---

## WR — Warnings (correctness / structure)

### WR-01 — `mission_episode_blocks` is mis-located in `generator.py`

**Files:** `recipes/generator.py:36-140`, `recipes/planner.py:38-40`, `recipes/evaluator.py:19-21`

The module docstring even admits it: _"Also contains the mission/episode context block builders shared by role recipes (formerly mission_episode.py)."_ Both `planner.py` and `evaluator.py` import `mission_episode_blocks` from `recipes.generator`. This means:

- Importing the planner recipe transitively pulls the entire generator recipe builder.
- The four `MISSION_EPISODE_HEADING`/`MISSION_HEADING`/`CURRENT_EPISODE_HEADING`/`PREVIOUS_EPISODE_RESULTS_HEADING` constants live next to a recipe that doesn't even use the first three.
- "Generator" no longer means "generator recipe" — it's a sibling-recipe import target. Readers have to guess.

**Fix:** Move `mission_episode_blocks` + the heading constants into a sibling helper module (e.g., `recipes/_frame.py` or back into `recipes/mission_episode.py` — your prior name was correct). Generator/planner/evaluator each import from that one module instead of from each other.

---

### WR-02 — `Recipe` ABC is dead code

**Files:** `recipes_registry.py:49-77`, plus surrounding docstrings/imports.

Verified via repo-wide grep: `class Recipe(ABC):` has no subclasses anywhere — production, tests, or fixtures. Every concrete recipe is built as a `ContextRecipe(...)` dataclass instance (`PLANNER_RECIPE`, `GENERATOR_RECIPE`, `EVALUATOR_RECIPE`, `ENTRY_EXECUTOR_RECIPE`, `ADVISOR_RECIPE`, `RESOLVER_RECIPE`).

The `Recipe` ABC + `to_context_recipe()` adapter + the `register()` branch that handles it + the multi-paragraph docstring justifying both shapes — all of it is speculative scaffolding for a use case that never materialized.

**Fix:** Delete `Recipe`, `to_context_recipe`, and the `isinstance(recipe, Recipe)` branch in `RecipeRegistry.register`. Trim the module docstring. `recipes_registry.py` drops from 117 LOC to ~55 LOC.

---

### WR-03 — Redundant `if scope.X is None` guards in every recipe

**Files:** `recipes/__init__.py:64-68`, `recipes/evaluator.py:35-39`, `recipes/generator.py:157-165`, `recipes/planner.py:76-84`, `recipes/planner.py:169-177`.

Each recipe builder repeats the same idiom:

```python
if scope.X is None or scope.Y is None:
    raise ContextEngineError("recipe requires X and Y; got {scope!r}")
```

…but `ContextEngine.build` already does:

```python
scope.assert_fields(recipe.required_scope_fields)
```

…before invoking the builder. The "explicit guard makes the recipe self-defending under `python -O`" justification (repeated verbatim in 5 places) is **wrong**: `assert_fields` raises `RecipeScopeError` via `raise`, not via `assert`. `python -O` doesn't affect it.

**Cost:** ~50 LOC of dead validation across 5 builders, plus 5 copies of the same misleading comment.

**Fix:** Delete the guards and the comments. Trust `assert_fields`. If you want type-narrowing for mypy, use `assert scope.X is not None` (one line, no `raise`) — those *will* strip under `-O` but mypy will still narrow, which is the actual point.

---

### WR-04 — `ContextScope.for_X` factory docstrings overpromise type safety

**File:** `scope.py:51-108`

Module docstring claims: _"missing a required field for the role is a static error instead of a runtime `RecipeScopeError`."_ This is **misleading**. The factories take keyword-only `str` args (not `str | None`), so a caller passing `None` *does* trigger a static error in strict mypy. But:

- A caller passing nothing at all relies on Python's "missing keyword argument" error, which is a **runtime** TypeError, not a static one (mypy will catch it in strict mode only).
- The factories do not call `assert_fields` — they just call `cls(...)`. So the engine's runtime guard is still what catches real misuse.

These factories ARE genuinely used by `attempt/launch.py` (verified), so they're not deletable — but the docstring should not claim safety the language doesn't provide.

**Fix:** Rewrite the module docstring to describe what the factories actually do: _"document required fields per role at the call site; missing fields raise `TypeError` at call time."_ Drop the "static error" claim.

---

### WR-05 — `recipes/__init__.py` mixes 3 concerns

**File:** `recipes/__init__.py` (126 LOC)

Three unrelated things live in this file:

1. The auto-discovery walker (`register_builtin_recipes`).
2. A shared utility (`latest_summary_text`) — imported by 3 sibling modules.
3. The full `entry_executor` recipe (id, required fields, builder, `ENTRY_EXECUTOR_RECIPE` constant), plus a special-cased manual registration because auto-discovery can't reach `__init__.py`.

Mixing #3 with #1 forces the auto-discovery code to be aware of itself ("also register the one in this file"). Mixing #2 with everything else makes `from task_center.context_engine.recipes import latest_summary_text` read like importing a recipe.

**Fix:**
- Move `entry_executor` into `recipes/entry_executor.py`. Delete the manual `RecipeRegistry.register(ENTRY_EXECUTOR_RECIPE)` line — auto-discovery picks it up.
- Move `latest_summary_text` into a sibling helper module (same module that holds `mission_episode_blocks` — see WR-01).
- `recipes/__init__.py` then collapses to ~25 LOC of pure auto-discovery.

---

### WR-06 — `advisor` + `resolver` recipes are mis-located in `planner.py`

**File:** `recipes/planner.py:44-152`

Half of `planner.py` is the planner recipe; the other half is `_build_helper_packet` + `_advisor_build` + `_resolver_build` + `ADVISOR_RECIPE` + `RESOLVER_RECIPE` + `_DEMOTION` + `demote_priority`. The docstring acknowledges: _"Also contains the advisor and resolver helper recipes (absorbed from helper.py)."_

These helpers have nothing to do with the planner — they implement parent-packet inheritance, which is a transversal mechanism. Same problem as WR-01 (and the original `helper.py` filename was correct — the "absorption" was a regression).

**Fix:** Move advisor/resolver back to `recipes/helper.py` (or `recipes/parent_inherit.py`). `planner.py` shrinks from 220 LOC to ~100; the new helper file is ~120 LOC of cohesive code.

---

### WR-07 — `_advisor_build` and `_resolver_build` are identical except for one string

**File:** `recipes/planner.py:125-138`

```python
def _advisor_build(scope, deps):
    return _build_helper_packet(target_role="advisor", scope=scope, deps=deps)

def _resolver_build(scope, deps):
    return _build_helper_packet(target_role="resolver", scope=scope, deps=deps)
```

Two thin shims around a single function call. Then two near-identical `ContextRecipe(id=..., required_scope_fields=..., build=...)` declarations.

**Fix:** Use `functools.partial(_build_helper_packet, target_role="advisor")` directly in the `ContextRecipe(build=...)` slot. Or a `_make_helper_recipe(target_role: str) -> ContextRecipe` factory called twice. Saves ~15 LOC.

---

### WR-08 — `HeadingTemplate` class is over-engineered

**File:** `renderer.py:33-88`

`HeadingTemplate` wraps `dict[str, str]` with:
- a `register()` method (used only by `default_heading_template()` via the constructor — never re-called),
- a `heading_for()` method that does `str.format(kind=..., title=..., **block.metadata)` — but every registered default template is a **static string** (`"# Mission"`, `"# Current Episode"`, etc.) with no format placeholders.

The `format()` call walks every template trying to substitute placeholders that don't exist. The `KeyError` fallback is the actual control flow for every block with extra metadata — except none of the defaults have placeholders to fail on, so `KeyError` never actually fires for the registered kinds. It only fires on the `"# {title}"` *fallback* template (line 50), and even then only if metadata is missing — which never happens because `{title}` doesn't depend on metadata.

**Fix:** Replace the class with `dict[str, str]` and one helper:

```python
def heading_for(block, headings):
    return block.metadata.get("heading") or headings.get(block.kind) or f"# {_humanize(block.kind)}"
```

Saves ~40 LOC. The format-string machinery is dead.

---

### WR-09 — `attempt_landscape.py` is over-decomposed

**File:** `recipes/attempt_landscape.py` (216 LOC)

8 helper functions for a single block builder:
`_render_failed_attempt`, `_render_accepted_plan`, `_render_generator_outcomes`, `_status_summary_lines`, `_render_generator_detail`, `_should_render_generator_detail`, `_render_evaluator_judgment`, `_has_premature_generator_failure`, `_generator_outcomes`, `_blocked_by`, `_plan_kind`.

Each is 2–8 lines. Some examples of trivial decomposition:

- `_render_generator_detail`: a single `f"#### {outcome.task_id}\n\n{outcome.summary}"` — inline it.
- `_should_render_generator_detail`: 6 lines for a 3-state check — inline as `if outcome.summary and outcome.summary not in {"(empty)", "(no summary recorded)"}`.
- `_has_premature_generator_failure`: a single `any(...)` — inline it.
- `_blocked_by`: 4 lines that could be `(latest.get("blocked_by") and str(latest["blocked_by"])) if isinstance(latest, dict) else None`.

The decomposition reads cleanly but tests against the public function only (`failed_attempt_landscape_blocks`). Most helpers don't earn their existence — they're abstractions over `f"…"` literals.

**Fix:** Inline single-call private helpers. Target ~140 LOC for the file.

---

## INFO — Lower-severity observations

### IN-01 — Import-chain depth: `task_center.context_engine.recipes.X` is 4 segments

User asked for "3 at most". Every cross-recipe import in this directory is 4 segments deep:

```python
from task_center.context_engine.recipes.attempt_landscape import ...
from task_center.context_engine.recipes.generator import ...
from task_center.context_engine.recipes_registry import ...  # 3 segments
```

This is structural: `recipes/` is a subpackage. Two options:
1. **Accept it.** The `recipes/` namespace is a real categorization. 4 segments for sibling-recipe imports is a fair price.
2. **Flatten.** Move every recipe into `context_engine/` directly (e.g., `context_engine/recipe_generator.py`). All cross-imports drop to 3 segments.

Recommend option 1 — `recipes/` carries genuine semantic weight, and resolving WR-01/WR-05/WR-06 removes most of the cross-recipe imports anyway (everything imports from one new helper module, not from siblings).

---

### IN-02 — `_GeneratorOutcome` name is ambiguous

**File:** `recipes/attempt_landscape.py:20-25`

Inside a "failed attempt landscape" module, `_GeneratorOutcome` reads like a runtime/lifecycle term but is actually a *display* record. Consider `_GeneratorRow` or `_AttemptGeneratorSummary`.

---

### IN-03 — `ContextBlockKind` enum vs free-string `kind` field

**File:** `packet.py:31-46`, `packet.py:63`

The module comment says: _"kept open via plain str fields rather than a closed enum so new recipes can introduce kinds without touching this module."_ But `ContextBlockKind(StrEnum)` IS a closed enum, and `kind: str = Field(min_length=1)` does NOT validate against it. So the enum is "advisory" — using `ContextBlockKind.MISSION_GOAL` and using `"mission_goal"` produce the same packet (StrEnum members ARE strings).

Effectively the enum is a name-spaced set of constants. That's fine, but the comment is contradictory. Either:
- Drop the enum and use module-level constants (`MISSION_GOAL = "mission_goal"`).
- Keep the enum but rewrite the comment to say "convenience constants — the field accepts any string."

13 enum-member accesses across the codebase, so the enum is genuinely used. Keep it; just fix the comment.

---

### IN-04 — `core.py` exception block has stale import-order rationale

**File:** `core.py:18-39, 60-66`

The "Import-order note" paragraph in the module docstring + the inline `# Defined first so that …` comment + `noqa: E402` markers — all to handle a circular import with `agent_routing.py` (imported via `from agents import AgentDefinition` and a lazy import inside `ContextComposer.default`).

This works, but it's structural debt. `agent_routing` lives outside this directory so we can't refactor it here, but flagging: if `agent_routing` were ever simplified or relocated, the entire "exceptions defined first" choreography in `core.py` could go away (~15 LOC of explanatory comments + `noqa` markers).

---

### IN-05 — `demote_priority(p)` wraps a single dict lookup

**File:** `recipes/planner.py:55-64`

```python
_DEMOTION = {ContextPriority.REQUIRED: ContextPriority.HIGH, ...}
def demote_priority(priority: ContextPriority) -> ContextPriority:
    return _DEMOTION[priority]
```

The function adds a stack frame for a `dict[Priority, Priority]` lookup. It IS imported by a test, so deleting it would break the test suite — but the test is testing a one-liner that wraps a dict. Consider inlining `_DEMOTION[priority]` at the one call site and dropping the function (after deleting/inlining the test assertions).

---

### IN-06 — `ContextEngine` is a 3-line class with `__init__`, `deps` property, and `build`

**File:** `core.py:102-118`

```python
class ContextEngine:
    def __init__(self, deps): self._deps = deps
    @property
    def deps(self): return self._deps
    def build(self, recipe_id, scope):
        recipe = RecipeRegistry.get(recipe_id)
        scope.assert_fields(recipe.required_scope_fields)
        return recipe.build(scope, self._deps)
```

`ContextEngine` exists only because `ContextComposer` needs access to both `deps` (for `_persist`) and `build` (for `compose`). Could be replaced by a frozen dataclass with a `build` method — drops `@property` and the `_deps`/`deps` indirection. Minor stylistic win, ~5 LOC.

---

### IN-07 — `_humanize` strips after replacing underscores, which can produce inconsistent capitalization

**File:** `renderer.py:29-31`

```python
def _humanize(kind: str) -> str:
    return kind.replace("_", " ").strip().capitalize()
```

`str.capitalize()` lowercases everything after the first character. So `"failed_attempt_landscape"` → `"Failed attempt landscape"` (correct here), but `"HTTP_request"` → `"Http request"`. Probably fine for the current kind taxonomy, but the choice is implicit. Consider documenting that all `ContextBlockKind` values are pure lowercase snake_case (currently true) so this works.

---

### IN-08 — Module docstrings in this directory are unusually long

Several modules carry multi-paragraph docstrings explaining architecture decisions (`core.py`, `packet.py`, `scope.py`, `recipes_registry.py`, all four recipe files). The user's CLAUDE.md guidelines favor "no comments unless the WHY is non-obvious." Some of these docstrings:

- Recipe builders prefacing themselves with their § number from a plan document that may have moved.
- `core.py` import-order note (see IN-04) — useful but verbose.
- `recipes/planner.py` 25-line docstring describing both planner + advisor + resolver inheritance policy.

Not actionable as a defect, but a ~100-LOC corpus-wide reduction is achievable by trimming docstrings to one-sentence purpose statements and pushing implementation notes into adjacent helpers' inline comments.

---

## Estimated LOC reduction by file

| File | Current | Achievable | Saves | Path |
|---|---|---|---|---|
| `__init__.py` | 1 | 1 | 0 | — |
| `core.py` | 189 | 155 | ~35 | trim docstrings, drop `deps` property (IN-06, IN-04) |
| `packet.py` | 94 | 80 | ~15 | trim docstring (IN-03) |
| `recipes_registry.py` | 117 | 55 | ~60 | delete `Recipe` ABC (WR-02) |
| `renderer.py` | 246 | 200 | ~45 | simplify `HeadingTemplate` (WR-08) |
| `scope.py` | 108 | 90 | ~20 | trim factory docstrings (WR-04) |
| `recipes/__init__.py` | 126 | 30 | ~95 | split out entry_executor + helpers (WR-05) |
| `recipes/attempt_landscape.py` | 216 | 140 | ~75 | inline single-call helpers (WR-09) |
| `recipes/evaluator.py` | 144 | 120 | ~25 | drop `if X is None` guard (WR-03) |
| `recipes/generator.py` | 257 | 100 | ~155 | split out mission_episode_blocks (WR-01); drop guard (WR-03) |
| `recipes/planner.py` | 220 | 80 | ~140 | split out advisor/resolver (WR-06); drop guard (WR-03); merge helper builds (WR-07) |
| **Total** | **1718** | **~1050** | **~670 (39%)** | |

Plus one new file `recipes/_frame.py` or similar (~80 LOC) holding `mission_episode_blocks` + headings + `latest_summary_text`. Net: ~1130 LOC, ~34% reduction.

---

## Suggested execution order

If you want to act on this, the dependencies suggest:

1. **WR-02** (delete `Recipe` ABC) — fully independent, biggest LOC/risk ratio.
2. **WR-03** (drop redundant guards) — fully independent, touches 5 files but each touch is mechanical.
3. **WR-05 + WR-01** (extract `latest_summary_text`, `mission_episode_blocks`, and `entry_executor` into proper homes) — one PR. After this, cross-recipe imports collapse.
4. **WR-06 + WR-07** (move advisor/resolver to helper module, collapse the two thin shims) — one PR.
5. **WR-08, WR-09** (renderer + attempt_landscape simplification) — independent cleanups.
6. **IN-01–IN-08** — opportunistic.

Each step is independently shippable and reversible. No critical issues; all warnings are structural/clarity, not correctness.
