# Plan: Planning Tools — `draft_task_plan` + `submit_task_plan`

## Context

The team runtime now uses the terminal-tools architecture end to end:

- no post-run submission phase
- submission tools are available in the main query loop
- terminal tools write structured data to task metadata
- the query loop stops immediately when a role-allowed terminal tool is called
- the executor dispatches from structured metadata after the loop returns

This document defines the planning-specific tools on top of that runtime.

## Tool Surface

| Tool | Terminal? | Available to | Purpose |
|------|-----------|--------------|---------|
| `draft_task_plan` | No | planner, replanner | Validate a proposed plan and render an ASCII before/after preview without mutating the task graph |
| `submit_task_plan` | Yes | planner, replanner | Commit a validated plan to task metadata for executor dispatch |
| `declare_blocker` | Yes | replanner | Escalate a shared blocker to the conductor |
| `submit_task_summary` | Yes | non-planner roles | Submit success/fail completion state |
| `read_task_graph` | No | all roles | Inspect the current DAG structure |

### Important Constraint

Dependency rewiring for existing siblings is **not** part of the current runtime contract.

- `existing_tasks` is intentionally rejected instead of being accepted and ignored.
- corrective replans are expressed with `new_tasks` and `remove_tasks` only
- if a sibling must change deps, replace it with `remove_tasks + new_tasks`

This is the best fit for the current persistence model, which supports add/cancel replan application but not in-place sibling dep rewrites.

## Terminal Tools By Role

```python
terminal_tools: dict[str, set[str]] = {
    "planner": {"submit_task_plan"},
    "replanner": {"submit_task_plan", "declare_blocker"},
    "developer": {"submit_task_summary"},
    "reviewer": {"submit_task_summary"},
    "resolver": {"submit_task_summary"},
    "explorer": {"submit_task_summary"},
    "scout": {"submit_task_summary"},
}
```

## Planning Payloads

### `new_tasks`

Each new task carries the full task-definition payload the runtime needs:

```python
class NewTaskSpec(BaseModel):
    id: str
    name: str          # exact agent name or roster role hint
    spec: str          # structured briefing with Goal, Environment, Scope, Context, Acceptance Criteria
    deps: list[str] = []
    scope_paths: list[str] = []
```

### `draft_task_plan`

```python
class DraftTaskPlanInput(BaseModel):
    new_tasks: list[NewTaskSpec] = []
    remove_tasks: list[str] = []
```

`draft_task_plan` performs the same structural checks as submit:

- new task IDs must be unique and not collide with the live graph
- deps must resolve to either live task IDs or newly-created local IDs
- agents must resolve through the roster
- plan validation must pass: cycles, size limits, validator policy, shared-scope conflicts
- replanners may only remove sibling tasks at their current layer
- `existing_tasks` rewiring is rejected explicitly

On success it renders:

- current sibling graph
- projected sibling graph after `remove_tasks` and `new_tasks`
- a destructive-action summary, including running-task and descendant-cancel warnings

It does **not** mutate the task graph.

### `submit_task_plan`

```python
class SubmitTaskPlanInput(BaseModel):
    new_tasks: list[NewTaskSpec] = []
    remove_tasks: list[str] = []
```

Behavior:

- planners submit an initial `Plan`
- replanners submit a `ReplanPlan(add_tasks, cancel_ids)`
- the tool re-validates everything at commit time
- successful submission writes structured plan data to task metadata for executor dispatch

### `declare_blocker`

`declare_blocker(root_cause_paths, reason, suggestion?)` is a replanner-only main-loop terminal tool.

It writes a structured blocker declaration to task metadata and exits the loop so the executor can hand it to the conductor.

### `read_task_graph`

`read_task_graph(scope="parent" | "global")` is a read-only context tool that renders the current DAG with IDs, agents, status, deps, and scope hints.

## Runtime Flow

### Planner

1. Read context and notes.
2. Shape child work as `new_tasks=[...]`.
3. Optionally preview with `draft_task_plan(new_tasks=[...])`.
4. Commit with `submit_task_plan(new_tasks=[...])`.
5. Query loop stops.
6. Executor reads `resolved_plan` and dispatches `TaskCenter.complete_task()`.

### Replanner

1. Read failure evidence, notes, and graph context.
2. Preview a corrective plan with `draft_task_plan(new_tasks=[...], remove_tasks=[...])`.
3. Commit with `submit_task_plan(new_tasks=[...], remove_tasks=[...])`.
4. If the failure is a shared cross-sibling blocker instead, call `declare_blocker(...)`.
5. Query loop stops.
6. Executor dispatches either replan application or conductor blocker flow.

## What Does Not Change

- `TaskCenter.complete_task()` remains the single executor dispatch point
- `PlanExpander.expand_submitted_plan()` still handles planner expansion
- `TaskCenter.apply_replan()` still applies add/cancel replans atomically
- external-trigger pause assessment and checkpoint notes stay separate from planning submission

## Deferred Work

- in-place sibling dependency rewiring
- a replan model that can express add/cancel/update in one atomic payload
- executor-driven freshness injection before submission

Those can be added later, but they are intentionally out of scope for the current architecture.

## Verification

1. Unit: `draft_task_plan` rejects `existing_tasks` rewires explicitly.
2. Unit: `submit_task_plan` re-validates and writes `resolved_plan`.
3. Unit: `declare_blocker` writes structured blocker metadata for executor dispatch.
4. Unit: executor reconstructs `BlockerDeclaration` from task metadata.
5. Integration: replanner flow `read_task_graph -> draft_task_plan -> submit_task_plan -> TaskCenter.apply_replan`.
6. Integration: blocker flow `declare_blocker -> executor._read_result -> conductor.create_blocker`.
