# EOS Agent Core Rust to TypeScript Migration - Phase 05.2 Workflow Outcome Context Rendering

Status: Implemented
Date: 2026-06-11
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Base spec: `phase-05.1-workflow-context-redesign_SPEC.md`
Depends on: Phase 05 + Phase 05.1 (`@eos/workflow` DB-authoritative tree,
per-field context projection, disk mirror, workflow-context scripts)

## 1. Intent

Phase 05.1 makes the workflow context a one-fact-one-path file universe, but it
still exposes the planning act as a rendered `plan_<id>/summary.md` subtree and
computes terminal outcomes from a mix of plan summary, work-item summary,
work-item outcome, and fail reason.

Phase 05.2 tightens that surface:

- `Plan` remains a durable DB/launch/binding entity, but it is no longer a
  context-rendered entity.
- Planner summary moves to the owning attempt as `plan_summary.md`.
- Attempt outcome becomes a first-class derived file at the attempt root.
- Iteration outcome becomes the closing attempt outcome.
- Workflow outcome becomes the ordered set of iteration outcomes.
- The spec names logical file/folder first-appearance times so tests can assert
  stable lifecycle behavior without relying on filesystem birthtime.

This spec amends rendering, context-script DTO path fields, and tests only.
It does not change planner/work-item launch ordering, plan rows, launch queue
rows, submission validation, retry budget rules, or refocus semantics from
Phase 05.1.

## 2. Design Decisions

1. **Plans are execution state, not context folders.** The `plans` table, plan
   status, planner `agent_run_id`, declared focus/deferred fields, and bound
   planner submission remain unchanged. The context path universe stops creating
   `plan_<id>/` directories, `plan_<id>/summary.md`, plan listing rows, and
   plan search hits.
2. **Planner summary belongs to the attempt story.** An accepted planner
   submission renders as `attempt_<id>/plan_summary.md`. It is the same
   `planner_summary` text from the plan row, but the path is owned by the
   attempt because the planner's summary describes the attempt's planned work.
3. **Attempt outcome is derived from work-item summaries.** A terminal
   successful or failed attempt renders `attempt_<id>/outcome.md` from every
   work item in planner order. The file summarizes work-item statuses and
   summaries only; individual `work_item_<id>/outcome.md` files stay separate
   facts and are not embedded into the attempt outcome.
4. **Iteration outcome is the closing attempt outcome.** An iteration renders
   `iteration_<id>/outcome.md` when it closes `Success` or closes `Failed`
   because no retry budget remains. The content is the closing attempt's
   outcome. A failed attempt that creates a retry does not create an iteration
   outcome.
5. **Workflow outcome is the workflow's iteration ledger.** A successful or
   failed workflow renders root `outcome.md` as all closed iteration outcomes in
   sequence order. The workflow outcome no longer means "last iteration only"
   on success or "latest fail reason only" on failure.
6. **Cancellation is status, not business outcome.** Cancelled attempts and
   cancelled iterations do not get business `outcome.md` files unless they had
   already reached `Success` or `Failed` before the cancel. A cancelled workflow
   may render root `outcome.md` as a cancellation marker plus any already closed
   iteration outcomes; tests must not treat that as a business outcome.
7. **Context snapshots keep plan metadata, but no plan path.** The
   `WorkflowContextAttempt.plan` object remains in `workflow_context` for
   scripts because it carries status, declarations, summary, and `agent_run_id`.
   Its `context_path` field is removed. Scripts that need the rendered planner
   summary path derive it as `${attempt.context_path}/plan_summary.md`.
8. **Attempt listing summary uses planner summary.** Since there is no plan row
   in context listings, the attempt directory row uses the first line of
   `plan_summary.md` where present. Work-item directory rows keep using the
   first line of the work-item summary.

## 3. Amendments to Phase 05.1

| Phase 05.1 Surface | Phase 05.2 Replacement |
| --- | --- |
| `plan_<id>/summary.md` under each attempt | `attempt_<id>/plan_summary.md` |
| `ContextEntityKind` includes `plan` | `plan` removed from rendered context entity kinds |
| `WorkflowContextPlan.context_path` | removed from the context-script DTO |
| Iteration outcome = closing attempt plan summary + work-item summaries/outcomes + fail reason | iteration outcome = closing attempt outcome |
| Workflow success outcome = last iteration outcome | workflow outcome = all iteration outcomes |
| Workflow failure outcome = latest fail reason | workflow outcome = all closed iteration outcomes plus the failed iteration outcome |
| No attempt-level `outcome.md` | terminal successful/failed attempts render `outcome.md` |

## 4. Context Path Universe

```text
workflow_<id>/
  goal.md
  outcome.md                               workflow Success/Failed; Cancelled marker only

  iteration_<id>/
    focus.md                               latest declaration; absent before first declaration
    deferred_goal.md                       latest declaration's deferral; absent if none
    outcome.md                             Success or final Failed only

    attempt_<id>/                          is_consistent_with_iteration_focus only
      plan_summary.md                      accepted planner summary; absent on planner death
      fail_reason.md                       failed attempts only
      outcome.md                           successful or failed attempts only
      work_item_<id>/
        description.md                     accepted planner work-item description
        spec.md                            accepted planner work-item spec
        summary.md                         worker submitted summary
        outcome.md                         worker submitted outcome

    archived/
      attempt_<id>/                        drifted attempt, relocated whole
        focus.md                           only if this attempt declared superseded focus
        deferred_goal.md                   only if that declaration carried one
        plan_summary.md                    same attempt-owned file as live shape
        fail_reason.md
        outcome.md
        work_item_<id>/
          description.md
          spec.md
          summary.md
          outcome.md
```

Rules:

- No rendered path contains `/plan_`.
- Plan summary is a field file on the attempt, not a child entity.
- Refocus path relocation is unchanged: an attempt superseded by a later focus
  declaration moves from `iteration_<id>/attempt_<id>/` to
  `iteration_<id>/archived/attempt_<id>/`.
- Archived attempts preserve the same attempt-owned files as live attempts.
  The archived declaring attempt additionally carries the superseded
  declaration files (`focus.md`, optional `deferred_goal.md`).
- Field files stay verbatim. Status stays in `ContextPage` and listing rows.
- Search keeps the Phase 05.1 rule: `archived/` subtrees are excluded unless
  the query scope explicitly names an archived path.

## 5. Derived Outcome Content

Outcome files are render-time projections over `WorkflowTree`; no new outcome
columns are stored.

### 5.1 Attempt Outcome

Created for attempt status `Success` or `Failed`.

Input rows:

- all work items in planner order,
- each work item's status,
- each work item's `worker_summary`, when present.

Suggested deterministic render:

```text
# Attempt outcome
- work_item_<id> [Success]: <worker_summary>
- work_item_<id> [Failed]: <worker_summary>
- work_item_<id> [Cancelled]: (no summary)
```

Rules:

- If a failed planner produced no work items, render:

  ```text
  # Attempt outcome
  (no work items)
  ```

- A failed attempt still has `fail_reason.md`; the fail reason is not embedded
  in `outcome.md`.
- Work-item `outcome.md` content is not embedded. Consumers that need detailed
  worker output read the work-item path directly.

### 5.2 Iteration Outcome

Created for iteration status `Success` or `Failed`.

Input rows:

- the closing attempt, defined as the last attempt in sequence order,
- that attempt's derived attempt outcome.

Render rule:

```text
<closing attempt outcome content>
```

Rules:

- A failed attempt with retry budget left does not close the iteration and does
  not create `iteration_<id>/outcome.md`.
- If the closing attempt is failed because the retry budget is exhausted, the
  failed attempt outcome becomes the iteration outcome.
- Cancelled iterations do not produce a business outcome file.

### 5.3 Workflow Outcome

Created for workflow status `Success` or `Failed`; for `Cancelled`, see the
cancellation rule below.

Input rows:

- every iteration that has an `outcome.md`, ordered by iteration sequence.

Suggested deterministic render:

```text
# Workflow outcome

## iteration_<id> [Success]
<iteration outcome content>

## iteration_<id> [Failed]
<iteration outcome content>
```

Rules:

- A successful multi-iteration workflow includes every successful iteration
  outcome, not only the final iteration.
- A failed workflow includes successful predecessor iteration outcomes and the
  final failed iteration outcome.
- A cancelled workflow may render:

  ```text
  # Workflow outcome
  workflow cancelled
  ```

  followed by any already closed iteration outcomes. This is a cancellation
  marker, not a business outcome.

## 6. Logical Creation Schedule

These times are logical projection points after a committed mutation and mirror
reprojection. They are the stable test contract. Do not assert filesystem
birthtime or OS `ctime`; the mirror writes with temp files and replacement.

| Time | Event | Stable Assertion Meaning |
| --- | --- | --- |
| T0 | `delegate_workflow` commits | Workflow root, first iteration, and first attempt directories exist. No work items or summary files exist. |
| T1 | Planner submits a valid payload | Focus/deferred files update; `plan_summary.md` appears; work-item directories and static files appear. |
| T1F | Planner dies or context composition fails before valid payload | Attempt fails with `fail_reason.md`; `plan_summary.md` is absent; attempt `outcome.md` appears with `(no work items)`; retry attempt appears if budget remains. |
| T2 | Worker submits one work item result | That work item gains `summary.md` and `outcome.md`; attempt stays running unless all completion rules are satisfied. |
| T3 | All work items in an attempt succeed | Attempt becomes `Success`; attempt `outcome.md` appears; iteration closes or promotes according to deferred goal. |
| T4 | A work item fails | That work item gains `summary.md` and `outcome.md`; non-terminal siblings are cancelled; attempt becomes `Failed`; `fail_reason.md` and attempt `outcome.md` appear. |
| T5 | Failed attempt has retry budget left | New retry attempt directory appears; iteration remains running; no iteration outcome yet. |
| T6 | Failed attempt exhausts retry budget | Iteration becomes `Failed`; iteration `outcome.md` appears; workflow becomes `Failed`; workflow `outcome.md` appears. |
| T7 | Successful iteration has deferred goal | Current iteration `outcome.md` appears; next iteration and its first attempt directory appear; workflow remains running. |
| T8 | Successful iteration has no deferred goal | Iteration `outcome.md` appears; workflow becomes `Success`; workflow `outcome.md` appears. |
| T9 | Retry planner refocuses | Superseded attempts relocate under `iteration_<id>/archived/attempt_<id>/`; old live attempt paths disappear. |
| T10 | Workflow is cancelled | Non-terminal entities become `Cancelled`; business outcome files are not created for cancelled attempts/iterations; workflow cancellation marker may appear. |

## 7. Directory First-Appearance Table

| Directory | First Appears | Moves / Disappears | Notes for Tests |
| --- | --- | --- | --- |
| `workflow_<id>/` | T0 | Never while workflow exists | Root mirror directory. |
| `iteration_<id>/` | T0 for initial iteration; T7 for promoted deferred iteration | Never archived | Prior iterations may collapse in listings but remain readable. |
| `iteration_<id>/attempt_<id>/` | T0 for the initial iteration's first attempt; T7 for a promoted iteration's first attempt; T5 for retry attempts | Disappears from live path at T9 if superseded | Live path exists iff `is_consistent_with_iteration_focus=true`. |
| `iteration_<id>/archived/` | T9 | Removed only if no archived attempts remain after a full reprojection | Created by refocus, not by iteration close. |
| `iteration_<id>/archived/attempt_<id>/` | T9 | Never while archived attempt exists | Same attempt entity, relocated. |
| `.../work_item_<id>/` | T1 | Moves with parent attempt at T9 | Exists for every accepted planner work item. |
| `.../plan_<id>/` | Never | N/A | Must not exist in memory renderer or disk mirror. |

## 8. File First-Appearance Table

| File | First Appears | Removed / Moved | Content Source |
| --- | --- | --- | --- |
| `goal.md` | T0 | Never | Workflow `goal`. |
| root `outcome.md` | T6 for workflow `Failed`; T8 for workflow `Success`; T10 for `Cancelled` marker | Created once workflow terminal | Ordered iteration outcomes; cancellation marker for cancelled workflows. |
| `iteration_<id>/focus.md` | T1 | Replaced at T9 on refocus | Latest declared `iteration_focus`. |
| `iteration_<id>/deferred_goal.md` | T1 when declaration has deferral | Removed at T9 if latest declaration omits deferral | Latest declared `deferred_goal`. |
| `iteration_<id>/outcome.md` | T6 for final failed iteration; T7/T8 for successful iteration | Never after creation | Closing attempt outcome. |
| `attempt_<id>/plan_summary.md` | T1 | Moves with parent attempt at T9 | Planner payload `summary`; absent for planner death before valid submission. |
| `attempt_<id>/fail_reason.md` | T1F or T4 | Moves with parent attempt at T9 | Attempt failure reason from plan failure, work-item failure, death synthesis, or compose failure. |
| `attempt_<id>/outcome.md` | T1F, T3, or T4 | Moves with parent attempt at T9 | All work-item summaries in planner order; `(no work items)` for planner failure before materialization. |
| `work_item_<id>/description.md` | T1 | Moves with parent attempt at T9 | Planner work-item `description`. |
| `work_item_<id>/spec.md` | T1 | Moves with parent attempt at T9 | Planner work-item `work_item_spec`. |
| `work_item_<id>/summary.md` | T2 on worker submission; T4 on failing submission or death/compose synthesis | Moves with parent attempt at T9 | Worker payload `summary`, or the synthesized failure reason for a worker that settles without submitting; absent for cancelled siblings with no submission. |
| `work_item_<id>/outcome.md` | T2 on worker submission; T4 on failing submission or death/compose synthesis | Moves with parent attempt at T9 | Worker payload `outcome`, or the synthesized failure reason for a worker that settles without submitting; absent for cancelled siblings with no submission. |
| `archived/attempt_<id>/focus.md` | T9 | Never after archive creation | Superseded focus, only on the attempt whose plan declared it. |
| `archived/attempt_<id>/deferred_goal.md` | T9 if superseded declaration had one | Never after archive creation | Superseded deferral, only on the declaring attempt. |

## 9. Implementation Boundary

Expected `@eos/workflow` changes:

- `archive/paths.ts`: stop importing/rendering `planFieldFiles`; stop creating
  plan directories; set attempt listing summary from the first line of
  `attempt.plan.summary`.
- `attempt/context.ts`: render `plan_summary.md`, `fail_reason.md`,
  `outcome.md`, and archived declaration files.
- `iteration/context.ts`: render `outcome.md` as closing attempt outcome.
- `workflow/context.ts`: render root `outcome.md` as all iteration outcomes.
- `plan/context.ts`: delete or leave unused until removed by cleanup.
- `ContextEntityKind`: remove `"plan"`.
- `WorkflowContextPlanSchema`: remove `context_path`; keep other plan metadata.
- Context script fixture helpers: derive planner summary path from attempt path
  if a script needs the rendered file path.

Out of scope:

- DB schema changes.
- Launch queue changes.
- Submission payload shape changes.
- Retry, refocus, cancellation, or sibling-cancel transition changes.
- Tool exposure changes.

## 10. Verification Plan

Add or update focused tests before broad checks:

| Case | Assertions |
| --- | --- |
| Plan flattening | No `plan_<id>/` directory exists in `buildWorkflowContext`; planner summary renders at `attempt_<id>/plan_summary.md`; listing rows contain no `kind: "plan"` owner. |
| Attempt outcome success | Before all work items finish, no attempt `outcome.md`; after all succeed, attempt `outcome.md` contains every work-item summary in planner order. |
| Attempt outcome failure with retry | Failed attempt gets `fail_reason.md` and `outcome.md`; retry attempt appears; iteration `outcome.md` and root `outcome.md` are absent. |
| Attempt outcome final failure | Exhausting retry budget creates attempt, iteration, and workflow `outcome.md`; workflow outcome includes the failed iteration outcome. |
| Multi-iteration success | Each successful iteration has `outcome.md`; root `outcome.md` includes all iteration outcomes in sequence order. |
| Refocus archive | Refocus prunes old live attempt paths; archived attempt keeps `plan_summary.md`, `outcome.md`, work-item files, and declaration files only on the declaring attempt. |
| Mirror equality | Disk mirror file list exactly equals the in-memory context file list; no `plan_<id>/` paths exist on disk. |
| Snapshot DTO | `WorkflowContextAttempt.plan` still carries status/declarations/summary/agent_run_id and no longer carries `context_path`. |

Commands:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm exec vitest run packages/contracts/tests/workflow.test.ts packages/workflow/tests/context.test.ts packages/workflow/tests/mirror.test.ts packages/agent-runtime/tests/workflow-runtime.test.ts
pnpm run typecheck
pnpm run lint
pnpm run test
```

Docs hygiene:

```bash
git diff --check -- docs/plans/agent-core-rust-to-typescript-migration eos-agent-core
```

## 11. Acceptance Criteria

Phase 05.2 is accepted when:

- the context tree contains no `plan_<id>/` directory or plan-owned file,
- planner summaries render as attempt-owned `plan_summary.md`,
- attempt outcomes render from work-item summaries,
- iteration outcomes equal closing attempt outcomes,
- workflow outcomes include all iteration outcomes in order,
- logical first-appearance behavior matches the tables in sections 6-8,
- refocus relocation preserves the flattened attempt-owned files under
  `archived/attempt_<id>/`,
- workflow context scripts still receive plan metadata needed for policy but no
  plan context path,
- focused tests plus `pnpm run test` pass.
