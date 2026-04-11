# Widening And Runtime

Use this reference only when either condition is true:

1. You need to edit a file outside `owned_files`.
2. The lane is runtime-owned and your evidence is still only syntax, LSP, or readback.

## Widening rules

- Must treat `owned_files` as the default edit surface.
- Must call `ci_scoped_status(...)` before the first edit outside `owned_files`.
- Must compose with live sibling edits on widened files.
- Must keep widened edits to one adjacent supporting owner surface for the same bug.
- Never widen into tests first when the production owner is still the clearer fix surface.
- Never widen into config or harness files unless live evidence proves they own the bug.

## Runtime completion rules

- Must treat failing tests and verify commands as evidence first, not automatic test ownership.
- Must not report success on a runtime-owned lane until one assigned runtime verification command passes.
- Never claim completion from syntax-only, LSP-only, or readback-only evidence.

## Runtime mismatch rules

- If the exact verify command fails before the named target collects, must treat that as a still-red surface.
- If the fault is a repo import error, missing symbol, or shared runtime-control problem, must report it as failure or replan evidence instead of narrowing the verify target away.
- If the fault is purely ambient environment drift that the lane cannot legitimately fix in repo code, must stop and surface that mismatch instead of improvising installs.

## Few-shot examples

- Example: the lane owns `pkg/io/json.py`, but the live traceback points to a helper import in `pkg/_compat.py`.
  Refresh `ci_scoped_status(...)` on `pkg/_compat.py`, widen once, patch the helper, and rerun the exact assigned verify command.
  Do not patch the failing test first.
- Example: the assigned pytest command dies during collection because `pkg/__init__.py` imports a missing compatibility symbol before the named target loads.
  Keep that collection crash as failure evidence and hand it to replan if the lane boundary is wrong.
  Do not declare success just because LSP on the owned file is clean.
