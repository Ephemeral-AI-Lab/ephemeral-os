# Widening And Runtime

Use this reference only when either condition is true:

1. You need to edit or create a file outside `scope_paths`.
2. The lane is runtime-owned and your evidence is still only syntax, LSP, or readback.

## Task/Goal

- You are deciding whether a widened edit belongs in this lane, or you are close to declaring success without runtime proof.

## Avoid

- If the scoped file is missing or disproved, must not widen by filename similarity alone. Do not hop to `pkg/foo_bar.py`, `pkg/_foo.py`, or another lookalike path by filename resemblance alone.
- If live evidence identifies a different production owner, missing module, compatibility shim, re-export module, or import bridge outside `scope_paths`, do not create or edit it in this lane. The missing path's literal name is evidence for replanning, not permission to create it. "Needed to make tests collect", "standard re-export pattern", "target count requires it", and "scope contains a similar file" are not exceptions.
- If `scope_paths` itself names an absent module, shim, re-export module, or import bridge that came from a test import or collection error, non-test production evidence is required before writing. Otherwise submit a failure with the missing-path evidence.
- Before calling `daytona_write_file(...)` or `daytona_edit_file(...)`, compare the target path to `scope_paths`. Do not attempt an out-of-scope edit or write to see whether the tool allows it; the attempt itself is a failed lane, even if the tool later returns an advisory warning.
- Must not use warning/config overrides, blank `addopts`, or alternate pytest config as proof while normal startup is red.
- Do not skip, xfail, or rewrite the verify file just to make the benchmark look green.

## Workflow

- Must treat `scope_paths` as the default edit surface, compose with live sibling edits on widened files, and keep widened edits to one adjacent supporting owner surface for the same bug.
- Before a widened write, classify it: adjacent support for the same scoped owner may proceed; a missing module, compatibility shim, re-export, import bridge, or different owner must be reported with `submit_task_summary(type="fail", content=...)` before writing, even when tests import that exact missing path.
- For new files, `scope_paths` does not override provenance. If the only reason for the new path is a failing test import, fail for replanning instead of creating the file.
- Must treat failing tests and verify commands as evidence first, not automatic test ownership, and must not report success on a runtime-owned lane until one assigned runtime verification command passes.
- If the exact verify command fails before the named target collects, or a shared import/runtime-control problem fires first, keep that shared chain red until it is repaired or reverted.
- If the fault is ambient drift, including root or OS permission semantics that invalidate a test setup, stop and surface that mismatch instead of editing tests or improvising installs.
- If `daytona_edit_file` returns `verification-surface write allowed in advisory mode`, revert that test edit and widen only to the adjacent production/import chain that owns the failure.
- If any Daytona mutation returns an `outside write_scope` warning, `verification-surface write allowed`, or evidence of a missing outside-scope module/shim/re-export/import bridge, stop immediately and submit `submit_task_summary(type="fail", content=...)` with the warning. Do not read, inspect, continue verifying, create a shim, or retry the operation through CodeAct.

## Expected Outcome

- Widening stays adjacent, justified, and anchored to one runtime-owned failing surface; a missing outside-scope owner becomes replan evidence, not an unscoped edit.
