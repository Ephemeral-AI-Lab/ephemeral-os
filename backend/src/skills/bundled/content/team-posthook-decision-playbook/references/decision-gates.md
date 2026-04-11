# Decision Gates

Use this reference only when the worker output is malformed, the verification state is mixed, or the next action is not obvious from one read.

## Summary gate

- Must choose `summary` only when the assigned verify target is green or the payload had no runtime verify target.
- Must not summarize syntax-only, LSP-only, or readback-only evidence on a runtime-owned lane.
- Must not summarize a still-red owned verify surface.

## Replan gate

- Must choose `replan` for `benchmark_surface_mismatch`.
- Must choose `replan` when the exact retry target cannot be collected.
- Must choose `replan` for wrong ownership, partial deterministic failure, or a still-red owned verify surface.

## Retry gate

- Must choose `retry` only for narrow transient runtime faults.
- Must not choose `retry` when the task boundary itself is wrong.

## Evidence rule

- Never accept "outdated test", "scope mismatch", or "outside this task" as a substitute for passing owned verification.

## One-shot example

If the worker reports "fixed" but shows only clean LSP output and the assigned verify target is still red or missing, choose `replan`.

Must not choose `summary`.
