---
status: round_3_complete
target: backend/src/task_center/context_engine
round: 3
loc_before_round1: 1718
loc_after_round1: 1544
loc_after_round2: 1430
loc_after_round3: 1398
loc_delta_total: -320 (-18.6%)
loc_delta_this_round: -32 (-2.2%)
tests_run: backend/tests/unit_test/test_task_center/ + test_tools/ (415)
tests_status: all green
---

# Round 3 — Core Restructure + Remaining Cleanup

## Part A — Round 3 review (findings)

By round 3 the obvious wins are exhausted. The remaining tractable improvements concentrate on `core.py`, which has been untouched until now because a parallel session was modifying it. That session has landed (git status clean), so the round-3 review centers there.

### R3-01 — `core.py` carries 30+ LOC of meta-prose
**Severity:** Warning

The file had:
- 25-line module docstring describing the entire architecture
- 7-line comment block explaining why exceptions are defined before package imports
- 5 separate `# noqa: E402` markers
- 7-line comment block at line 121 explaining the lazy `RuleBasedAgentResolver` import

All of this was meta-explanation for one structural quirk: the cycle `core ↔ scope ↔ recipes_registry` over the exception class names.

**Root cause:** Exceptions, deps, engine, composer all lived in one file. Because exceptions had to come first (other modules import them back), every other import had to be deferred past them, requiring per-line noqa overrides.

**Fix:** Split exceptions into their own leaf module (`exceptions.py`). Cycle disappears; deferred-imports gymnastics goes away; all 5 `noqa: E402` markers go away; explanatory prose goes away. Backward-compatible: `core.py` re-exports the exception names, so existing callers (`from task_center.context_engine.core import ContextEngineError`) keep working unchanged.

### R3-02 — `ContextEngine` was a 3-line class with a manual `deps` property
**Severity:** Info

```python
class ContextEngine:
    def __init__(self, deps): self._deps = deps
    @property
    def deps(self): return self._deps
    def build(self, recipe_id, scope): ...
```

**Fix:** `@dataclass(frozen=True, slots=True)` with `deps` as a field. Same public API (positional `ContextEngine(deps)`, `.deps` attribute access). Saves ~7 LOC and gives free `__eq__`/`__repr__`/immutability.

### R3-03 — `_persist` was a 3-line method used once
**Severity:** Info

```python
def _persist(self, packet):
    store = self.engine.deps.context_packet_store
    if store is None:
        return None
    return store.insert(packet)
```

**Fix:** Inlined at the single call site as `store.insert(packet) if store is not None else None`. The store is hoisted to a local variable in `compose()` for readability. Saves ~4 LOC.

### R3-04 — `packet.py` docstring contradicts its own enum
**Severity:** Info (carryover IN-03)

The module comment said: _"kept open via plain str fields rather than a closed enum so new recipes can introduce kinds without touching this module."_ But the same file defines `class ContextBlockKind(StrEnum)`. The intent was right (kind field is typed `str`, the enum is just convenience constants) but the prose contradicted itself.

**Fix:** Rewrote the comment to say plainly: _"``ContextBlock.kind`` is typed as ``str`` (not this enum) so new recipes can introduce kinds without touching this module. The enum below is a namespaced set of constants for callers that want to avoid stringly-typed code."_

### R3-05 — `"missing task row"` was a magic string in two places
**Severity:** Warning (subtle drift risk)

In `attempt_landscape.py`:
- `_PREMATURE_STATUSES = frozenset({"failed", "blocked", "missing task row"})`
- `_generator_outcomes` sets `status="missing task row"` when a task row is absent

If someone renamed one without the other, the premature-failure check would silently miss the case. Classic stringly-typed coupling.

**Fix:** Extracted `_MISSING_TASK_ROW_STATUS = "missing task row"` and referenced from both sites.

### R3-06 — `recipes_registry.py` and `_shared.py` import `ContextEngineError` from `core` (cycle relic)
**Severity:** Info

Before R3, every file in the package imported `ContextEngineError` from `core.py`, going through the cycle that the new `exceptions.py` resolves. Migrated `scope.py` and `recipes_registry.py` to import from `exceptions` directly. The recipe modules (generator/evaluator/planner/helper/entry_executor) still import from `core` because that path is the public API and is backward-compatible; they can migrate opportunistically.

## Part B — Findings deliberately NOT addressed

### IN-R2-03 — `ContextScope.for_X` factories
**Why skipped:** The 4 factories (`for_planner`, `for_generator`, `for_evaluator`, `for_entry_executor`) are used by `backend/src/task_center/attempt/launch.py` (4 call sites). They're cargo-cult one-liners (each is `cls(**kwargs)`) but they document role-required fields at the call site. Deleting them would require an opinion on whether the documentation value justifies the LOC, and that opinion hasn't been requested. Total addressable LOC: ~40 in `scope.py` + 4 minor edits in `launch.py`.

### IN-R2-06 — `RecipeRegistry` class → module functions
**Why skipped:** All methods are `@classmethod` over a `ClassVar` dict. Could be flattened to module-level functions + a module-level dict. But:
- Test files import `RecipeRegistry` and reach into `_registry` directly (e.g., `RecipeRegistry._registry.update(saved)` in `test_engine.py`).
- External callers say `RecipeRegistry.register(...)` — flattening means breaking ~10 import statements across tests + src.
- Saves ~10 LOC. Not worth the blast radius without explicit ask.

### Helper recipe consolidation (WR-07 carryover)
`_advisor_build` and `_resolver_build` could be collapsed into `functools.partial(_build_helper_packet, target_role=...)` invocations directly inside `ADVISOR_RECIPE` and `RESOLVER_RECIPE`. But `test_helper_recipes.py` calls these functions directly (`_advisor_build(scope, deps)`). Saves 4 LOC at the cost of test edits.

### Plan-section references in docstrings (IN-R2-08)
`helper.py` references "plan §3.3.8"; `planner.py` references "plan §3.3.6". These are brittle pointers to a document outside the codebase. Not actionable without confirmation that the plan has stable section numbers.

## Part C — Fixes applied this round

### R3-A — Created `exceptions.py` (25 LOC, new file)
**Files:** `exceptions.py` (new), `core.py` (189 → 136, −53), `scope.py` (one import path swap), `recipes_registry.py` (one import path swap + docstring trim 66 → 62, −4)

- New leaf module `exceptions.py` houses all 4 exception classes.
- `core.py` imports + re-exports them via `__all__`.
- `scope.py` and `recipes_registry.py` import directly from `exceptions.py` — breaks the cycle that required deferred imports.
- `core.py` is now structurally clean: all imports at top, no `noqa` markers, no "exceptions first" gymnastics, no 25-line architecture prose.
- Backward compatible: all 20+ external callers of `from task_center.context_engine.core import ContextEngineError` (etc.) keep working.

### R3-B — Dataclassified `ContextEngine` and inlined `_persist`
**File:** `core.py`

- `ContextEngine`: now `@dataclass(frozen=True, slots=True)` with `deps` as a public field. Removed the manual `__init__` and `@property deps`.
- `_persist`: 3-line method inlined at its single call site in `compose()`.
- `compose()` now reads the store into a local for readability before the ternary.

### R3-C — Fixed `packet.py` docstring contradiction
**File:** `packet.py` (94 → 93, −1)

Replaced the self-contradicting "kept open via plain str fields rather than a closed enum" comment with a non-contradicting one that explains the actual design: kind is `str`-typed, the enum is convenience.

### R3-D — Extracted `_MISSING_TASK_ROW_STATUS` constant
**File:** `recipes/attempt_landscape.py` (161 → 162, +1 — adds the constant)

Magic-string `"missing task row"` now lives in one place. Both `_PREMATURE_STATUSES` and `_generator_outcomes` reference the constant.

## LOC summary (cumulative across all 3 rounds)

| File | Original | R1 | R2 | R3 | Δ Total |
|---|---|---|---|---|---|
| `__init__.py` | 1 | 1 | 1 | 1 | 0 |
| `core.py` | 189 | 189 | 189 | **136** | **−53** |
| `exceptions.py` | — | — | — | 25 | +25 (new) |
| `packet.py` | 94 | 94 | 94 | 93 | −1 |
| `recipes_registry.py` | 117 | 66 | 66 | 62 | −55 |
| `renderer.py` | 246 | 246 | 187 | 187 | −59 |
| `scope.py` | 108 | 104 | 104 | 104 | −4 |
| `recipes/__init__.py` | 126 | 29 | 29 | 29 | −97 |
| `recipes/_shared.py` | — | 135 | 135 | 135 | +135 (new) |
| `recipes/entry_executor.py` | — | 46 | 46 | 46 | +46 (new) |
| `recipes/helper.py` | — | 101 | 101 | 101 | +101 (new) |
| `recipes/attempt_landscape.py` | 216 | 216 | 161 | 162 | −54 |
| `recipes/evaluator.py` | 144 | 131 | 131 | 131 | −13 |
| `recipes/generator.py` | 257 | 120 | 120 | 120 | −137 |
| `recipes/planner.py` | 220 | 66 | 66 | 66 | −154 |
| **Total** | **1718** | **1544** | **1430** | **1398** | **−320 (−18.6%)** |

## Structural state at end of R3

- **Module count:** 11 → 15 (3 new recipe modules + 1 new exceptions module)
- **Cycle count:** 1 (core ↔ scope/recipes_registry over exceptions) → 0
- **`noqa: E402` markers:** 5 → 0
- **Cross-recipe sibling imports:** ~5 → 0 (all go through `_shared.py` or `attempt_landscape.py`)
- **Multi-paragraph architectural docstrings in source files:** 5 → 1 (just the `_shared.py` 4-line module note)
- **Magic-string couplings:** at least 1 fixed (`"missing task row"`)
- **Defensive duplicate guards:** 5 → 0

## Test results

- `backend/tests/unit_test/test_task_center/` + `test_tools/` — **415/415 pass**
- Pre-existing 12 failures in `test_agents/` confirmed independent (carryover from rounds 1 & 2)

## Recommendation

Three rounds of cleanup have removed ~19% of LOC, eliminated all sibling-recipe imports, dissolved the exception import cycle, and split each module to a single clear responsibility. Remaining improvements are stylistic or blast-radius-prohibitive (`ContextScope.for_X` factories, `RecipeRegistry` → module functions, helper-build consolidation). Further reduction work is diminishing returns from here.

Suggest stopping at R3 unless a specific item in the deferred list is requested explicitly.
