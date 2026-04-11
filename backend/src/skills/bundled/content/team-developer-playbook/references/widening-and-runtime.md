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

## One-shot example

If the lane owns `pkg/io/json.py`, but the exact failure is a missing import from `pkg/_compat.py`, you may widen once:

- Refresh `ci_scoped_status(...)` on `pkg/_compat.py`
- Edit `pkg/_compat.py`
- Run the exact assigned runtime verification command

Must not patch the failing test first.
Must not report success from `py_compile` alone.
