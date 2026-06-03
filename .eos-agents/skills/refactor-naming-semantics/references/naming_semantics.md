# Naming Semantics Reference

Use this reference when deciding whether names should change and what the replacement names should communicate.

## Audit Order

1. Start at the path: folder and file names should reveal ownership, domain, and workflow position before the file is opened.
2. Review public module names and exported symbols before private helpers.
3. Review class/function/method names by responsibility and side effect.
4. Review important variables that cross a function boundary, hold state, model domain concepts, or influence control flow.
5. Leave small loop variables and obvious local temporaries alone unless they hide domain meaning.

## Name Smells

- Vague buckets: `utils`, `helpers`, `common`, `shared`, `misc`, `base`.
- Authority without responsibility: `manager`, `handler`, `processor`, `service`, `controller`, `engine`.
- Overloaded lifecycle words: `status`, `state`, `phase`, `mode`, `result`, `context`, `data`, `payload`.
- Directionless verbs: `handle`, `process`, `run`, `execute`, `do`, `update`, `sync`.
- Implementation names where domain names are clearer: `dict_data`, `raw_obj`, `temp`, `wrapper`, `adapter`.
- Names that preserve old architecture after the code has moved.

Smelly names can stay only when the surrounding repo uses them as a precise local convention or when a public import path must be preserved.

## Better Name Shape

Prefer names that answer at least two of these questions:

- What domain concept is owned here?
- Who calls this, and at what workflow step?
- What input is transformed?
- What output or side effect is produced?
- Is this internal, public, persisted, or transport-facing?
- Is the value pending, active, committed, projected, serialized, or displayed?

Examples:

- Prefer `workspace_patch_projection.py` over `utils.py` when the file projects workspace edits.
- Prefer `load_sandbox_layer_stack()` over `process_layers()` when the function loads ordered sandbox layers.
- Prefer `submission_review_state` over `status` when the value tracks the review state of a submission.
- Prefer `public_api.py` as a thin facade only when external imports depend on it; keep internal implementation names more specific.

## Rename Rules

- Rename after simplifying the shape, not before, unless the old name blocks understanding.
- Update all imports, tests, fixtures, docs, and mocks affected by the rename.
- Prefer one canonical internal name. Do not add aliases for convenience.
- Preserve a public facade only when active callers, documented APIs, or migration windows require it.
- If preserving a facade, make the facade explicit and thin, and move internal callers to the canonical name.

## Rename Map Handoff

For non-trivial renames, maintain a compact rename map in loop notes or the final report:

| Old name/path | New name/path | Semantic reason | Public facade impact | Call sites updated | Stale-name search |
| --- | --- | --- | --- | --- | --- |
| `<old>` | `<new>` | `<why the new responsibility is clearer>` | `<none/preserved/changed>` | `<files or count>` | ``rg -n "<old>" <scope>`` |

Use this map for subagent handoff and final self-review. Every preserved old public path needs an explicit compatibility reason.
