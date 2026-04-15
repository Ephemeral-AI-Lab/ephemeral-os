# Completion Contract

Use this reference only when `target_paths` is a single file or a short fixed file list.

## Task/Goal

- The scout scope is a single file or a short fixed list and you are preparing the handoff.

## Avoid

- Never subdivide a single file just because it is long; only name real seams the downstream planner should schedule.
- Never claim code was created, fixed, patched, or refactored.

## Workflow

- Must keep the handed scope itself as the deliverable.
- The Task Center note is the durable handoff. The final message is only a short prose acknowledgment.
- The note should usually cover `Scope`, `Files mapped`, `Entry points`, `Owner seam`, `Suggested subdivisions`, and `Gaps`.
- If the draft is only a JSON object or only `Mapped pkg/cli.py`, it is unfinished.
- For single-file or short fixed file-list scouts, `suggested_subdivisions` should usually be `[]` or `none`.

## Expected Outcome

- The scout handoff is short, durable, and scoped exactly to the handed file set.
