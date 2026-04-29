# GAN executor contract

Explicit, executor-only view of the GAN task graph. Derived from
[gan-task-graph-v1.md](./gan-task-graph-v1.md) and
[gan-task-graph-recovery-cap.md](./gan-task-graph-recovery-cap.md). When this
doc and v1/cap conflict, the source docs win.

## What an executor is

An executor is the role that does direct work. It is the only role allowed to
call `DIRECT_WORK_TOOLS`. Two kinds:

- **Root executor.** Created from the user's request. `task_center_harness_graph_id is None`. Only role with no owning harness graph. Its terminal status is the run's terminal status.
- **Child executor.** Created by a planner inside a harness graph `H`. `task_center_harness_graph_id == H.id`. Its parent is `H.root_task_id`; its peers are `H.executor_task_ids` and `H.evaluator_task_id`.

An executor never knows about a parent task pointer. It only knows:

1. Its own `input`.
2. Its `needs` (direct data dependencies) and their summaries once DONE.

`parent_id` and `closes_for` do not exist on tasks. Lineage lives on the
harness graph.

## Inputs the executor sees

| Field | Source | Notes |
|---|---|---|
| `input` | Root: user's request. Child: `task_inputs[self.id]` set by the enclosing planner via `submit_plan_handoff`. | Always a string. |
| Dependency summaries | `completed_dependencies(self.id)` — direct `needs` tasks that are DONE plus their `summaries`. | Only DONE deps. FAILED deps don't reach a running executor; the executor itself is FAILED with `dependency_blocked` before it dispatches. |

The executor does **not** see: parent goal, planner handoff text, sibling
summaries, evaluator state, harness graph topology. Those are evaluator/planner
context.

## Tools the executor may call

`DIRECT_WORK_TOOLS` (work tools, unchanged from prior modes) plus exactly three
terminal tools:

| Terminal | When to use | Effect |
|---|---|---|
| `submit_task_success(summary)` | The task is done and the result is acceptable. | Append `success` summary; status DONE; notify owning graph. |
| `submit_task_failure(summary)` | Soft fail. This atomic task cannot be completed. | Append `failure` summary; status FAILED; cascade to dependency-blocked descendants; notify owning graph. |
| `launch_plan_handoff(task_detail, reason)` | Hand off to a planner. Reason picks the semantics. | Append `handoff` summary; status HANDOFF; create a new harness graph rooted at this executor. |

`submit_evaluation_failure` is **not** an executor tool. Calling it is a role
violation.

### `launch_plan_handoff` reasons (executor side)

The `reason` parameter is mandatory and role-scoped. An executor may pass:

| Reason | Meaning | Counts toward recovery cap |
|---|---|---|
| `not_atomic` | "This work is decomposable; planner should split it into phases." Normal phase-by-phase planning. | No |
| `stuck` | "Atomic task I cannot solve directly; planner should plan a fix." | Yes |

`validation_failed` is evaluator-only; an executor passing it is a tool error.

Pick `not_atomic` by default for "this is bigger than one tool sequence." Pick
`stuck` only after the executor has actually attempted the work and concluded
it cannot finish atomically — `stuck` is a recovery edge and is bounded.

## Terminal decision tree

```
executor running
   |
   v
+-----------------------------+
| Did the work succeed?       |
+--+--------------------+-----+
yes|                    | no
   v                    v
submit_task_success   +-----------------------------+
                      | Is this work decomposable   |
                      | (multi-phase, not atomic)?  |
                      +--+--------------------+-----+
                      yes|                    | no
                         v                    v
              launch_plan_handoff(   +----------------------+
                reason=not_atomic)   | Can a planner help   |
                                     | recover this atomic  |
                                     | failure?             |
                                     +--+----------------+--+
                                     yes|                | no
                                        v                v
                              launch_plan_handoff(    submit_task_failure
                                reason=stuck)            (soft fail)
                                  [cap-checked]
```

## Soft fail semantics

`submit_task_failure` is the soft-fail terminal. It is **scoped**: it does not
close the harness graph. It marks this executor and only its dependency-blocked
descendants as FAILED. Siblings without a `needs` path through this task keep
running.

```
TaskCenter.submit_task_failure(X):
    X.summaries += TaskSummary(kind="failure", source_task_id=X.id)
    X.status = FAILED
    for D in dependency_blocked_descendants(X.id):
        D.summaries += TaskSummary(kind="dependency_blocked", source_task_id=X.id)
        D.status = FAILED
    notify_child_terminal_changed(X.task_center_harness_graph_id)
```

Once every executor child is terminal (DONE or FAILED), the evaluator is
dispatched. The evaluator decides whether to absorb (`submit_task_success`),
recover (`launch_plan_handoff(reason=validation_failed)`, cap-checked), or hard
fail (`submit_evaluation_failure`).

## When an executor receives a hard-fail cascade

If this executor previously called `launch_plan_handoff` and the inner harness
graph hard-fails (its evaluator submitted `submit_evaluation_failure`), the
graph closure procedure marks this executor FAILED and runs the same
dependency-blocked cascade as a direct soft fail. Concretely:

```
close_harness_graph_failed(graph_id):
    1. graph.planner_task_id -> FAILED
    2. graph.root_task_id.summaries += child_failure
    3. graph.root_task_id.status = FAILED
       If root_task.role == executor:
           run the same dependency-blocked cascade as submit_task_failure
    4. notify_child_terminal_changed(root_task.task_center_harness_graph_id)
```

From an outer evaluator's perspective there is no observable difference between
"executor called `submit_task_failure`" and "executor's inner graph hard-failed
into it." Both produce a FAILED executor task with `failure` or `child_failure`
summaries.

The shared body is `_terminate_executor_failed(task_id)` and is invoked by
both call sites.

## Recovery cap (executor side)

The cap bounds recovery, not decomposition. At every
`launch_plan_handoff(reason)` call the runtime computes:

```
recovery_depth(self) =
    walk: self -> self.owning_graph -> graph.root_task -> root_task.owning_graph -> ...
    +1 for each graph whose handoff_reason in {"stuck", "validation_failed"}
    +0 for each graph whose handoff_reason == "not_atomic"
    stop at the root executor (no owning graph)
```

Then:

- `reason == "not_atomic"` — always allowed.
- `reason == "stuck"` — allowed iff `recovery_depth(self) < recovery_depth_cap`.
- Cap default = `2`.

On rejection: the call returns a tool error and a `TaskSummary(kind="recovery_cap_reached", source_task_id=self.id)` is appended to this executor. Remaining terminals collapse to:

- `submit_task_success` (still always available)
- `launch_plan_handoff(reason=not_atomic)` (decomposition is never capped)
- `submit_task_failure` (the fallback)

Two retries on the same lineage chain is the budget. After that, this executor
must either succeed, decompose, or soft-fail — it cannot ask a planner to fix
the same problem a third time.

## Run termination (root executor only)

The root executor's terminal is the run's terminal. Specifically:

> The run's terminal status equals the root executor's status, observed when
> the root executor transitions to DONE or FAILED. This is the only place
> run-level terminal status is set.

There are exactly four ways a root executor reaches terminal:

1. Direct `submit_task_success` (simple task DONE).
2. Direct `submit_task_failure` (simple task FAILED).
3. An inner harness graph closes successfully → root executor DONE via `close_harness_graph_success` step 3.
4. An inner harness graph hard-fails → root executor FAILED via `close_harness_graph_failed` step 3 (with the dependency-blocked cascade noted above; root executor has no `needs`-dependents but the same code path still runs).

A single observer on `task_status_changed` for `task_id == root_executor.id`
emits the run-terminal event. Graph closure does not terminate the run.

## What the executor must not do

- Call `submit_evaluation_failure` (evaluator-only).
- Call `launch_plan_handoff(reason="validation_failed")` (evaluator-only reason).
- Call `launch_plan_handoff` without a `reason` (rejected).
- Call `launch_plan_handoff(reason="stuck")` when `recovery_depth(self) >= cap` (rejected; pick a different terminal).
- Look at `parent_id`, `closes_for`, sibling status, or evaluator state. None exist or are visible at this role.
- Treat `needs` dependencies as parent tasks. They are data dependencies whose summaries are inputs.

## Summaries an executor produces or receives

| Kind | Direction | When |
|---|---|---|
| `success` | produced | `submit_task_success` |
| `failure` | produced | `submit_task_failure` |
| `handoff` | produced | `launch_plan_handoff` (the executor-side handoff summary) |
| `child_success` | received on `self` | inner graph closes successful |
| `child_failure` | received on `self` | inner graph hard-fails |
| `dependency_blocked` | received on `self` | a dependency in `needs` failed; this executor is auto-FAILED before dispatch |
| `recovery_cap_reached` | received on `self` | a `stuck` handoff was rejected by the cap |

The executor itself never appends `child_success`, `child_failure`,
`evaluation_failure`, or `dependency_blocked` — those are written by graph
closure or by the failure cascade.

## Quick reference card

```
role:                executor
owning graph:        None (root) | H (child)
sees:                self.input, completed needs summaries
tools:               DIRECT_WORK_TOOLS + 3 terminals
terminals:           submit_task_success
                     submit_task_failure              (soft, scoped)
                     launch_plan_handoff(reason=…)
reasons allowed:     not_atomic   (free)
                     stuck        (cap-bounded, default cap=2)
forbidden:           submit_evaluation_failure
                     launch_plan_handoff(reason=validation_failed)
soft fail blast:     self + dependency_blocked_descendants
hard fail received:  inner graph hard-fail collapses to soft fail at this node
run termination:     root executor's terminal = run's terminal
```
