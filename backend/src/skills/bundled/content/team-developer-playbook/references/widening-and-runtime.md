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
- If the fault is a repo import error, missing symbol, or shared runtime-control problem, must treat that shared chain as red evidence; if it started after your edit, repair or revert that edit before any new target-specific diagnosis.
- If the fault is purely ambient environment drift that the lane cannot legitimately fix in repo code, including root or OS permission semantics that invalidate a test setup, must stop and surface that mismatch instead of editing tests or improvising installs.
- If the runner itself is missing or import-time modules fail before the named test loads, may do one existing-environment probe, then must stop ambient setup retries.
- Must not use warning/config overrides, blank `addopts`, or alternate pytest config as proof while normal startup is red.

## Few-shot examples

- Example: the lane owns `pkg/io/json.py`, but the live traceback points to a helper import in `pkg/_compat.py`.
  Refresh `ci_scoped_status(...)` on `pkg/_compat.py`, widen once, patch the helper, and rerun the exact assigned verify command.
  Do not patch the failing test first.
- Example: the lane owns `pkg/io/hdf.py`, but `pytest pkg/io/tests/test_hdf.py -x` dies during collection because the verify surface imports a deprecated or missing private symbol through `pkg/_compat.py`.
  Confirm that import chain once with live traceback evidence, widen only to the adjacent production/import path if it truly owns the fix, or stop with blocker evidence for replanning; `owned_failures` does not authorize editing the verify file.
  Do not patch `pkg/io/tests/test_hdf.py`, `pkg/tests/test_compat.py`, or any other verification-surface import just to make collection pass, and if you add a compat shim, prove the exact import path and symbol spelling before returning to pytest.
- Example: editing `pkg/tests/test_compat.py` returns `daytona_edit_file: verification-surface write allowed in advisory mode`.
  Revert that test edit, keep the red runtime surface, and widen only to the adjacent production/import chain that owns the failure.
  Do not keep the modified test in the fix packet.
- Example: a verify file removes read bits or enters an unreadable directory, but the runtime is UID 0 or on an OS that still reads the path.
  Treat that as an ambient environment mismatch after confirming the owned production path is not the first failing boundary.
  Do not skip, xfail, or rewrite the verify file just to make the benchmark look green.
