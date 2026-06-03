# Reduction-Focused Refactor Reference

Use this reference when the target has redundant helpers, compatibility paths, fallbacks, duplicated logic, or overgrown abstractions.

## Reduction Questions

For every file and abstraction, ask:

- Can this 200 LOC file be 150, 100, or 50 LOC without losing behavior?
- Would a senior engineer call this overcomplicated?
- Is this helper hiding one line of code behind a vague name?
- Is this branch only supporting deleted callers, stale flags, or historical behavior?
- Is this fallback masking a clearer error path?
- Is this abstraction expressing a real boundary, or just moving code around?

If yes, simplify before renaming.

## Deletion Candidates

- Dead functions, unused classes, unused parameters, unused constants, and stale fixtures.
- Backward-compatible aliases with no active caller.
- Migration bridges after the migration window has passed.
- Deprecated parameters and dual old/new code paths.
- Fallback dispatchers where callers already provide the canonical shape.
- Speculative wrapper layers around existing repo abstractions.
- Tests that only assert deleted legacy behavior.

Keep compatibility only when required by current public contracts, persisted data, external APIs, active callers, or a documented migration window.

## Consolidation Rules

- Extract shared functions only for repeated policy, validation, transformation, or control flow.
- Introduce helper classes only for cohesive state or a real protocol.
- Prefer typed objects when a payload shape crosses boundaries or repeated parallel parameters travel together.
- Prefer enums or literal unions when status or lifecycle values are closed and meaningful.
- Do not create generic `helpers`, `utils`, `manager`, or `common` modules as landing zones.

## Behavior Rule

Keep behavior unchanged unless current behavior is clearly a bug. If behavior changes, name the bug, add or update focused tests, and explain the before/after behavior.

## Deletion Proof

Before deleting code, collect enough evidence for the risk:

- Reference search: no active imports or call sites remain, or all call sites are updated in the same pass.
- Test search: tests asserting only legacy behavior are removed or rewritten to canonical behavior.
- Runtime contract check: public APIs, persisted formats, CLI flags, config keys, and migration windows are not silently broken.
- Compatibility decision: every preserved compatibility path has a concrete active caller or public contract reason.
- Verification command: the narrowest relevant check passes after deletion.

For subagent lanes, require deletion proof in the handoff summary before integrating deletion-heavy changes.
