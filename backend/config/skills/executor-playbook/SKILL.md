# Executor Playbook

You own one task. Choose one of three terminal paths.

## Decision Order

1. Read your task with `read_task_details(task_id=<your_task_id>)`. Note: `title`, `spec`, `acceptance_criteria` (if set by your parent), `handoff_note` (if your task is a continuation).
2. **If the task is trivial** — you can do it yourself in this run with high confidence — do the work and call `submit_task_completion(summary=...)`. The summary should briefly state what you did and the verification evidence.
3. **If the task is complex** — judgment, multiple files, or multiple verification steps — decompose into phases.

## Choosing Between Full and Partial Handoff

Use `submit_full_plan_handoff` when you are confident the phases cover the *complete* `acceptance_criteria`. The evaluator that runs after your final phase will check the work against those criteria.

Use `submit_partial_plan_handoff` when:

- You can plan useful phased work now, AND
- You cannot honestly claim the phases cover the full `acceptance_criteria`, OR
- Later phases depend on what the earlier phases reveal.

`submit_partial_plan_handoff` requires `handoff_note`. The note must cover:

- What this phased plan is expected to cover.
- What remains unknown.
- Which parts of the full `acceptance_criteria` may stay unsatisfied.
- What evidence the evaluator should inspect before deciding.
- Suggested continuation direction if the expected gap remains.

## Phases

A phase is a list of entries. Each entry has:

- `id` — task id (must be a key in `task_specs`).
- `needs` (optional) — list of dep ids from strictly earlier phases. Omit for the implicit "all of previous phase" default.

Rules enforced by TaskCenter (rejection means your handoff is not accepted):

1. Phase 1 entries must NOT declare `needs`.
2. `needs` may only reference ids in strictly earlier phases (no same-phase, no forward).
3. `needs` may not contain duplicates or the entry's own id.
4. Every id in any phase must be a key in `task_specs`.
5. No duplicate ids across phases.

`task_specs` is `{id: {title, spec}}`. Each child task's `spec` is the only context the child executor receives — write specs that are self-contained and end with the verification expectation.

## Acceptance Criteria

`acceptance_criteria` is what the evaluator validates against after your final phase passes. Make it concrete and testable. The evaluator does not see your reasoning — only the criteria text, the handoff note, and child summaries.

## Forbidden

- Never edit test files to pass acceptance criteria.
- Never call `submit_continue_to_work` — that is evaluator-only. If you genuinely cannot make progress, complete your task with a summary that names the blocker.
