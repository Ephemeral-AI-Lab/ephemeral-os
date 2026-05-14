---
status: round_2_complete
target: backend/src/task_center/context_engine
round: 2
deferred_fixed: 2
loc_before_round1: 1718
loc_after_round1: 1544
loc_after_round2: 1430
loc_delta_total: -288 (-16.8%)
loc_delta_this_round: -114 (-7.4%)
tests_run: backend/tests/unit_test/test_task_center/ + test_tools/ (415)
tests_status: all green
---

# Round 2 — Deferred Fixes Applied + Second-Round Findings

## Part A — Deferred fixes (WR-08, WR-09) applied

### WR-08 — Replaced `HeadingTemplate` class with module dict
**File:** `renderer.py` (246 → 187 LOC, −59)

Verified `HeadingTemplate` and `default_heading_template()` have **zero external callers** (grep repo-wide, only references are inside `renderer.py` itself). The documented `str.format` placeholder capability was unused: every registered default is a static string with no placeholders.

Removed:
- `HeadingTemplate` class (registry + `heading_for` method)
- `default_heading_template()` factory
- `str.format()` machinery + its `KeyError` fallback
- `_split_inherited` staticmethod (was a 4-line two-list filter — inlined into `render()`)

Added:
- `_DEFAULT_HEADINGS` module-level dict
- `_heading_for(block, headings)` module-level function (4 lines, exact same precedence: explicit metadata → registered → humanized fallback)
- `_truncate(block)` lifted to module level (was a staticmethod that didn't use `cls`/`self`)

`MarkdownPromptRenderer.__init__` signature changed from `(heading_template: HeadingTemplate | None)` to `(headings: dict[str, str] | None)`. Public API users (none external) just pass a dict now.

### WR-09 — Inlined single-call helpers in `attempt_landscape.py`
**File:** `attempt_landscape.py` (216 → 161 LOC, −55)

Verified private helpers have **zero external callers** (grep confirms only internal references). Inlined / consolidated:

| Removed | Now lives in |
|---|---|
| `_render_accepted_plan` | `_render_failed_attempt` as an f-string |
| `_plan_kind` | `_render_failed_attempt` as if-elif chain |
| `_status_summary_lines` | `_render_generator_outcomes` as comprehension |
| `_render_generator_detail` | `_render_generator_outcomes` as comprehension |
| `_should_render_generator_detail` | filter in same comprehension |
| `_render_evaluator_judgment` | `_render_failed_attempt` body |
| `_has_premature_generator_failure` | `_render_failed_attempt` as `any(...)` |
| `_blocked_by` | `_generator_outcomes` inline ternary |

Also moved magic strings to module constants: `_PREMATURE_STATUSES`, `_EMPTY_SUMMARY_PLACEHOLDERS`.

**Function count:** 11 → 4 (`failed_attempt_landscape_blocks`, `_render_failed_attempt`, `_render_generator_outcomes`, `_generator_outcomes`). Each remaining function has a clear single responsibility and is ≥10 LOC of real logic.

## Part B — Round-2 review findings (additional observations)

The cleanup surfaced a handful of lower-priority items that are now visible (previously buried under the structural noise). Listed for awareness — not all worth fixing.

### IN-R2-01 — `core.py` module docstring is 25 lines of architecture prose
**File:** `core.py:1-26`

Describes the entire compose-build-render pipeline + reverse-import rationale + exception-block ordering. Useful prose, but it duplicates content that belongs in plan/§3.3 or a doc/wiki page. Skim-cost for readers is high relative to actionable info. Could compress to ~5 lines:

> _"Engine + composer + engine exceptions. ContextComposer threads `base_agent_name` + ContextScope through resolver → engine → renderer. Recipe IDs are looked up at call time; adding a role = registering a recipe."_

Skipped because: doc-only, no functional impact. ~20 LOC available.

### IN-R2-02 — `core.py:121-127` has a 7-line comment to explain a 1-line lazy import
**File:** `core.py`

```python
# ---- Composer -----------------------------------------------------------
#
# ``RuleBasedAgentResolver`` is imported lazily inside ``ContextComposer.default``
# because ``agent_routing`` imports ``ContextEngineDeps`` from this module;
# importing the resolver at module top forms a cycle when an external entry
# point loads ``agent_routing`` before ``core``. Deferring the resolver
# import keeps both load orders safe.
```

The actual lazy import is one line (`from task_center.agent_routing import RuleBasedAgentResolver`). Could be a single inline `# lazy: cycles with agent_routing` comment.

Note: This file is currently being modified by a parallel session (the `agent_routing` → `_core.agent_routing` refactor). Skipped to avoid stomping their diff.

### IN-R2-03 — `scope.py` has 4 cargo-cult factory methods
**File:** `scope.py:54-104`

`for_planner`, `for_generator`, `for_evaluator`, `for_entry_executor` are all `return cls(**kwargs)` one-liners. They're used by `attempt/launch.py` (4 call sites), so they're not deletable. But they add no logic — just documentation of which fields each role needs.

Defensible argument: they DO express role contracts at the call site, and removing them would scatter that knowledge across `launch.py`. Counter-argument: with 4 nearly-identical factories at 8-15 LOC each, the contract is already documented in `_REQUIRED_FIELDS` on each recipe.

Worth a conversation; not unilaterally fixable. Skipped.

### IN-R2-04 — `core.py` exception block has 6 lines of prose justifying the import ordering
**File:** `core.py:34-39` and `core.py:60-66`

Two redundant comment blocks (one in the docstring, one inline) explain why exceptions are defined before the package imports. Could be a single `# defined before package imports — agent_routing imports back from here` comment.

Skipped: same reason as IN-R2-02 (parallel session active on this file).

### IN-R2-05 — Per-recipe `noqa: E402` markers in `core.py`
**File:** `core.py:62-66`

```python
from agents import AgentDefinition  # noqa: E402
from task_center.context_engine.packet import ContextPacket  # noqa: E402
from task_center.context_engine.recipes_registry import RecipeRegistry  # noqa: E402
from task_center.context_engine.renderer import MarkdownPromptRenderer  # noqa: E402
from task_center.context_engine.scope import ContextScope  # noqa: E402
```

Five `noqa: E402` markers because imports come after exception class definitions. Could be a single per-file `# ruff: noqa: E402` directive at the top of the file (or in `pyproject.toml` per-file-ignores). Minor.

### IN-R2-06 — `recipes_registry.RecipeRegistry._registry: ClassVar[dict[...]]` is a process-global mutable
**File:** `recipes_registry.py:38-67`

Not new — existed pre-refactor — but now more visible without the `Recipe` ABC noise around it. The class is essentially a namespaced module-level singleton: every method is `@classmethod`, the state is `ClassVar`. Could equally well be module-level functions + a module-level `_REGISTRY: dict = {}`. The class form aids discoverability (one symbol to import); the function form removes a layer of indirection.

Not a defect, but worth a stylistic call. Skipped — not in current ask.

### IN-R2-07 — `packet.py` `ContextBlockKind` enum vs free-string `kind` field (carryover IN-03)
Still standing. The enum is genuinely useful (13 callsites verified), but the module docstring's "kept open via plain str fields rather than a closed enum" comment contradicts the enum's existence. Fix is a 2-line docstring edit; left for the user to choose phrasing.

### IN-R2-08 — `helper.py` and `planner.py` reference "plan §3.3.8" and "§3.3.6"
Plan references in docstrings — useful when the plan exists at a stable path, brittle when plan layout changes. Not actionable without knowing whether the plan still exists at those section numbers.

## LOC summary (cumulative)

| File | Original | After R1 | After R2 | Δ Total |
|---|---|---|---|---|
| `__init__.py` | 1 | 1 | 1 | 0 |
| `core.py` | 189 | 189 | 189 | 0 |
| `packet.py` | 94 | 94 | 94 | 0 |
| `recipes_registry.py` | 117 | 66 | 66 | −51 |
| `renderer.py` | 246 | 246 | **187** | **−59** |
| `scope.py` | 108 | 104 | 104 | −4 |
| `recipes/__init__.py` | 126 | 29 | 29 | −97 |
| `recipes/_shared.py` | — | 135 | 135 | +135 (new) |
| `recipes/entry_executor.py` | — | 46 | 46 | +46 (new) |
| `recipes/helper.py` | — | 101 | 101 | +101 (new) |
| `recipes/attempt_landscape.py` | 216 | 216 | **161** | **−55** |
| `recipes/evaluator.py` | 144 | 131 | 131 | −13 |
| `recipes/generator.py` | 257 | 120 | 120 | −137 |
| `recipes/planner.py` | 220 | 66 | 66 | −154 |
| **Total** | **1718** | **1544** | **1430** | **−288 (−16.8%)** |

## Test results

- **`backend/tests/unit_test/test_task_center/test_context_engine/` — 61/61** pass
- **`backend/tests/unit_test/test_task_center/` + `test_tools/` — 415/415** pass
- **Pre-existing failures (12) in `test_agents/`** — `task_center.agent_routing.predicates` ModuleNotFoundError. Confirmed unrelated (failing on clean branch).

## Status

All originally-flagged warnings (WR-01 through WR-09) addressed except for documentation-only items (WR-04 was applied as a docstring rewrite). The IN-R2 series above is observational — no items urgent enough to act on without explicit user direction.

If you want to keep cutting LOC, the remaining tractable targets are:
- **IN-R2-01 + IN-R2-04**: trim verbose docstrings/comments in `core.py` — ~25–30 LOC available, doc-only
- **IN-R2-03**: collapse `ContextScope.for_X` factories if you decide they're redundant — requires changing 4 call sites in `attempt/launch.py`
- **IN-R2-06**: `RecipeRegistry` → module functions — stylistic, ~15 LOC

Total remaining headroom: ~60–80 LOC, with diminishing returns.
