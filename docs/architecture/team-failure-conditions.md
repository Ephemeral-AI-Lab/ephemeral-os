# Team Failure Conditions

This document separates whole-run failure from task-local failure in team
coordination. A worker task can fail without failing the team run if replanning
absorbs the failure. The team run fails only through `fail_fast(...)` or when
final status computation sees the root task in `failed`.

## Failure Categories

| Category | Condition | Run result | Notes |
| --- | --- | --- | --- |
| Fatal invariant | `GraphInvariantViolation` during ready dispatch, running transition, replan dependency rewiring, snapshot restore, or failure cleanup | `failed` immediately | The executor calls `TeamRun.fail_fast("graph_invariant_violation: ...")` because the task graph is no longer schedulable with confidence. |
| Fatal budget | `BudgetExceeded` while expanding a submitted plan, creating a replanner, or applying a replan during execution | `failed` immediately | Task budget and replan budget are run-level guarantees, not task-local failures. |
| Root task terminal failure | Root task reaches `failed` | `failed` at `TeamRun.wait()` finalization | The final status reflects the root task outcome unless an earlier fatal failure reason exists. |
| Root task direct execution failure | Root agent is unknown, the root runner crashes, context construction raises, or root cleanup fails into task failure | Usually `failed` | These first mark the root task failed or request replanning. The run fails if recovery does not produce a successful root outcome. |
| Invalid root plan | Root planner submits no plan or an invalid plan | `failed` | `PlanExpander` marks the planner task failed with `InvalidPlan: ...`; because the task is the root, final run status is failed. |
| Failed recovery path | Replanner task fails or crashes | `failed` if this failure reaches the root | `TaskCenter.fail_task()` fails the original `request_replan` task with `replanner_failed: ...` when the replanner was fired for it. |
| Invalid runtime replan | Runtime `apply_replan(...)` rejects a submitted replan | `failed` if this failure reaches the root | The original `request_replan` task is failed with `replan_apply_failed: ...`; the replanner error then follows normal task failure handling. |
| Orphaned replan request | A task remains stuck in `request_replan` with no live recovery path at finalization | `failed` if this includes or propagates to root | `TeamRun._compute_final_status()` calls `fail_orphaned_replanning()` before reading the root status. |
| Detached-child propagation | Every child of an expanded parent is `failed` or `cancelled`, with no successful child | `failed` if propagation reaches root | The parent is marked `failed` with `all_children_detached`, then participates as a detached child of its own parent. |

## Task-Local Failure

The following conditions fail or replan a task but do not by themselves fail the
team run:

- A worker calls `submit_task_summary(type="fail")`; the executor converts this
  into `TaskCenter.request_replan(...)`.
- An agent exits without calling a terminal submission tool; the runner writes a
  failure summary and the executor treats it as a replan request.
- A non-root planner submits an invalid plan; the planner task fails and parent
  promotion decides whether the failure is absorbed or propagates.
- A non-root worker runner raises a normal exception; the task fails or enters
  replanning through normal executor cleanup.

These failures become run failures only when recovery fails, all useful children
detach, or the root task ultimately becomes `failed`.

## Non-Fatal Conditions

Several errors are intentionally not run-fatal:

- Transient `DispatchQueue.pop_ready(...)` errors other than
  `GraphInvariantViolation` are logged and retried.
- Event-store append failures are logged and ignored so coordination can
  continue.
- Completion note and activity checkpoint failures are logged and ignored.
- Scope warnings are injected when possible; injection failures do not fail the
  task.

## Non-Run Errors

Some validation errors happen before a team run becomes active and should not be
counted as failed team runs:

- Starting a root task without a non-empty `objective`.
- Starting from a team definition whose `entry_planner` is not registered.
- Starting with budgets too small to create the root task.
- Rehydrating an event log that is missing or malformed.

These raise to the caller instead of producing a normal `TeamRunStatus.FAILED`
event.
