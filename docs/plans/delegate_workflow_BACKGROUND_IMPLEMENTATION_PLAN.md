# delegate_workflow Background Workflow Implementation Plan

Status: draft
Date: 2026-06-01

## User Decisions

- Tool name: `delegate_workflow`.
- `delegate_workflow` is not a terminal tool.
- Workflow delegation is managed by the background manager.
- `delegate_workflow` returns a workflow handle immediately instead of blocking
  until the child workflow finishes.
- `delegate_workflow` is allowed after the parent generator has already edited.
- Only one delegated workflow may be outstanding for a parent generator task in
  the first implementation.
- Add `check_workflow_status` to render workflow progress and final outcomes.
  It accepts `workflow_id` plus optional `workflow_task_id`: `workflow_id` alone
  renders the workflow big picture, and both ids render the delegated task
  detail.
- Add `cancel_workflow` to cancel delegated workflow work.
- Workflow completion should also be pushed by notification, with workflow
  outcomes printed into the parent conversation.
- Cancelling a delegated workflow immediately cancels nested delegated workflow
  work below it.
- Delegated workflow work stops when the parent generator crashes or is
  cancelled.
- No workflow-owned timeout semantics in this phase.
- A generic `set_timeout` tool is planned later for stopping agent waiting
  cycles while long-running work continues, including workflow monitoring.
- `delegate_workflow` is not advisor-gated. Advisor approval stays on
  `submit_generator_outcome`, which now reviews the parent's synthesized
  outcome after delegated results are delivered.
- A delegating generator synthesizes the child workflow result into its own
  outcome instead of passing the child's flattened outcomes through (the
  upward synthesis boundary).

## Goal

Replace the terminal handoff model with an agent-facing background workflow
capability. A generator agent should be able to start delegated workflow work,
continue doing useful local work, inspect progress when it chooses, cancel stale
delegations, and submit its own final generator outcome only after outstanding
workflow work has been resolved.

Target control flow:

```text
parent generator task RUNNING
  -> may edit or run local tools first
  -> delegate_workflow(goal=...)
  -> TaskCenter starts child Workflow -> Iteration -> Attempt
  -> background manager registers task_type="workflow"
  -> delegate_workflow returns workflow_task_id + workflow_id immediately

parent agent continues
  -> can use normal tools
  -> cannot start a second outstanding delegated workflow
  -> can call check_workflow_status(workflow_id)
  -> can call check_workflow_status(workflow_id, workflow_task_id)
  -> can call cancel_workflow(workflow_task_id)
  -> receives a workflow completion notification with printed outcomes
  -> eventually calls submit_generator_outcome
```

This replaces:

```text
parent generator calls submit_workflow_handoff terminal
  -> parent agent run ends
  -> child workflow closes later
  -> parent generator is directly marked DONE or FAILED
```

It also replaces the earlier synchronous draft:

```text
delegate_workflow blocks inside the parent tool call
  -> private workflow waiter resolves final outcomes
  -> parent agent resumes only after child workflow closes
```

The background model is the target because it preserves agentic flexibility
during delegated work and aligns with the Phase 3T PTY/background-manager
contract.

## Design Rationale: Per-Delegation Context Boundaries

Background delegation gives every delegation hop a clean context boundary in
both directions. This is the core reason to prefer it over both the terminal
handoff and an in-parent orchestration script.

Downward isolation (goal in, fresh planner):

- `delegate_workflow` ships only a goal string. TaskCenter bootstraps the child
  Workflow whose planner decomposes that goal in a fresh ContextEngine packet.
- The parent never authors or holds the child's task DAG. Decomposition
  reasoning stays out of the parent conversation, and the plan is re-derived
  from store state instead of inherited from the parent's current context.

Upward synthesis (one outcome out, subtree encapsulated):

- Today `AttemptOrchestrator.apply_child_workflow_outcome` writes the child
  workflow's flattened outcomes directly onto the parent generator task, and
  the handing-off generator is terminal and contributes no outcome of its own.
  The generator is a transparent conduit, so a nested subtree's raw outcomes
  leak upward into the parent attempt's reducer input.
- In the background model the generator stays `RUNNING`, receives the child
  result, and emits one synthesized outcome through `submit_generator_outcome`.
  Each generator becomes the reduction point for its delegated subtree instead
  of a window into it.
- This restores compositionality to the recursive `Outcome` algebra: one node,
  one synthesized outcome, children encapsulated. The parent attempt's reducer
  reads one high-signal outcome per delegating generator, and failure
  attribution reads as "the generator weighed the delegated result and decided
  X" rather than a flattened pile of grandchild outcomes.
- Encapsulation is not lossy at the audit layer: the child workflow's durable
  outcomes remain in TaskCenter stores and stay inspectable through
  `check_workflow_status`.

## Ownership Boundary

TaskCenter owns durable workflow state:

```text
Workflow -> Iteration -> Attempt -> task rows -> outcomes
```

The background manager owns the live agent-facing handle:

```text
WorkflowBackgroundRecord {
  workflow_task_id
  workflow_id
  parent_task_id
  parent_attempt_id
  agent_id
  status
  started_at
  last_seen_at
  final_status
  final_outcomes
  terminal_reported_by_status_tool
  terminal_reported_by_notification
  cancelled_by_cancel_tool
}
```

`delegate_workflow`, `check_workflow_status`, and `cancel_workflow` should call
TaskCenter lifecycle services for durable state and use the background manager
for ownership checks, active-work accounting, delivery state, and cancellation
bookkeeping.

Do not make the background manager the Workflow lifecycle owner. It is the
supervisor and handle registry.

## Non-Goals

- Do not add workflow timeout or deadline behavior.
- Do not implement `set_timeout` in this phase.
- Do not add a private blocking `WorkflowCompletionWaiter`.
- Do not call `EphemeralAttemptAgentLauncher.wait_for_idle()` from inside
  workflow tools.
- Do not route delegated workflow completion through
  `wait_background_tasks(timeout=...)`; workflow status and notifications are
  the delivery surface.
- Do not complete or fail the parent generator directly when a child workflow
  closes.
- Do not introduce peer-to-peer agent messaging.
- Do not hard-gate `delegate_workflow` with `AdvisorApprovalPreHook`; advisor
  review stays on `submit_generator_outcome`.
- Do not redesign planner, reducer, root workflow bootstrap, or the
  Workflow -> Iteration -> Attempt durable model.

## Current Anchors

- Current terminal tool:
  `backend/src/tools/submission/generator/submit_workflow_handoff/submit_workflow_handoff.py`
- Generator submission context:
  `backend/src/tools/submission/context/generator.py`
- Workflow start:
  `backend/src/task_center/workflow/starter.py`
- Workflow close route:
  `backend/src/task_center/workflow/lifecycle.py`
- Parent task mutation:
  `backend/src/task_center/attempt/orchestrator.py`
- Background manager:
  `backend/src/engine/background/task_supervisor.py`
  `backend/src/engine/background/dispatch.py`
  `backend/src/engine/background/history.py`
- Background tools:
  `backend/src/tools/background/check_background_task_result/check_background_task_result.py`
  `backend/src/tools/background/cancel_background_task/cancel_background_task.py`
  `backend/src/tools/background/wait_background_tasks/wait_background_tasks.py`
- In-flight background prehook:
  `backend/src/tools/_hooks/require_no_inflight_background_tasks.py`
- Executor profile:
  `backend/src/agents/profile/main/executor.md`
- Phase 3T background-manager model:
  `docs/plans/sandbox-rust-external-migration-PHASE-3T-PTY-COMMAND-DESIGN.md`

## Tool Surface

### delegate_workflow

Add a non-terminal tool named `delegate_workflow`.

Input:

```json
{
  "goal": "The delegated workflow goal, including relevant findings and constraints."
}
```

Output:

```json
{
  "workflow_task_id": "wf_1",
  "workflow_id": "child workflow id",
  "status": "running",
  "message": "Started delegated workflow wf_1. Use check_workflow_status to inspect progress or cancel_workflow to stop it."
}
```

Tool metadata:

- `is_terminal_tool=False`
- `intent=Intent.LIFECYCLE`
- background-manager task type: `workflow`
- no generic `background` argument required

`delegate_workflow` is itself a lifecycle start tool. It should return quickly
after starting TaskCenter workflow state and registering the background record.
It should not be dispatched through the generic `background=True` wrapper,
because the workflow handle is the tool's primary result.

The tool is allowed after the parent generator has already called edit-capable
tools. The old "handoff before edits" reminder becomes obsolete for this model.

The first implementation should reject a second outstanding delegated workflow
for the same parent generator task. Outstanding means running, cancelling, or
terminal but not yet delivered to the parent conversation. The rejection should
name the existing `workflow_id` and `workflow_task_id`, and instruct the agent to
call `check_workflow_status` or `cancel_workflow`.

### check_workflow_status

Add `check_workflow_status`.

Input:

```json
{
  "workflow_id": "child workflow id"
}
```

Detailed input:

```json
{
  "workflow_id": "child workflow id",
  "workflow_task_id": "wf_1"
}
```

`workflow_id` is required. `workflow_task_id` is optional:

- `workflow_id` only renders the big picture for the ongoing workflow: workflow
  status, active iteration/attempt, task graph progress, and current terminal
  outcomes if any.
- `workflow_id` plus `workflow_task_id` renders the detailed delegated task view
  for the background-manager record returned by `delegate_workflow`, including
  ownership/delivery state and any final outcomes tied to that parent agent.

Output while running:

```json
{
  "workflow_task_id": "wf_1",
  "workflow_id": "child workflow id",
  "status": "running",
  "progress": "Rendered concise progress summary.",
  "workflow": {
    "status": "open",
    "current_iteration_id": "...",
    "current_attempt_id": "...",
    "tasks": [
      {
        "task_id": "...",
        "role": "planner | generator | reducer",
        "status": "pending | running | waiting_workflow | done | failed | blocked",
        "agent_name": "executor"
      }
    ]
  },
  "outcomes": []
}
```

`workflow_task_id` is present when the caller supplies it, or when the status
tool can unambiguously identify the caller's one outstanding workflow record for
that `workflow_id`.

Output when terminal:

```json
{
  "workflow_task_id": "wf_1",
  "workflow_id": "child workflow id",
  "status": "succeeded | failed | cancelled",
  "progress": "Rendered final workflow summary.",
  "workflow": {
    "status": "succeeded | failed | cancelled"
  },
  "outcomes": [
    {
      "status": "success | failed",
      "role": "generator | reducer",
      "task_id": "...",
      "outcome": "..."
    }
  ]
}
```

`check_workflow_status` should mark terminal workflow results as delivered for
that parent agent only when it prints final outcomes into the current
conversation. That prevents a generator from submitting a final outcome without
first receiving the child workflow result.

The background manager may also push a workflow completion notification with the
same final outcomes. That notification also counts as delivery and should mark
`terminal_reported_by_notification` so later status checks do not duplicate the
surprise completion message.

### cancel_workflow

Add `cancel_workflow`.

Input:

```json
{
  "workflow_task_id": "wf_1",
  "reason": "The delegated branch is no longer needed."
}
```

Output:

```json
{
  "workflow_task_id": "wf_1",
  "workflow_id": "child workflow id",
  "status": "cancelled",
  "message": "Cancelled delegated workflow wf_1."
}
```

`cancel_workflow` is explicit model-requested cancellation. It should mark the
background record as cancelled and call TaskCenter cancellation code to close or
stop the child workflow without completing the parent generator task.

Cancellation must cascade immediately into nested delegated workflows owned by
tasks inside the cancelled workflow. The cascade should use the same TaskCenter
cancellation path and mark those nested background records as parent-cancelled
instead of explicit user/model cancellations.

## Background Manager Integration

Reuse the existing background manager/handler modules. Do not create a new
`live_work` module.

Extend the manager's typed records:

```text
task_type = "workflow" | "pty_command" | "subagent" | existing legacy type
```

Recommended workflow manager functions:

```text
register_workflow(...)
find_outstanding_workflow_for_parent(...)
collect_workflow_completions(...)
mark_workflow_reported_by_status_tool(...)
mark_workflow_reported_by_notification(...)
cancel_workflow_by_agent(...)
count_by_agent(...)
terminate_for_parent_exit(...)
```

Workflow record fields:

```text
WorkflowBackgroundRecord {
  workflow_task_id
  workflow_id
  parent_task_id
  parent_attempt_id
  task_center_run_id
  agent_id
  goal
  status
  final_status
  final_outcomes
  terminal_reported_by_status_tool
  terminal_reported_by_notification
  cancelled_by_cancel_tool
  parent_cancelled
  started_at
  last_seen_at
}
```

The manager stores lightweight supervision state only. Workflow progress and
outcomes stay in TaskCenter stores and are rendered on demand.

`register_workflow` must enforce the one-outstanding-workflow rule for the
parent generator task. If a workflow record already exists and is running,
cancelling, or terminal but undelivered, `delegate_workflow` rejects the second
request and returns the existing ids.

`count_by_agent` must include workflow records that are running or terminal but
not yet delivered to the parent conversation. Terminal tools should be blocked
while such records exist. A parent generator can submit its final
`submit_generator_outcome` only after each delegated workflow is either:

- cancelled by `cancel_workflow`; or
- completed and delivered through `check_workflow_status` or an equivalent
  workflow completion notification.

The Phase 3T PTY plan and current code have bounded wait semantics for generic
background tasks (`wait_background_tasks(timeout=...)`) and per-command PTY
timeouts (`exec_command(..., timeout=...)`). This plan does not implement
`set_timeout` yet, but reserves it as a future generic agent attention timer.
Delegated workflows are controlled through status, pushed notification, explicit
cancel, and parent-exit cleanup.

Future `set_timeout` semantics should be separate from workflow lifecycle
semantics:

- for `exec_command` and `run_subagent`, it lets an agent stop waiting cycles
  and re-evaluate whether to check, continue waiting, or cancel;
- for workflow, it lets an agent wake up and call `check_workflow_status` on
  long-running workflow work;
- it does not cancel the workflow by default;
- it does not create a workflow timeout, deadline, or scheduler policy.

## TaskCenter Lifecycle Changes

### Parent Task State

The parent generator task should remain `RUNNING` while delegated workflows run.

Target parent state:

```text
RUNNING
  -> edits/local tool calls may already have happened
  -> delegate_workflow starts at most one outstanding child workflow
  -> parent remains RUNNING
  -> terminal prehook blocks while active/undelivered workflows exist
  -> submit_generator_outcome eventually marks DONE or FAILED
```

Do not use `WAITING_WORKFLOW` for background `delegate_workflow`. That state
means the attempt is waiting for TaskCenter to resolve the parent task. In the
new model, the parent agent is still alive and responsible for its own terminal
outcome.

### Child Workflow Start

Current `WorkflowStarter.start(...)` marks the parent task
`RUNNING -> WAITING_WORKFLOW`. The background path needs a start mode that does
not mutate the parent task status.

Preferred implementation:

- keep root workflow bootstrap behavior unchanged;
- keep existing waiting behavior only if needed for legacy migration tests;
- add a background delegation start path, for example
  `WorkflowStarter.start_background_child(...)`, that:
  - validates parent task exists, is attempt-bound, and is `RUNNING`;
  - rejects if the parent generator already has an outstanding workflow record;
  - creates Workflow, Iteration, and initial Attempt;
  - starts the child Attempt;
  - leaves parent task status `RUNNING`;
  - does not write `child_workflow_id` on the parent task;
  - returns `StartedWorkflow`.

For background delegation, the durable back-link is `Workflow.parent_task_id`.
The live handle is the background manager record. Do not depend on
`Task.child_workflow_id`, even though the first pass allows only one outstanding
delegated workflow, because the live delivery/cancellation state belongs to the
background manager record and the durable relationship already lives on
`Workflow.parent_task_id`.

### Child Workflow Close

Current close route:

```text
WorkflowLifecycle._route_close(...)
  -> AttemptOrchestrator.apply_child_workflow_outcome(...)
  -> parent generator WAITING_WORKFLOW -> DONE or FAILED
  -> child outcomes copied onto parent task
```

Target close route:

```text
WorkflowLifecycle._route_close(...)
  -> TaskCenter closes child workflow status
  -> background manager observes/collects terminal workflow
  -> parent task remains RUNNING
  -> final outcomes are printed by notification or check_workflow_status
```

Do not write child outcomes onto the parent task. The parent generator's own
`submit_generator_outcome` is the only writer of its task outcome. This is the
upward synthesis boundary from Design Rationale: the generator integrates the
child result into one outcome instead of passing the child's flattened outcomes
through.

Failed child workflows become information returned to the parent agent, not
automatic parent task failure.

### Workflow Cancellation

Add a TaskCenter cancellation path for delegated workflows.

Minimum expected behavior:

- reject cancellation for workflows not owned by the calling agent's workflow
  background record;
- mark the workflow `CANCELLED` if it is still open;
- close or cancel open iterations and attempts consistently;
- cancel or prevent further child agent launches for that workflow;
- immediately cascade cancellation to any nested delegated workflows below it;
- mark the workflow background record `cancelled`;
- do not mutate the parent generator task outcome.

If a delegated workflow is already terminal, `cancel_workflow` should return the
current terminal status instead of trying to reopen or re-cancel it.

Timeout-based cancellation remains out of scope.

### Parent Crash Or Cancellation

If the parent generator crashes, is cancelled, or exits through a
non-continuing lifecycle path while delegated workflow work is still running,
the background manager should stop the delegated workflow immediately through the
same TaskCenter cancellation path.

Expected behavior:

- call `terminate_for_parent_exit(reason=...)` for workflow records owned by the
  parent agent/task;
- cancel the child workflow and any nested delegated workflows below it;
- mark records as parent-cancelled, not explicit user/model cancellations;
- emit at most one notification/audit event for the stopped workflow work;
- avoid writing any parent generator task outcome.

## Agent Profile And Prompting

Move workflow tools into executor allowed tools.

Executor profile target:

```yaml
allowed_tools:
  - delegate_workflow
  - check_workflow_status
  - cancel_workflow
  - ...
terminals:
  - submit_generator_outcome
```

Prompt guidance:

- `delegate_workflow` starts background delegated work and returns a handle.
- It can be used after local edits have started.
- Only one delegated workflow may be outstanding; resolve or cancel it before
  attempting another delegation.
- Use `check_workflow_status(workflow_id)` for workflow big-picture progress.
- Use `check_workflow_status(workflow_id, workflow_task_id)` for the delegated
  task detail and delivery state.
- Use `cancel_workflow` when the delegated branch is obsolete or harmful.
- Workflow completion notifications print final outcomes into the conversation.
- Synthesize the delegated workflow result into your own generator outcome. Do
  not echo the child outcomes verbatim; integrate them with your local work.
- Do not call `submit_generator_outcome` while delegated workflows are still
  running or final outcomes are undelivered.
- `submit_generator_outcome` remains the only generator terminal.

Remove the old after-edit warning. `request_workflow_after_edit` and prompt
guidance such as "Do not use handoff after editing has started" contradict the
new model and should be deleted or rewritten away from timing restrictions.

Remove `submit_workflow_handoff` from the terminal catalog. `delegate_workflow`
does not belong in `tools/_terminals/registry.py`.

Advisor approval stays tied to terminal submissions. Do not gate
`delegate_workflow` with `AdvisorApprovalPreHook`.

Rationale:

- `AdvisorApprovalPreHook` is a handshake gate: it refuses the call until the
  agent has already run `ask_advisor(tool_name=...)` and received
  `verdict="approve"`. Imposing that on `delegate_workflow` would turn a fast
  handle-returning kickoff into a blocking per-delegation approval round-trip,
  out of step with the other background-manager tools (`run_subagent`,
  background `exec_command`), which are not advisor-gated.
- The irreversible commit that justified gating the old handoff moved to
  `submit_generator_outcome`. That terminal stays advisor-gated and now blocks
  until delegated workflow results are delivered, so the advisor reviews the
  parent's integrated outcome with the child result already in context — a
  better review point than pre-approving a goal string before any evidence
  exists.
- Advisor approval is a judgment gate, not a safety invariant. Safety is
  enforced by the one-outstanding rule, the delivery-before-terminal gate, and
  cascade cancellation. Helper and non-terminal tools already omit the hook by
  design.

Cheaper mitigations for a wastefully scoped delegation, in place of a hard
gate:

- the one-outstanding-workflow rule already bounds runaway fanout;
- the child workflow's planner rejects a malformed goal in fresh context;
- optional soft prompt guidance can suggest a voluntary advisor call before a
  large or deeply nested delegation.

Revisit only if telemetry shows recurring delegate-then-cancel churn, and even
then prefer a soft reminder or a recursion-depth threshold over a mandatory
`ask_advisor` handshake on the hot path.

## Nested Workflow Policy

Nested delegation stays clean because each hop applies the upward synthesis
boundary: a nested generator emits one synthesized outcome, so a delegated
subtree never flattens its grandchild outcomes into an ancestor reducer.

Keep the existing depth guard unless the user explicitly expands recursion.

Rename policy surfaces:

- `DisallowNestedWorkflowHandoff` ->
  `DisallowNestedWorkflowDelegation`
- `nested_workflow_handoff_disabled` ->
  `nested_workflow_delegation_disabled`

The block message should name `delegate_workflow`, not
`submit_workflow_handoff`, and should tell the nested generator to finish with
`submit_generator_outcome`.

## File Plan

Workflow tools:

```text
backend/src/tools/workflow/delegate_workflow/
backend/src/tools/workflow/check_workflow_status/
backend/src/tools/workflow/cancel_workflow/
backend/src/tools/workflow/__init__.py
```

TaskCenter lifecycle:

```text
backend/src/task_center/workflow/starter.py
backend/src/task_center/workflow/lifecycle.py
backend/src/task_center/attempt/orchestrator.py
backend/src/task_center/attempt/orchestrator_registry.py
backend/src/task_center/_core/task_state.py
backend/src/task_center/_core/outcomes.py
```

Background manager:

```text
backend/src/engine/background/task_supervisor.py
backend/src/engine/background/dispatch.py
backend/src/engine/background/history.py
backend/src/tools/background/_lib/task_output.py
```

Optional split if `task_supervisor.py` becomes too large:

```text
backend/src/engine/background/workflow_records.py
backend/src/engine/background/workflow_notifications.py
```

Registration:

```text
backend/src/tools/_framework/factory.py
backend/src/tools/_names.py
```

Profiles and policy:

```text
backend/src/agents/profile/main/executor.md
backend/src/tools/submission/generator/_prompt_guidance.py
backend/src/tools/_hooks/disallow_nested_workflow_handoff.py
backend/src/tools/submission/notification_triggers/nested_workflow_handoff_disabled.py
backend/src/tools/submission/notification_triggers/request_workflow_after_edit.py
backend/src/tools/_terminals/registry.py
```

The nested hook/reminder files may be renamed as part of the cleanup.
`request_workflow_after_edit.py` should be removed or rewritten so it no longer
discourages delegation after edits.

## Implementation Phases

### Phase 1: Tool Names And Registration

- Add `delegate_workflow`, `check_workflow_status`, and `cancel_workflow`.
- Register them as workflow tools.
- Remove `submit_workflow_handoff` from the default registered tool set.
- Add `DELEGATE_WORKFLOW_TOOL_NAME`, `CHECK_WORKFLOW_STATUS_TOOL_NAME`, and
  `CANCEL_WORKFLOW_TOOL_NAME`.
- Remove or deprecate `SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME`.

Verification:

```bash
uv run pytest -q \
  backend/tests/unit_test/test_tools/test_submission_tool_registration.py \
  backend/tests/contracts/test_tool_intent_drift.py
```

### Phase 2: Background Workflow Records

- Extend the background manager with `task_type="workflow"`.
- Add workflow record registration, lookup, ownership checks, cancellation
  marking, delivery marking, and active/undelivered counting.
- Enforce one outstanding workflow record per parent generator task.
- Track delivery by either status tool or pushed workflow notification.
- Make terminal prehooks count running or undelivered workflow records for the
  current agent.
- Add unit tests for workflow record lifecycle.

Verification:

```bash
uv run pytest -q \
  backend/tests/unit_test/test_engine/test_background_tasks.py \
  backend/tests/unit_test/test_tools/test_submission_soft_reminders.py
```

### Phase 3: Background Child Workflow Start

- Add a child-workflow start path that does not flip the parent generator to
  `WAITING_WORKFLOW`.
- Have `delegate_workflow` call that path and register the background record.
- Allow `delegate_workflow` even when the parent generator has already used
  edit-capable tools.
- Reject a second outstanding delegated workflow with the existing ids and a
  status/cancel instruction.
- Return `workflow_task_id` and `workflow_id` immediately.
- Keep root workflow bootstrap behavior unchanged.

Focused expectations:

- Parent generator remains `RUNNING` after `delegate_workflow`.
- Child workflow is `OPEN`.
- Initial child iteration and attempt exist.
- Background manager has one running workflow record for the parent agent.
- A second outstanding delegation is rejected.
- A delegation after `write_file`, `edit_file`, or `shell` is accepted.

Verification:

```bash
uv run pytest -q \
  backend/tests/unit_test/test_task_center/test_lifecycle/test_phase04_workflow_request_start.py \
  backend/tests/unit_test/test_tools/test_submission_main_role_terminals.py
```

The tool test file should be renamed or split because `delegate_workflow` is no
longer a submission terminal.

### Phase 4: Progress Rendering

- Implement `check_workflow_status`.
- Require `workflow_id`; accept optional `workflow_task_id`.
- With only `workflow_id`, render current workflow, iteration, attempt, task
  status, and dependency progress from TaskCenter stores.
- With both ids, render the detailed background workflow task record and
  delivery state for the parent agent.
- Return structured final outcomes when workflow status is terminal.
- Mark terminal workflow records delivered when final outcomes are printed.
- Add tests for running, succeeded, failed, cancelled, unknown id, wrong-agent,
  already-delivered, workflow-only, and workflow-plus-task cases.

Verification:

```bash
uv run pytest -q backend/tests/unit_test/test_tools/test_workflow_tools.py
```

### Phase 5: Cancellation

- Implement TaskCenter workflow cancellation for delegated child workflows.
- Implement `cancel_workflow`.
- Mark background records as cancelled by tool.
- Cascade immediately into nested delegated workflows below the cancelled
  workflow.
- Ensure cancellation does not write parent task outcomes.
- Ensure cancellation unblocks terminal submission once delivered.

Verification:

```bash
uv run pytest -q \
  backend/tests/unit_test/test_task_center/test_lifecycle/test_child_workflow_handoff.py \
  backend/tests/unit_test/test_tools/test_workflow_tools.py
```

The lifecycle file should be renamed or rewritten around background delegation
instead of handoff.

### Phase 6: Parent Exit And Notifications

- Define parent non-continuing behavior for active workflow records.
- On parent terminal/non-continuing exit, call
  `terminate_for_parent_exit(reason="non_cancellation_tool_request")` for
  active workflow records.
- Cancel or close active delegated workflows according to the TaskCenter
  cancellation path.
- Stop delegated workflows immediately if the parent generator crashes or is
  cancelled.
- Emit one workflow completion/cancellation notification with printed workflow
  outcomes, unless the status was already reported by `check_workflow_status` or
  `cancel_workflow`.

Verification:

```bash
uv run pytest -q \
  backend/tests/unit_test/test_engine/test_background_tasks.py \
  backend/tests/unit_test/test_tools/test_workflow_tools.py
```

### Phase 7: Profile And Architecture Docs

- Move workflow tools into executor `allowed_tools`.
- Remove `submit_workflow_handoff` from executor `terminals`.
- Update executor prose and tests.
- Remove the old after-edit workflow reminder/prose.
- Remove handoff terminal catalog entries.
- Update maintained architecture docs.

Required architecture pages:

- `docs/architecture/index.html`
- `docs/architecture/task_center/index.html`
- `docs/architecture/task_center/lifecycle.html`
- `docs/architecture/task_center/bridges.html`
- `docs/architecture/task_center/terminal-tools.html`
- `docs/architecture/tools/submission.html`
- `docs/architecture/tools/terminals.html`
- `docs/architecture/agent_loops/prompt-context.html`
- `docs/architecture/assets/search-index.js`

Verification:

```bash
uv run pytest -q \
  backend/tests/unit_test/test_agents/test_agent_markdown.py \
  backend/tests/unit_test/test_agents/test_skill_message.py \
  backend/tests/unit_test/test_tools/test_submission_generator_prompts.py

rg -n "submit_workflow_handoff|workflow handoff|handoff terminal|apply_child_workflow_outcome|WAITING_WORKFLOW -> DONE|does not return to RUNNING" \
  docs/architecture backend/src backend/tests
```

Expected remaining matches should be intentional historical notes only.

## Success Criteria

- `delegate_workflow` is visible to generator agents as a normal workflow tool.
- `delegate_workflow` returns immediately with a workflow handle.
- `delegate_workflow` is allowed after local edits have started.
- A parent generator task can have only one outstanding delegated workflow.
- `check_workflow_status(workflow_id)` renders workflow-level progress and
  terminal outcomes.
- `check_workflow_status(workflow_id, workflow_task_id)` renders delegated task
  detail and delivery state.
- `cancel_workflow` cancels delegated workflow work and marks the background
  record.
- `cancel_workflow` immediately cascades into nested delegated workflows.
- Parent generator remains `RUNNING` while delegated workflow work runs.
- Child workflow close does not mark the parent generator `DONE` or `FAILED`.
- Workflow completion notification prints final outcomes into the parent
  conversation.
- Parent crash/cancellation stops delegated workflow work.
- Parent task outcomes are written only by `submit_generator_outcome`.
- A delegating generator synthesizes the delegated workflow result into one
  generator outcome; the parent attempt reducer reads that synthesized outcome
  rather than the child workflow's flattened outcomes.
- Terminal submissions are blocked while delegated workflows are running or
  terminal-undelivered.
- No workflow timeout policy is introduced.
- `set_timeout` remains deferred as a generic agent waiting-cycle tool, not a
  workflow lifecycle timeout.
- Root workflow bootstrap and run close behavior are unchanged.

## Risks And Watchpoints

- State ownership risk: do not let the background manager become the workflow
  lifecycle owner. It owns handles and delivery state; TaskCenter owns durable
  lifecycle.
- Old-state risk: using `WAITING_WORKFLOW` for background delegation recreates
  the old workflow-engine model and prevents the parent agent from continuing.
- Lost-result risk: if terminal tools do not block terminal-undelivered workflow
  records, an agent can submit without seeing child outcomes.
- Handle leakage risk: wrong-agent, unknown, cancelled, and already-finished
  workflow handles should not expose cross-agent details.
- Cancellation risk: cancelling a workflow must stop future launches and cleanly
  close open workflow state without writing a parent generator outcome.
- Parent-exit risk: parent crash/cancellation must not leave orphan delegated
  workflows running.
- Timeout drift risk: generic background waits, command timeouts, and the future
  `set_timeout` tool must not become an implicit workflow timeout policy.
- Prompt drift risk: executor frontmatter, terminal catalog, advisor prompt, and
  architecture docs currently describe handoff as terminal or discourage
  delegation after edits.
- Synthesis-quality risk: a delegating generator could echo the child outcomes
  verbatim instead of integrating them, re-introducing the flatten-passthrough
  shape. Executor prompt guidance must push synthesize-don't-echo.
- Advisor-coverage risk: advisor review of delegated work depends on the
  terminal gate blocking until delegated results are delivered. If the delivery
  gate regresses, the parent can submit a synthesized outcome the advisor never
  saw alongside the child result.
- Test naming risk: tests that keep "handoff terminal" names after the behavior
  change will hide wrong assumptions.

## Deferred Work

- Parallel delegated workflow fanout beyond one outstanding workflow per parent
  generator task.
- Any workflow-owned timeout, deadline, or scheduler policy.
- Generic `set_timeout` for agent waiting-cycle control across `exec_command`,
  `run_subagent`, and workflow status monitoring.
