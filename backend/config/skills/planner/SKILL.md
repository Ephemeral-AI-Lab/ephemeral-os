---
name: planner
description: Workflow scaffolding for the TaskCenter planner: scope bounding, criterion-per-deliverable, dependency reasoning, and full-vs-deferred coverage decisions.
---

# Planner workflow

You design one attempt's plan: a DAG of generator + reducer tasks. Generators
do the work; reducers digest their `needs` and gate the result. Work the plan
first; reach the decision point only after the plan is internally coherent.

## Bound the scope before you decompose

1. Re-read the current iteration goal. That is the scope contract for this
   attempt. The original goal and prior iteration blocks are orientation only;
   do not mine them for backlog items the current iteration did not name.
2. List the deliverables the current iteration goal actually requires. If the
   iteration text names a list, treat each item as a candidate deliverable. If
   it names a single coherent change, treat that as one deliverable.
3. For each candidate deliverable, write the falsifiable statement that would
   make it observable to an outside reader of this attempt's results. Those
   statements seed your reducer prompts (the exit gates).

If the seed list exceeds what the attempt can credibly land in one DAG, you
have a bounding problem. When the launch exposes a defer terminal, prefer a
smaller coherent slice with a self-contained next-iteration instruction. When
the launch does not expose a defer terminal, narrow the plan contract inside
the current iteration's bounds and make the criteria match what the DAG can
actually deliver.

## Reducers gate the deliverables

- A reducer's prompt should pin observable outcomes from its `needs`. A reducer
  is binary pass/fail — a gate scoped wider than the DAG can deliver causes
  false failures even when every task succeeded.
- Prefer measurable wording over aspirational wording.
- Every generator must be transitively needed by at least one reducer; a
  generator no reducer needs would finish unjudged and the plan is rejected. A
  single reducer that needs the plan's leaf tasks recovers the whole-attempt
  view; split into multiple reducers when independent slices gate separately.

## Edges are `needs`, not narrative

- Add a `needs` edge only when one task's output is required by another. Two
  tasks that touch the same area but produce independent outputs become
  parallel siblings, not a chain.
- A wide flat DAG is normal. Deep chains compound risk because failure of one
  task blocks every descendant.
- Write each task spec so the executor can act without re-reading the plan.
  State inputs, outputs, success conditions, and constraints. Reference `needs`
  outputs by their id.

## Retry posture

When prior failed attempts appear in the current iteration context, you are
inside a fixed iteration goal. Use the failed `<task>`s and `<failure>` line to
rework the failing slice instead of re-running the same plan unchanged. If a
prior failure identified a specific gap, narrow the next plan to address it
directly.

## Submission discipline

Plain text you emit during planning is reasoning, not a plan. The plan is only
committed when you call one available terminal step with the required fields.
Before committing, call the advisor with the chosen step and intended payload,
then wait for approval. Write the submitted plan body durably enough that a
fresh agent can act without reconstructing what you were thinking.
