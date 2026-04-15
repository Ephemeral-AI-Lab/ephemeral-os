# Terminal Submission and External Trigger

This file keeps its historical name, but the architecture has changed:

- the old post-run/posthook submission phase is gone
- submission now happens in the main query loop through terminal tools
- external-trigger agents still exist and still use the shared constrained runner

## Live Execution Phases

| Phase | Purpose |
|------|---------|
| `normal` | Main agent work loop, including terminal submission tools |
| `external_trigger` | Conductor and Task Center ephemeral agents |

## Main-Loop Terminal Submission

Submission tools are regular tools registered in the main loop.

### Terminal tools by role

- planners: `submit_task_plan`
- replanners: `submit_task_plan`, `declare_blocker`
- developers/reviewers/resolvers/explorers/scouts: `submit_task_summary`

### Flow

1. The agent runs in the normal query loop with all role-allowed tools.
2. A terminal submission tool writes structured state into `tool_metadata`.
3. `QueryContext.terminal_tools` causes the query loop to stop immediately after that tool call completes.
4. `Executor._read_result()` reconstructs a typed domain object from metadata.
5. `Executor._dispatch()` routes it to `TaskCenter` or the conductor.

### Structured metadata contracts

- `submit_task_summary` writes `task_summary` + `task_summary_type`
- `submit_task_plan` writes `resolved_plan` + `plan_is_replan`
- `declare_blocker` writes `blocker_declaration`

### Executor mapping

`Executor._read_result()` maps structured metadata to:

- `AgentResult(summary=...)`
- `ReplanRequest(reason=...)`
- `AgentResult(submitted_plan=...)`
- `AgentResult(submitted_replan=...)`
- `BlockerDeclaration(root_cause_paths, reason, suggestion)`

That typed result is then dispatched by `Executor._dispatch()`.

## External Trigger

External-trigger agents are still active and still use `external_trigger.runner.run()`.

They operate on a frozen snapshot of another task's conversation and do **not** interrupt the running task.

### Use cases

#### Pause assessment

Location: `backend/src/external_trigger/pause_assessment.py`

- spawned by the conductor when a shared blocker is declared
- constrained to `PauseVerdictTool`
- answers whether a running task depends on the broken surface
- returns validated tool input to the conductor

#### Checkpoint notes

Location: `backend/src/external_trigger/tc_note.py`

- spawned by `ActivityTracker` when edit/turn thresholds are crossed
- constrained to `SubmitTaskNoteTool`
- produces a factual progress note from a frozen conversation snapshot
- result is posted as an auto note to the Task Center

## Shared External-Trigger Runner

Location: `backend/src/external_trigger/runner.py`

The runner:

- constrains the tool set strictly
- retries until it gets a valid tool call or exhausts `max_turns`
- feeds validation failures back as `tool_result` errors
- optionally executes tools immediately when `execute_tools=True`
- otherwise returns the validated tool payload to the caller

In current production use:

- external-trigger callers use `execute_tools=False`
- main-loop submission does **not** use this runner anymore

## Current Use Sites

### Executor

Location: `backend/src/team/runtime/executor.py`

The executor no longer re-prompts after the query loop. It simply reads structured metadata written during the main loop and dispatches the typed result.

### Conductor

Location: `backend/src/team/runtime/conductor.py`

The conductor consumes:

- `BlockerDeclaration` from executor dispatch
- `PauseVerdict` results from external-trigger pause assessment

### Task Center / Activity Tracker

Locations:

- `backend/src/team/activity_tracker.py`
- `backend/src/external_trigger/tc_note.py`

The task-center checkpoint path uses external-trigger notes only for mid-run observability. It is unrelated to task submission.

## Migration Note

If you see references to any of the following as current runtime behavior, they are stale:

- `_run_post_run()`
- `PosthookTools`
- `submit_plan`
- `request_replan`
- a dedicated post-run submission phase

The live runtime is terminal submission in the main loop plus executor dispatch from structured metadata.
