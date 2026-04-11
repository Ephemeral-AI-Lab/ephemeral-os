# Decision Gates

Use this reference only when the worker output is malformed, the verification state is mixed, or the next action is not obvious from one read.

## Summary gate

- Must choose `summary` only when the assigned verify target is green or the payload had no runtime verify target.
- Must not summarize syntax-only, LSP-only, or readback-only evidence on a runtime-owned lane.
- Must not summarize a still-red owned verify surface.

## Replan gate

- Must choose `replan` for `benchmark_surface_mismatch`.
- Must choose `replan` when the exact retry target cannot be collected.
- Must choose `replan` for wrong ownership, partial deterministic failure, a still-red owned verify surface, or a systemic runtime/control failure that the same worker boundary cannot fix.

## Retry gate

- Must choose `retry` only for narrow transient runtime faults.
- Must not choose `retry` when the task boundary itself is wrong.

## Evidence rule

- Never accept "outdated test", "scope mismatch", or "outside this task" as a substitute for passing owned verification.

## Few-shot examples

- Example: the worker reports "fixed" but only shows clean LSP output while the assigned verify target is still red or missing.
  Choose `replan`.
  Do not choose `summary`.
- Example: the worker's exact pytest command fails during collection because the repo imports a missing shared symbol before the named target loads.
  Choose `replan`, not `retry`, unless the worker already proved the same boundary can repair that shared import crash.
