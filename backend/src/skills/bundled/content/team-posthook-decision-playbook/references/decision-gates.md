# Decision Gates
Use this reference only when the worker output is malformed, mixed, or the next action is unclear.
## Summary gate
- Must choose `summary` only when exact command evidence shows the assigned verify target green, or the payload had no runtime verify target.
- Worker prose is never command evidence.
- Do not summarize syntax-only, LSP-only, readback-only, wrapper-only, or still-red owned surfaces.
## Replan gate
- Choose `replan` for `benchmark_surface_mismatch`.
- Choose `replan` when the exact retry target cannot be collected, including pytest `not found`, exit code 4, `no tests ran`, missing owned command evidence, a verification-surface write warning, a verify-surface import/binding rewrite, or workspace mutation like `git stash/pop` that changes whether the target exists.
- A later green rerun does not untaint a packet that already contains a verification-surface write warning; still choose `replan`.
- Choose `replan` for wrong ownership, partial deterministic failure, or a still-red owned verify surface.
## Retry gate
- Choose `retry` only for narrow transient runtime faults on the correct task boundary.
- Do not choose `retry` when the task boundary itself is wrong.
## Evidence rule
- Never treat "all tests pass", "outdated test", "scope mismatch", "outside this task", "the test is inverted", "the import path in the test was wrong", wrapper `RC: 0`, manifest `status: ok`, a green rerun that only appeared after editing the verify surface, or results under overridden pytest config, `rootdir=/dev`, or `configfile: null` as substitutes for passing the exact owned verification target.
## Few-shot examples
- Example: the worker reports "fixed" but only shows clean LSP output while the assigned verify target is still red or missing. Choose `replan`; do not choose `summary`.
- Example: the worker says "all tests pass" from a broader suite, but the assigned node was not run, `pytest` returned `not found`/exit code 4/`no tests ran`, or that control failure appeared after `git stash/pop`. Choose `replan`; do not choose `summary`.
- Example: the worker says the packet owned that test, "`owned_failures` made the test editable", "the assertion was inverted", or "the import path in the test was wrong", then shows a green rerun, but the same packet also shows `daytona_edit_file` warning about a verification-surface write on the benchmark test. Choose `replan`; do not choose `summary`.
- Example: the worker "fixes" a chmod-based permission test by adding `geteuid()==0`, mode-bit inspection, or similar root-only simulation in production to mimic non-root access. Choose `replan`; that is environment-shaped patching, not proof of the owned contract.
- Example: the worker restores `pkg._compatibility` or a similar private owner, then edits `pkg.compatibility` in the same packet before any import-smoke or exact verify, or later relies on `pkg.compatibility.__getattr__`, caller-stack heuristics, or import-order behavior and the suite dies during warning-filter parsing. Choose `replan`; the lane skipped the required boundary check and turned a compat fix into a startup regression.
