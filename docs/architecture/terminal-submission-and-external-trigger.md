# Terminal Submission And External Triggers

Team-mode agents finish by calling a terminal submission tool. The query loop stops after the terminal tool call, and the executor reads structured metadata written by that tool.

## Terminal Tools

- Planners: `submit_plan`
- Replanners: `submit_replan`
- Developers and reviewers: `submit_task_success` or `request_replan`
- Parent summarizers: `submit_task_success` or `request_replan`

`submit_plan` and `submit_replan` write `resolved_plan` and `plan_is_replan`. `submit_task_success` and `request_replan` write `task_summary` and `task_summary_type`.
For `submit_plan` and `submit_replan`, every `new_tasks` item carries the full task briefing in `spec`; no separate short `description` label is required.
Planners call `submit_plan(new_tasks=[...])` only; replanners call `submit_replan(new_tasks=[...], cancel_ids=[...])` only. They do not submit free-text `output` or `summary` fields. The submission tools append the full structured task JSON to the parent detail as `Initial Plan` or `Initial Replan`, including ids, assignments, acceptance criteria, dependencies, and scope paths.
Developers and reviewers should use `submit_task_success(summary=...)` for evidence-rich completion notes and `request_replan(reason=...)` when the lane is blocked or still red. Parent summarizers use the same two-tool surface to either write the planner/replanner roll-up or replan the summarized parent when child evidence stays unresolved.

## Executor Dispatch

The executor maps terminal metadata to one `TaskStatusUpdate`, and
`TaskQueue` hands that update to `TaskStatusHandler`:

- `submit_plan(...)` becomes `TaskStatusUpdate(EXPANDED, plan=...)`.
- `submit_replan(...)` becomes `TaskStatusUpdate(EXPANDED, replan=...)`.
- `submit_task_success(summary=...)` becomes `TaskStatusUpdate(DONE, summary=...)`.
- `request_replan(reason=...)` becomes `TaskStatusUpdate(REQUEST_REPLAN, summary=...)`.

Planner and replanner parents with children do not become `done` at submission
time. They move through `expanded`; after all direct children are terminal,
`TaskStatusHandler` moves them to `expanded_awaiting_summary`, injects a
dispatchable `parent_summarizer` sidecar task, and only finalizes them as
`done` after the roll-up is durably submitted.

## Parent Summary Sidecar

The parent-summary path is now a first-class team task, not an external trigger.
When every direct child of a planner or replanner parent is terminal,
`TaskStatusHandler` creates a READY `parent_summarizer` sidecar with
`fired_by_task_id` pointing at the awaiting-summary parent. The normal executor
runs it with `read_task_details` and one terminal submission tool; successful
submission calls `NoteManager.submit_summary(...)` for the authoritative parent
roll-up and then finalizes the parent.
If the summarizer finds unresolved child evidence, it submits
`request_replan(reason=...)` instead; the executor replans the summarized parent
rather than marking it `done`.
