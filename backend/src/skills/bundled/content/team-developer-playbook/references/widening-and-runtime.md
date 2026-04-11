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
- Never widen into config or harness files unless live evidence proves they own the bug, and never use git/history probes to argue that a newly broken shared surface was "pre-existing".

## Runtime completion rules

- Must treat failing tests and verify commands as evidence first, not automatic test ownership.
- Must not report success on a runtime-owned lane until one assigned runtime verification command passes.
- Never claim completion from syntax-only, LSP-only, or readback-only evidence.

## Runtime mismatch rules

- If the exact verify command fails before the named target collects, must treat that as a still-red surface.
- If the fault is a repo import error, missing symbol, or shared runtime-control problem, must report it as failure or replan evidence instead of narrowing the verify target away.
- If the fault is purely ambient environment drift that the lane cannot legitimately fix in repo code, must stop and surface that mismatch instead of improvising installs.
- If the runner itself is missing or import-time modules fail before the named test loads, may do one existing-environment probe, then must stop ambient setup retries.

## Few-shot examples

- Example: the lane owns `pkg/io/json.py`, but the live traceback points to a helper import in `pkg/_compat.py`.
  Refresh `ci_scoped_status(...)` on `pkg/_compat.py`, widen once, patch the helper, and rerun the exact assigned verify command.
  Do not patch the failing test first.
- Example: the lane owns `pkg/tests/test_compat.py::test_deprecation`, but a broader assigned verify now dies during warning-filter parsing because a shared import path warns on import after a sibling edit.
  Confirm that shared import chain once with live traceback evidence, then either widen exactly to that chain or stop with blocker evidence for replanning.
  Do not reverse-engineer the prior state from `git diff`, and do not bypass startup with warning/config overrides just to keep an unowned lane moving.
- Example: the assigned pytest command dies during collection because `pkg/__init__.py` imports a missing compatibility symbol before the named target loads.
  Keep that collection crash as failure evidence and hand it to replan if the lane boundary is wrong.
  Do not declare success just because LSP on the owned file is clean.
- Example: `python -m pytest ...` reports `No module named pytest`, and a later import probe reports `ModuleNotFoundError: yaml`.
  Perform at most one existing-runner probe, then read the named test and owned source files and keep diagnosis on the repo surface.
  Do not turn that into `pip`, `pip3`, `conda`, or `uv` install retries.
