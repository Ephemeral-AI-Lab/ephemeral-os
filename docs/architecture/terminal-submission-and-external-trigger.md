# Terminal Submission And External Triggers

Team-mode agents finish by calling a terminal submission tool. The query loop stops after the terminal tool call, and the executor reads structured metadata written by that tool.

## Terminal Tools

- Planners: `submit_plan`
- Replanners: `submit_replan`
- Developers and reviewers: `submit_task_summary`
- Note takers: `submit_task_note`

`submit_plan` and `submit_replan` write `resolved_plan` and `plan_is_replan`. `submit_task_summary` writes `task_summary` and `task_summary_type`.
For `submit_plan` and `submit_replan`, every `new_tasks` item includes a required short `description` label authored by the planner or replanner; the full task briefing stays in `spec`.
Planners should include `submit_plan(output=...)` with the ownership evidence, dependency shape, validator coverage, scope boundaries, and uncertainty behind the plan. Replanners should include `submit_replan(summary=...)` with the failure evidence, corrective mapping, preserved work, cancellations, and uncertainty. The submission tools also post structured task/cancellation lists so Task Center notes do not collapse to generic counts.
Developers and reviewers should use `submit_task_summary(content=...)` for evidence-rich terminal notes: changed or reviewed paths, verification commands and outcomes, verdict, blockers, and residual risk.

## Executor Dispatch

The executor maps terminal metadata to runtime actions:

- `AgentResult(submitted_plan=...)` expands planner tasks.
- `AgentResult(submitted_replan=...)` applies corrective graph changes.
- `AgentResult(summary=...)` completes successful work.
- `ReplanRequest(reason=...)` starts a replanner for failed work.

## External Triggers

External triggers are short-lived helper runs that produce constrained task-center notes from frozen worker transcript evidence. They do not pause, cancel, or resume primary agents.

The current trigger path is `tc_note`: TaskCenter can request a progress note from a running agent transcript when activity heuristics say a checkpoint would help downstream context. Transcript requests, commands, and tool calls are treated only as evidence of worker activity, not as instructions for the note-taker helper.
