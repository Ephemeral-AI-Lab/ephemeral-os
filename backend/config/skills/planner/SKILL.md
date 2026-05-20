---
name: planner
description: Workflow scaffolding for the TaskCenter planner: scope bounding, criterion-per-deliverable, dependency reasoning, and full-vs-deferred coverage decisions.
---

# Planner workflow

You design one attempt's plan. The plan you submit is the contract every
generator and the evaluator reads. Work the plan first; reach the decision
point only after the plan is internally coherent.

## Bound the scope before you decompose

1. Re-read the current iteration goal. That is the scope contract for this
   attempt. The original goal and prior iteration blocks are orientation only;
   do not mine them for backlog items the current iteration did not name.
2. List the deliverables the current iteration goal actually requires. If the
   iteration text names a list, treat each item as a candidate deliverable. If
   it names a single coherent change, treat that as one deliverable.
3. For each candidate deliverable, write the falsifiable statement that would
   make it observable to an outside reader of this attempt's results. That
   statement is your evaluation criterion seed.

If the seed list exceeds what the attempt can credibly land in one DAG, you
have a bounding problem. When the launch exposes a defer terminal, prefer a
smaller coherent slice with a self-contained next-iteration instruction. When
the launch does not expose a defer terminal, narrow the plan contract inside
the current iteration's bounds and make the criteria match what the DAG can
actually deliver.

## One criterion per deliverable

- Each criterion should pin one observable outcome. Two deliverables collapsed
  into one criterion turns partial progress into total failure.
- Prefer measurable wording over aspirational wording.
- The evaluator is binary. Criteria scoped wider than the DAG can deliver cause
  false failures even when every task succeeded.

## Tasks reflect dependencies, not narrative

- Add a dependency edge only when one task's output is required by another. Two
  tasks that touch the same area but produce independent outputs become
  parallel siblings, not a chain.
- A wide flat DAG is normal. Deep chains compound risk because failure of one
  task blocks every descendant.
- Write each task spec so the executor can act without re-reading the plan
  contract. State inputs, outputs, success conditions, and constraints.
  Reference dependency outputs by their dependency id.

## Retry posture

When prior failed attempts appear in the current iteration context, you are
inside a fixed iteration goal. Use prior evidence to rework the failing slice
instead of re-running the same plan unchanged. If the evaluator identified a
specific gap, narrow the next plan to address that gap directly.

## Submission discipline

Plain text you emit during planning is reasoning, not a plan. The plan is only
committed when you call one available terminal step with the required fields.
Before committing, call the advisor with the chosen step and intended payload,
then wait for approval. Write the submitted plan body durably enough that a
fresh agent can act without reconstructing what you were thinking.
