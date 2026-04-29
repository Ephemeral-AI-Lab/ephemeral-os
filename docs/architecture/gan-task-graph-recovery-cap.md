# GAN task graph: recovery depth cap and run termination

## Context

This document refines [gan-task-graph-v1.md](./gan-task-graph-v1.md). It addresses the open follow-up at the bottom of v1:

> Replan depth cap: nothing bounds `executor -> planner -> child -> planner -> ...` chains. Track depth on harness graph lineage and reject `launch_plan_handoff` past a configured limit.

It also closes one gap in v1's hard-fail propagation and reframes run termination as a property of the root executor's status.

The design separates two motivations for `launch_plan_handoff`:

- **Decomposition** — "this work is not atomic; plan its phases." Unbounded; this is normal phase-by-phase planning.
- **Recovery** — "this work failed or could not be validated; plan a fix." Bounded; this is where loops hide.

The recovery axis is what gets a depth cap. Decomposition stays free.

## Request reasons

`launch_plan_handoff` takes an explicit reason. Reasons are role-scoped:

| Caller | Reason | Meaning | Counts toward cap |
|---|---|---|---|
| executor | `not_atomic` | Task is decomposable; planner should split it. | no |
| executor | `stuck` | Atomic task that the executor cannot solve directly. | yes |
| evaluator | `validation_failed` | Children produced output that does not meet the parent goal; plan corrective work. | yes |

`evaluator_ok` from earlier drafts is removed. The evaluator's job is to validate. If validation passes, it calls `submit_task_success`. If it cannot accept the result, every reason it has to invoke a planner is corrective and counts as recovery.

The reason is persisted on the harness graph created by the handoff:

```python
TaskCenterHarnessGraph:
    ...
    handoff_reason: Literal["not_atomic", "stuck", "validation_failed"]
```

Storing the reason on the graph (not on a `TaskSummary`) lets the lineage walker classify each step in O(1) without parsing text.

## Skip-graph depth recurrence

Define `recovery_depth(task)` by walking the harness-graph lineage:

```
recovery_depth(root_executor) = 0

recovery_depth(task) =
    walk: task -> task.owning_graph -> graph.root_task -> root_task.owning_graph -> ...
    sum:  +1 for each graph whose handoff_reason in {"stuck", "validation_failed"}
          +0 for each graph whose handoff_reason == "not_atomic"
    stop: when owning_graph is None (reached the root executor)
```

The walker stops at the root executor — the root executor has no owning graph by definition (v1 line 14).

`not_atomic` edges are skipped (they represent decomposition, not retry). `stuck` and `validation_failed` edges increment. Decomposition between two recovery edges does not reset the count.

### Worked example

```
root_executor                                  recovery_depth = 0
   |
   | launch_plan_handoff(reason=not_atomic)
   v
H1.planner -> H1.children -> H1.evaluator      recovery_depth = 0
                                  |
                                  | launch_plan_handoff(reason=validation_failed)
                                  v
H2.planner -> H2.children -> H2.evaluator      recovery_depth = 1
                                  |
                                  | launch_plan_handoff(reason=not_atomic)
                                  v
H3.planner -> H3.children -> H3.evaluator      recovery_depth = 1
                                  |
                                  | launch_plan_handoff(reason=validation_failed)
                                  v
H4.planner -> H4.children -> H4.evaluator      recovery_depth = 2  (cap)
                                  |
                                  | launch_plan_handoff(reason=validation_failed)
                                  X  REJECTED
```

## Cap behavior

```python
TaskCenter.recovery_depth_cap: int = 2  # configurable per run
```

At the moment a caller invokes `launch_plan_handoff(reason)`:

```
+--------------------------+
| caller invokes           |
| launch_plan_handoff      |
+-----------+--------------+
            |
            v
+--------------------------+
| compute recovery_depth   |
| via lineage walk = d     |
+-----------+--------------+
            |
            v
   +-------------------+
   | reason recovery?  |
   +---+------------+--+
   no  |            | yes
       v            v
+-------------+  +------------------+
| ALLOW       |  | d  >=  cap ?     |
| handoff     |  +--+------------+--+
| (decompose) |  no |            | yes
+-------------+     v            v
              +-----------+  +-----------------------+
              | ALLOW     |  | REJECT.               |
              | handoff   |  | Caller must terminate |
              | (recover) |  | with success or fail. |
              +-----------+  +-----------------------+
```

When rejected, the remaining terminals are unchanged:

- `submit_task_success` — allowed; the cap is on retry, not on completion.
- `launch_plan_handoff(reason=not_atomic)` — allowed for executors; decomposition is always free.
- `submit_task_failure` (executor) / `submit_evaluation_failure` (evaluator) — the fallback.

The cap rejection is recorded as a `TaskSummary`:

```python
TaskSummary(kind="recovery_cap_reached", text=..., source_task_id=caller.id)
```

So post-mortems can distinguish "agent chose to fail" from "agent was forced to fail."

## Failure semantics

The blast radius of a failure follows the role:

| Failure | Blast | Mechanism |
|---|---|---|
| executor `submit_task_failure` | horizontal: `needs`-dependents in the same harness graph become FAILED with `dependency_blocked` summaries | unchanged from v1 |
| evaluator `submit_evaluation_failure` | vertical: harness graph closes failed; propagates to graph parent task; if that parent is an executor, soft-fail cascade applies; if that parent is an evaluator, that evaluator gets the choice to absorb, recover, or escalate again | refined below |

### Hard fail to soft fail conversion

v1's `close_harness_graph_failed` (lines 237-242) marks `root_task_id` FAILED but does not cascade `dependency_blocked_descendants` from that task when it is an executor. Without that cascade, an executor that launched an inner graph and is now FAILED leaves its outer-graph `needs`-dependents in PENDING limbo.

Updated procedure:

```
close_harness_graph_failed(graph_id):
    1. Mark graph.planner_task_id FAILED.
    2. Append child_failure summary to graph.root_task_id.
    3. Mark graph.root_task_id FAILED.
       If root_task.role == executor:
           for D in dependency_blocked_descendants(root_task.id):
               D.summaries += TaskSummary(
                   kind="dependency_blocked",
                   text=f"Blocked because dependency {root_task.id} failed.",
                   source_task_id=root_task.id,
               )
               D.status = FAILED
    4. notify_child_terminal_changed(root_task.task_center_harness_graph_id)
```

Step 3 in this version unifies executor terminalization across both paths: direct `submit_task_failure` and inner-graph-driven hard fail funnel through the same dependency-blocked cascade. Refactor target: extract `_terminate_executor_failed(task_id)` that both call sites invoke.

### Hard fail propagation through evaluators

If the parent task in step 4 belongs to another harness graph and that graph's evaluator is now ready (`is_harness_graph_ready_for_evaluation` returns True), the outer evaluator dispatches and chooses its terminal:

- `submit_task_success` — absorbs the failure, presenting a partial result as acceptable.
- `launch_plan_handoff(reason=validation_failed)` — attempts recovery; subject to the cap.
- `submit_evaluation_failure` — propagates the hard fail one level further.

Hard-fail propagation is therefore not automatic across evaluator boundaries. Each level's evaluator gets a decision. The cap is what guarantees the recursion bottoms out.

## Run termination

v1 terminates the run inside `close_harness_graph_failed` step 5 ("If the parent task is the root executor, terminate the run FAILED") and inside the symmetric success closure. This special-cases one of the closure's callers and ignores the other paths that can flip the root executor terminal.

Replace with a single property:

> **The run's terminal status equals the root executor's status, observed when the root executor transitions to DONE or FAILED. This is the only place run-level terminal status is set.**

Drop step 5 from `close_harness_graph_failed` and from `close_harness_graph_success`. Steps 1-4 still mark `graph.root_task_id` terminal; the run terminator observes that transition like any other.

This unifies four termination paths into one rule:

1. Root executor calls `submit_task_success` directly (simple-task DONE).
2. Root executor calls `submit_task_failure` directly (simple-task FAILED).
3. An inner graph closes successfully, propagating up to root executor DONE.
4. An inner graph hard-fails, propagating up to root executor FAILED.

All four are "root executor reached terminal." The run terminator subscribes to `task_status_changed` for `task_id == root_executor.id` and emits the matching run-terminal event.

## Properties

The combined design gives:

1. **Decomposition is free.** Any chain of `not_atomic` handoffs is allowed.
2. **Recovery is bounded.** Any single lineage chain accumulates at most `cap` recovery edges before further `launch_plan_handoff(reason in {stuck, validation_failed})` is rejected.
3. **Soft and hard failure have orthogonal blast radii.** Executor failure travels horizontally via `needs`; evaluator failure travels vertically via graph lineage.
4. **Hard fail collapses to soft fail at executor boundaries.** When a hard fail's propagation reaches an executor parent, that executor is treated identically to one that called `submit_task_failure` directly.
5. **Run termination is observable from one place.** The root executor's status is the run's status.
6. **Termination is provably finite.** With finite planner fanout F and decomposition depth D (per phase, finite by construction), the total task count is bounded by `O(F^D * R^cap)` where R is per-node retry options. The cap is what makes the recovery factor finite.

## Critical files and changes

In addition to v1's change list:

### 1. `launch_plan_handoff` signature

Add `reason` parameter. Role-scoped validation:

- executor → `reason in {"not_atomic", "stuck"}`
- evaluator → `reason in {"validation_failed"}`

Reject other combinations as a tool error.

### 2. `TaskCenterHarnessGraph` schema

Add `handoff_reason: Literal["not_atomic", "stuck", "validation_failed"]`. Set at graph creation time inside `launch_plan_handoff`.

### 3. New helper: `recovery_depth(task_id) -> int`

Walks the lineage chain via `task.task_center_harness_graph_id -> graph.root_task_id -> ...`, sums non-`not_atomic` edges, stops at root executor.

### 4. `launch_plan_handoff` cap check

Before creating the new harness graph: if `reason != "not_atomic"` and `recovery_depth(caller) >= recovery_depth_cap`, reject the call. Append `TaskSummary(kind="recovery_cap_reached", ...)` to the caller for telemetry.

### 5. Unify executor terminalization

Extract `_terminate_executor_failed(task_id)` from `submit_task_failure`. Call it from both `submit_task_failure` and from `close_harness_graph_failed` step 3 when `root_task.role == executor`.

### 6. Move run termination out of graph closure

Drop `close_harness_graph_*` step 5 (run termination). Add a single observer hook on `task_status_changed` for the root executor that emits the run-terminal event.

### 7. New `TaskSummary` kind

Add `"recovery_cap_reached"` to the `TaskSummary.kind` literal.

### 8. Configuration

Expose `recovery_depth_cap` as a per-run config. Default `2`.

## Verification

Augment v1's verification suite with:

12. **Recovery cap rejects at threshold.** Build a chain of `validation_failed` handoffs; assert the cap+1th call is rejected and the caller's terminals shrink to {success, failure}.
13. **Decomposition does not increment.** Interleave `not_atomic` and `validation_failed` edges; assert recovery depth equals only the count of `validation_failed` edges.
14. **Cap reset across siblings.** Two parallel children each retry up to cap independently.
15. **Hard-fail cascade through executor parent.** Inner graph hard-fails into an executor parent; assert that parent's outer-graph `needs`-dependents become FAILED with `dependency_blocked` summaries (the v1 gap).
16. **Run termination unified.** For each of the four termination paths (simple success, simple failure, propagated success, propagated failure), assert run terminal status is set exactly once and equals the root executor's status.
17. **Telemetry on cap rejection.** Assert `TaskSummary(kind="recovery_cap_reached", ...)` is appended to the caller on rejection.

## Implementation order

After v1 steps 1-12:

13. Add `handoff_reason` to `TaskCenterHarnessGraph`; thread the parameter through `launch_plan_handoff`.
14. Implement `recovery_depth` helper and lineage walker.
15. Add cap check in `launch_plan_handoff`; add `recovery_cap_reached` summary kind.
16. Extract `_terminate_executor_failed` and call it from both failure paths.
17. Remove step 5 from `close_harness_graph_*`; add the root-executor status observer for run termination.
18. Wire `recovery_depth_cap` configuration.
19. Verification tests 12-17.

## Known follow-ups

- **Per-reason caps.** `stuck` and `validation_failed` may deserve different budgets in practice. Start with one cap; split if telemetry shows distinct failure modes.
- **Same-failure detection.** Cap=2 still allows two attempts at the same unrecoverable bug. Augment with content-similarity check on `task_detail` against ancestor recovery handoffs.
- **Width caps.** Recovery cap bounds depth, not breadth. A planner emitting 50 children with `not_atomic` handoffs each is unbounded by this design. Per-graph fanout cap is a separate follow-up.
- **Server-side reason validation.** Today the caller picks the reason. For evaluator `validation_failed` we could require evidence that at least one child summary kind is `failure` or `dependency_blocked`; otherwise the handoff is more likely scope-extension than corrective work.
