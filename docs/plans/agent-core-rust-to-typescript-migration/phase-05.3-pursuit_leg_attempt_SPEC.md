# EOS Agent Core Rust to TypeScript Migration - Phase 05.3 Pursuit / Leg / Attempt Vocabulary

Status: Proposed
Date: 2026-06-12
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Base specs:
- `phase-05-workflow-orchestration_SPEC.md`
- `phase-05.1-workflow-context-redesign_SPEC.md`
- `phase-05.2-workflow-outcome-context-rendering_SPEC.md`
Input notes:
- `note/workflow-vocabulary-judge-report.md`
- `note/leg-goal-without-focus-debate.md`
- `note/planner-worker-initial-messages.md`

## 1. Intent

Phase 05.1/05.2 implemented the durable planner/worker orchestration spine, but
its vocabulary still exposes the losing `workflow / iteration / deferred_goal`
model. Phase 05.3 replaces that vocabulary with the judged
`pursuit / leg / attempt` model and removes the separate `focus` concept.

The end-state behavior is:

- a delegated root objective is a `pursuit`,
- a vertical continuation unit is a `leg`,
- retries remain `attempt`s inside a leg,
- each leg has an effective `leg_goal` from creation time,
- `next_leg_goal` is the optional successor gate,
- a planner may omit `leg_goal` to accept the current leg goal,
- a planner submits `leg_goal` only to refocus the current leg,
- superseded attempts move under `superseded/`, not `archived/`.

This is a vocabulary and contract phase. It keeps the Phase 05 launch,
submission, retry, cancellation, mirror, and outcome mechanics unless this spec
explicitly replaces a name or validation rule.

## 2. Non-Negotiable Boundary

Initial-message scripting moves under the pursuit script root:

```text
/Users/yifanxu/machine_learning/LoVC/EphemeralOS/.eos-agents/pursuit/scripts
```

Repo-relative:

```text
.eos-agents/pursuit/scripts/
  planner.cjs
  worker.cjs
  variable_reference_map.cjs
```

The directory name, script content, input DTOs, variable map, and emitted
messages must all use pursuit vocabulary. Do not retain
`.eos-agents/workflow/scripts/` as an active profile or runtime script root.

## 3. Vocabulary Decisions

| Old surface | New surface | Notes |
| --- | --- | --- |
| workflow | pursuit | Product/session/tool/context vocabulary. |
| iteration | leg | Ordered vertical continuation unit. |
| attempt | attempt | Retry unit remains unchanged. |
| `iteration_focus` | removed | No separate focus concept. |
| `focus.md` | removed | Replaced by `leg_goal.md`. |
| `deferred_goal` | `next_leg_goal` | Successor gate. |
| `archived/` | `superseded/` | Attempts displaced by a later `leg_goal`. |
| `workflow_context` | `pursuit_context` | Script input root and DTO naming. |
| `workflow_context_script` | `pursuit_context_script` | Profile field; resolved under `.eos-agents/pursuit/scripts/`. |
| `delegate_workflow` | `delegate_pursuit` | Tool family becomes pursuit-facing. |
| background session type `"workflow"` | `"pursuit"` | Cancellation still rides `cancel_background_session`. |
| `@eos/workflow` / `packages/workflow` | `@eos/pursuit` / `packages/pursuit` | Package-level vocabulary follows product vocabulary. |

Allowed old spelling after Phase 05.3:

- historical spec/note text,
- migration aliases inside a one-time migration script, if a migration script is
  required.

Everything else in the TypeScript product surface should use pursuit terms.

## 4. Public API and Caller Model

`pursuit` is a caller-agnostic orchestration package. The caller may be an
agent, user action, machine scheduler, test harness, or any other non-agent
object that can call the public API.

The public creation surface should be narrow:

```ts
type CreatePursuitInput =
  | {
      pursuit_goal: string;
      leg_goal_mode?: "dynamic";
      leg_goals?: undefined;
    }
  | {
      pursuit_goal: string;
      leg_goal_mode?: "predefined";
      leg_goals: readonly [string, ...string[]];
    };

interface PursuitHandle {
  pursuit_id: string;
  cancel(reason?: string): Promise<void>;
  settle(): Promise<PursuitSettlement>;
}
```

The handle semantics are independent of who called it:

- `cancel` cancels the pursuit and all non-terminal descendants.
- `settle` resolves when the pursuit reaches `Success`, `Failed`, or
  `Cancelled`.
- Background-supervisor registration remains a runtime/tool adapter concern;
  the pursuit package exposes a terminal handle that callers can register.

Package dependency rule:

```text
@eos/pursuit owns orchestration behind injected ports. It may depend on
@eos/contracts and may import the agent-runtime launch port used to start
planner and worker agents. It must not import @eos/db, @eos/tool, runtime
composition, profile loading, supervisor state, or tool registration. If the
launch-port import would create a cycle, extract that port into a narrow public
subpath before wiring.
```

`leg_goal_mode` is derived from the payload shape: omitting `leg_goals` selects
`"dynamic"`; providing non-empty `leg_goals` selects `"predefined"`. An explicit
`leg_goal_mode` may be accepted for diagnostics, but a mismatch between
`leg_goal_mode` and `leg_goals` must be rejected.

## 5. Leg Goal Modes

| Leg goal mode | Creation input | Leg goal source | Planner declaration rule | Next-leg rule |
| --- | --- | --- | --- | --- |
| Dynamic | `create_pursuit({ pursuit_goal })` | First leg inherits `pursuit_goal`; later legs inherit the previous successful leg's `next_leg_goal`. | Planner may omit `leg_goal`, submit `leg_goal` to refocus, and submit successor-only `next_leg_goal`. | A new leg is created only when the successful current leg has an effective `next_leg_goal`. |
| Predefined | `create_pursuit({ pursuit_goal, leg_goals })` | Each leg uses the caller-provided `leg_goals[sequence - 1]`. | Planner must not submit `leg_goal` or `next_leg_goal`; refocus is disallowed. | A new leg is created from the next predefined `leg_goals` entry until the list is exhausted. |

Predefined mode is for callers that already know the ordered leg list. In that
mode, `pursuit_goal` remains the umbrella objective, while `leg_goals` are the
fixed execution checkpoints.

Dynamic mode is the default. It preserves the current Phase 05 behavior where
each successful leg may discover exactly one successor goal.

The `delegate_pursuit` prompt should not present both modes as equally common:

```text
Use dynamic leg goals by default. Provide only pursuit_goal when the planner
should discover or refocus legs during execution.

Use predefined leg goals only when the caller already knows the complete ordered
leg list. Provide pursuit_goal and leg_goals. In this mode planners cannot
submit leg_goal or next_leg_goal.
```

## 6. Leg Goal Model

`leg_goal` is the current effective goal of a leg.

Dynamic creation rule:

```text
first leg:
  leg_goal = pursuit.goal

next leg:
  leg_goal = previous successful leg.next_leg_goal
```

Predefined creation rule:

```text
leg_n:
  leg_goal = pursuit.leg_goals[n - 1]
```

Dynamic planner submission rule:

```text
leg_goal omitted:
  keep the current leg_goal

leg_goal present:
  replace the current leg_goal and mark older live attempts in the leg as
  superseded

next_leg_goal omitted:
  keep any standing next_leg_goal when leg_goal is also omitted

next_leg_goal present:
  if the leg succeeds, create the next leg with that value as its leg_goal

leg_goal present and next_leg_goal omitted:
  refocus the leg and reset the standing next_leg_goal to absent
```

Success invariant:

```text
Success means the full effective leg_goal was achieved.
Success never means "leg_goal minus next_leg_goal".
```

Dynamic planner prompt invariant:

```text
If you cannot achieve the full leg_goal in this leg, submit a narrowed leg_goal
and defer the remainder as next_leg_goal.
```

Dynamic validation stays intentionally loose: `next_leg_goal` is valid without
a sibling `leg_goal`. The planner may complete the full current `leg_goal` while
declaring newly discovered successor scope.

Predefined planner submission rule:

```text
leg_goal present:
  reject; predefined leg goals cannot be refocused

next_leg_goal present:
  reject; predefined leg goals own the next-leg sequence

both omitted:
  accepted; planner moves directly to planning work items for the current
  predefined leg_goal
```

## 7. Effective Declaration Semantics

Plans remain execution state and the planner submission binding point. They are
not rendered as context folders.

Each plan may carry a declaration:

| Plan declaration field | Meaning |
| --- | --- |
| `declared_leg_goal` | Replace the current leg goal from this attempt onward. |
| `declared_next_leg_goal` | Set the successor goal for this leg. |
| declaration absent | Keep current `leg_goal` and standing `next_leg_goal`. |

The declaration view is append-only and ordered by attempt sequence. An attempt
is consistent with the current leg goal iff no later declaration in the same leg
submitted `leg_goal`.

Adaptive effective values:

```text
base_leg_goal(leg_1) = pursuit.goal
base_leg_goal(leg_n) = effective_next_leg_goal(leg_n-1)

effective_leg_goal(leg) =
  latest declared_leg_goal in leg, if present
  otherwise base_leg_goal(leg)

effective_next_leg_goal(leg) =
  latest declaration in leg that touched leg_goal or next_leg_goal:
    - if it declared next_leg_goal, that value
    - if it declared leg_goal but not next_leg_goal, absent
  otherwise absent
```

Predefined effective values:

```text
effective_leg_goal(leg_n) = pursuit.leg_goals[n - 1]
effective_next_leg_goal(leg_n) =
  pursuit.leg_goals[n], if present
  otherwise absent
```

In predefined mode, plan declarations for `declared_leg_goal` and
`declared_next_leg_goal` must remain absent. The next-leg preview is derived
from the caller-provided list, not from planner output.

`leg_goal.md` must include a provenance line:

```text
<effective leg goal>

Provenance: <inherited from pursuit goal | inherited from successful leg_<n> next_leg_goal | declared by attempt_<id> planner | predefined leg_goal[<n>]>
```

## 8. Context Path Universe

Rendered context paths switch from `workflow_<id>/iteration_<id>/...` to
`pursuit_<id>/leg_<id>/...`.

```text
pursuit_<id>/
  goal.md
  outcome.md                               pursuit Success/Failed; Cancelled marker only

  leg_<id>/
    leg_goal.md                            effective leg goal plus provenance; appears at leg creation
    next_leg_goal.md                       effective successor gate; absent if none
    outcome.md                             Success or final Failed only

    attempt_<id>/                          is_consistent_with_leg_goal only
      plan_summary.md                      accepted planner summary; absent on planner death
      fail_reason.md                       failed attempts only
      outcome.md                           successful or failed attempts only
      work_item_<id>/
        title.md                           accepted planner work-item title
        spec.md                            accepted planner work-item spec
        summary.md                         worker submitted summary
        outcome.md                         worker submitted outcome

    superseded/
      attempt_<id>/                        displaced attempt, relocated whole
        leg_goal.md                        only if this attempt declared superseded leg_goal
        next_leg_goal.md                   only if that declaration carried one
        plan_summary.md                    same attempt-owned file as live shape
        fail_reason.md
        outcome.md
        work_item_<id>/
          title.md
          spec.md
          summary.md
          outcome.md
```

Rules:

- No rendered path contains `/plan_`.
- No rendered path contains `workflow_`, `iteration_`, `focus.md`,
  `deferred_goal.md`, or `archived/`.
- Disk mirror context lives under `.eos-agents/pursuit/context/pursuit_<id>/`.
  No active context mirror path should remain under `.eos-agents/workflow/`.
- `leg_goal.md` appears at leg creation, before planner submission.
- `next_leg_goal.md` is absent until a dynamic declaration or later predefined
  leg exists; absence means no successor.
- Superseded attempts preserve the same attempt-owned files as live attempts.
- Declaration files under `superseded/attempt_<id>/` exist only on the attempt
  whose planner declared the displaced value.
- Status stays in `ContextPage` and listing rows, not file content.
- Search excludes `superseded/` by default unless scope explicitly names it.

## 9. Outcome Rendering

Outcome aggregation remains Phase 05.2 behavior with renamed headings:

| Old | New |
| --- | --- |
| Attempt outcome | Attempt outcome |
| Iteration outcome | Leg outcome |
| Workflow outcome | Pursuit outcome |

Attempt outcome:

```text
# Attempt outcome
- work_item_<id> [Success]: <worker_summary>
- work_item_<id> [Failed]: <worker_summary>
- work_item_<id> [Cancelled]: (no summary)
```

Leg outcome:

```text
<closing attempt outcome content>
```

Pursuit outcome:

```text
# Pursuit outcome

## leg_<id> [Success]
<leg outcome content>

## leg_<id> [Failed]
<leg outcome content>
```

Cancelled pursuit marker:

```text
# Pursuit outcome
pursuit cancelled
```

## 10. Planner Payload Contract

`submit_planner_outcome` remains the terminal planner submission tool unless a
later phase renames all terminal tools. Its payload changes vocabulary:

```ts
const PlannerWorkItemSpecSchema = z.object({
  id: z.string().min(1),
  agent_name: z.string().min(1),
  title: z.string().min(1),
  spec: z.string().min(1),
  depends_on: z.array(z.string()).default([]),
});

const PlannerOutcomePayloadSchema = z.object({
  summary: z.string().min(1),
  leg_goal: z.string().min(1).optional(),
  next_leg_goal: z.string().min(1).optional(),
  work_items: z.array(PlannerWorkItemSpecSchema).min(1),
});
```

Dynamic-mode validation changes:

| Case | Result |
| --- | --- |
| first planner omits `leg_goal` | accepted; uses existing leg goal |
| `next_leg_goal` without `leg_goal` | accepted |
| `leg_goal` without `next_leg_goal` | accepted; refocuses and clears standing successor |
| neither `leg_goal` nor `next_leg_goal` | accepted; keeps both current values |
| unknown worker `agent_name` | rejected in-run |
| duplicate/dangling/cyclic work-item ids or `depends_on` ids | rejected in-run |
| old work-item `description` or `needs` fields | rejected in-run |

Predefined-mode validation changes:

| Case | Result |
| --- | --- |
| first planner omits `leg_goal` and `next_leg_goal` | accepted; uses the predefined current leg goal |
| retry planner omits `leg_goal` and `next_leg_goal` | accepted; uses the predefined current leg goal |
| planner submits `leg_goal` | rejected in-run; no attempt budget is consumed for a correctable payload error |
| planner submits `next_leg_goal` | rejected in-run; no attempt budget is consumed for a correctable payload error |
| successful non-final predefined leg | next leg is created from the next `leg_goals` entry |
| successful final predefined leg | pursuit closes `Success` |

The tool result on success should keep returning the summary payload as today.

## 11. Script Input DTOs

Context scripts receive pursuit-named DTOs on stdin. The script directory is
`.eos-agents/pursuit/scripts/`.

```ts
interface PlannerPursuitContextInput {
  kind: "planner";
  pursuit_context: PursuitContextSnapshot;
  current: {
    pursuit_id: string;
    leg_id: string;
    attempt_id: string;
    plan_id: string;
  };
}

interface WorkerPursuitContextInput {
  kind: "worker";
  pursuit_context: PursuitContextSnapshot;
  current: {
    pursuit_id: string;
    leg_id: string;
    attempt_id: string;
    work_item_id: string;
  };
}
```

Snapshot shape:

```ts
interface PursuitContextSnapshot {
  pursuit: {
    id: string;
    goal: string;
    leg_goal_mode: "dynamic" | "predefined";
    predefined_leg_count: number | null;
    status: PursuitEntityRunStatus;
    outcome: string | null;
    context_path: string; // pursuit_<id>
    legs: PursuitContextLeg[];
  };
}

interface PursuitContextLeg {
  id: string;
  sequence: number;
  origin: "initial" | "next_leg_goal" | "predefined";
  status: PursuitEntityRunStatus;
  leg_goal: string;
  leg_goal_provenance: string;
  is_leg_goal_mutatable: boolean;
  next_leg_goal: string | null;
  max_attempts: number;
  outcome: string | null;
  context_path: string;
  attempts: PursuitContextAttempt[];
}

interface PursuitContextAttempt {
  id: string;
  sequence: number;
  status: PursuitEntityRunStatus;
  fail_reason: string | null;
  is_consistent_with_leg_goal: boolean;
  outcome: string | null;
  context_path: string;
  plan: PursuitContextPlan;
  work_items: PursuitContextWorkItem[];
}

interface PursuitContextPlan {
  id: string;
  status: PursuitEntityRunStatus;
  agent_run_id: string | null;
  summary: string | null;
  declared_leg_goal: string | null;
  declared_next_leg_goal: string | null;
}

interface PursuitContextWorkItem {
  id: string;
  agent_name: string;
  status: PursuitEntityRunStatus;
  agent_run_id: string | null;
  title: string;
  spec: string;
  depends_on: string[];
  summary: string | null;
  outcome: string | null;
  context_path: string;
}
```

`PursuitContextPlan` keeps plan metadata but no plan `context_path`, matching
Phase 05.2. `PursuitContextWorkItem.depends_on` contains work-item ids local to
the same attempt. Snapshot `outcome` fields are `null` until their owning
attempt, leg, pursuit, or work item reaches a terminal condition and the
corresponding `outcome.md` content exists.

## 12. Initial Message Scripting

The runtime must resolve profile-selected context scripts under:

```text
.eos-agents/pursuit/scripts/
```

Expected files:

```text
.eos-agents/pursuit/scripts/planner.cjs
.eos-agents/pursuit/scripts/worker.cjs
.eos-agents/pursuit/scripts/variable_reference_map.cjs
```

The existing scripts currently use workflow/iteration variable names. Phase 05.3
must move them to the pursuit script folder and update them to produce
pursuit/leg messages from the new `pursuit_context` DTO.

Script output stays hook-parity JSON:

```json
{
  "initial_messages": [
    {
      "role": "user",
      "content": [{ "type": "text", "text": "<message 1>" }]
    }
  ]
}
```

Planner launch messages:

1. pursuit and current leg context,
2. current leg evidence,
3. planner directive.

Worker launch messages:

1. leg and attempt context,
2. assigned work and dependencies,
3. worker directive.

The exact content contract is the one recorded in
`note/planner-worker-initial-messages.md`.

## 13. Initial Message Directive Invariants

Dynamic-mode planner messages must include:

```text
A new dynamic leg exists only because the previous leg closed successfully and
declared next_leg_goal.
```

All planner messages must include:

```text
Success means the full effective leg_goal is achieved.
```

Dynamic-mode planner messages must also include:

```text
If you cannot achieve the full leg_goal in this leg, submit a narrowed leg_goal
and put the remainder in next_leg_goal.
```

Predefined-mode planner messages must also include:

```text
If the predefined leg_goal is too broad or wrong, do not submit leg_goal or
next_leg_goal. Plan only work that completes the current predefined leg_goal.
```

Planner payload guidance:

```text
Dynamic mode:
- Omit leg_goal when you accept the current leg_goal.
- Include leg_goal only to refocus this leg.
- Refocus supersedes prior live attempts and resets the standing next_leg_goal.
- Include next_leg_goal only for work that should become a future leg after
  this leg succeeds.
- next_leg_goal is a goal to be planned later; it is never a plan and never a
  description of work delivered by this leg.

Predefined mode:
- The caller predefined this leg_goal.
- Omit leg_goal and next_leg_goal.
- Do not refocus this leg.
- Do not declare future legs; the predefined list owns leg progression.
```

Worker messages must include:

```text
Stay inside the current leg_goal and this work item. Do not plan new legs,
change leg_goal, or decide next_leg_goal.
```

## 14. Implementation Boundary

Expected package changes:

| Package | Required work |
| --- | --- |
| `@eos/contracts` | Rename workflow/iteration DTOs and ids to pursuit/leg; remove focus fields; add `leg_goal`, `leg_goal_provenance`, `is_leg_goal_mutatable`, `next_leg_goal`, and nullable snapshot `outcome`; update planner payload schema to use work-item `title` and `depends_on`. |
| `@eos/db` | Rename row types and migration schema to `pursuits` / `legs`; replace `origin: "deferred_goal"` with `"next_leg_goal"` and add `"predefined"` where mode needs it; replace plan declaration columns with `declared_leg_goal` / `declared_next_leg_goal`; provide the persistence adapter for the pursuit store port. |
| `@eos/pursuit` | Rename `packages/workflow` and package name from `@eos/workflow`; expose caller-agnostic create/cancel/settle handles; own the store and launch ports; derive effective leg goal and successor goal for dynamic and predefined modes; render `pursuit_<id>` / `leg_<id>` / `superseded`; preserve plan flattening and outcome behavior. |
| `@eos/tool` | Rename `delegate_workflow` to `delegate_pursuit`; accept `pursuit_goal` and optional `leg_goals`; expose background session type `"pursuit"`; update planner tool prompt and payload content. |
| `@eos/agent-runtime` | Wire pursuit service and context input DTOs; rename runtime config such as `workflowDb` / `workflowContextRoot` to pursuit equivalents; load `.eos-agents/pursuit/scripts`; profile-selected scripts emit pursuit initial messages. |
| `.eos-agents/pursuit/scripts` | Move/rewrite `planner.cjs`, `worker.cjs`, and `variable_reference_map.cjs` to use pursuit/leg names and the Phase 05.3 initial-message contract. |

Target package tree:

```text
packages/pursuit/
  package.json
  src/
    index.ts
    service.ts
    agent-launcher.ts
    pursuit-tree.ts
    pursuit-context.ts
    pursuit/
      context.ts
      state.ts
      transition.ts
    leg/
      context.ts
      state.ts
      transition.ts
    attempt/
      context.ts
      state.ts
      transition.ts
    plan/
      state.ts
      transition.ts
    work-item/
      context.ts
      state.ts
      transition.ts
    context-engine/
      composer.ts
      input.ts
    projection/
      listing.ts
      paths.ts
      resolve.ts
      mirror.ts
  tests/
    context.test.ts
    creation-schedule.test.ts
    guards.test.ts
    lifecycle.test.ts
    mirror.test.ts
    package-boundary.test.ts
    support.ts
```

Expected file-level rename map:

| Current | Target |
| --- | --- |
| `packages/workflow/` | `packages/pursuit/` |
| `workflow/context.ts` | `pursuit/context.ts` |
| `iteration/context.ts` | `leg/context.ts` |
| `iteration/state.ts` | `leg/state.ts` |
| `iteration/transitions.ts` | `leg/transition.ts` |
| `workflow/state.ts` | `pursuit/state.ts` |
| `workflow/transitions.ts` | `pursuit/transition.ts` |
| `attempt/transitions.ts` | `attempt/transition.ts` |
| `plan/transitions.ts` | `plan/transition.ts` |
| `work-item/transitions.ts` | `work-item/transition.ts` |
| `workflow-tree.ts` | `pursuit-tree.ts` |
| `workflow-context.ts` | `pursuit-context.ts` |
| `context-projection.ts` | `projection/mirror.ts` |
| `archive/listing.ts` | `projection/listing.ts` |
| `archive/paths.ts` | `projection/paths.ts` |
| `archive/resolve.ts` | `projection/resolve.ts` |
| `tools/workflow/delegate-workflow.ts` | `tools/pursuit/delegate-pursuit.ts` |

Avoid compatibility shims unless needed for a single migration boundary. If a
shim is unavoidable, delete it in the same phase after callers are moved.

## 15. Logical Creation Schedule

| Time | Event | Stable Assertion Meaning |
| --- | --- | --- |
| T0 | `delegate_pursuit` commits | `pursuit_<id>/`, first `leg_<id>/`, first `attempt_<id>/`, `goal.md`, and `leg_goal.md` exist. In dynamic mode the first `leg_goal.md` inherits `pursuit_goal`; in predefined mode it uses `leg_goals[0]`. No work items or summary files exist. |
| T1 | Planner submits valid dynamic payload | `plan_summary.md` appears; work-item directories and static files appear; `next_leg_goal.md` appears or updates if declared. |
| T1P | Planner submits valid predefined payload | `plan_summary.md` appears; work-item directories and static files appear; `leg_goal.md` remains predefined; `next_leg_goal.md` is derived only from the next predefined entry when one exists. |
| T1R | Dynamic planner submits replacement `leg_goal` | Prior live attempts relocate under `superseded/`; `leg_goal.md` updates; standing `next_leg_goal.md` resets unless the same payload declares a new one. |
| T1PR | Predefined planner submits `leg_goal` or `next_leg_goal` | Submission is rejected as a correctable payload error; no work items appear and no attempt budget is consumed. |
| T1F | Planner dies or context composition fails before valid payload | Attempt fails with `fail_reason.md`; `plan_summary.md` is absent; attempt `outcome.md` appears with `(no work items)`; retry attempt appears if budget remains. |
| T2 | Worker submits one work item result | That work item gains `summary.md` and `outcome.md`; attempt stays running unless all completion rules are satisfied. |
| T3 | All work items in an attempt succeed | Attempt becomes `Success`; attempt `outcome.md` appears; leg closes or promotes according to dynamic `next_leg_goal` or the predefined list. |
| T4 | A work item fails | That work item gains `summary.md` and `outcome.md`; non-terminal siblings are cancelled; attempt becomes `Failed`; `fail_reason.md` and attempt `outcome.md` appear. |
| T5 | Failed attempt has retry budget left | New retry attempt directory appears; leg remains running; no leg outcome yet. |
| T6 | Failed attempt exhausts retry budget | Leg becomes `Failed`; leg `outcome.md` appears; pursuit becomes `Failed`; pursuit `outcome.md` appears. |
| T7 | Successful dynamic leg has `next_leg_goal` | Current leg `outcome.md` appears; next leg and its first attempt directory appear with `leg_goal.md` inherited from previous successful leg's `next_leg_goal`. |
| T7P | Successful predefined leg has another predefined goal | Current leg `outcome.md` appears; next leg and its first attempt directory appear with `leg_goal.md` from the next `leg_goals` entry. |
| T8 | Successful dynamic leg has no `next_leg_goal` | Leg `outcome.md` appears; pursuit becomes `Success`; pursuit `outcome.md` appears. |
| T8P | Successful predefined final leg | Leg `outcome.md` appears; pursuit becomes `Success`; pursuit `outcome.md` appears. |
| T10 | Pursuit is cancelled | Non-terminal entities become `Cancelled`; business outcome files are not created for cancelled attempts/legs; pursuit cancellation marker may appear. |

## 16. Unit Test Matrix

Each row should be covered by a focused Vitest case or an `it.each` case table.
Prefer package-local unit tests over broad e2e unless the assertion requires
real runtime wiring.

| ID | Test target | Scenario | Assertions |
| --- | --- | --- | --- |
| C01 | `@eos/contracts` planner payload | Adaptive payload omits `leg_goal` and `next_leg_goal`. | Schema accepts; parsed payload has no focus/deferred fields. |
| C02 | `@eos/contracts` planner payload | Adaptive payload has `next_leg_goal` without `leg_goal`. | Schema accepts successor-only declaration. |
| C03 | `@eos/contracts` planner payload | Payload uses old `iteration_focus`, `focus`, or `deferred_goal`. | Schema rejects or strips old fields according to existing strictness policy; no public type exports them. |
| C04 | `@eos/contracts` creation payload | `create_pursuit` adaptive input has only `pursuit_goal`. | Schema accepts and resolves mode to `adaptive`. |
| C05 | `@eos/contracts` creation payload | Sequential input has non-empty `leg_goals`. | Schema accepts and resolves mode to `sequential`. |
| C06 | `@eos/contracts` creation payload | Sequential input has empty `leg_goals`. | Schema rejects; sequential mode never starts without a first predefined leg. |
| B01 | `@eos/pursuit` package boundary | Package imports are inspected. | Package does not import `@eos/db`, `@eos/tool`, supervisor/background packages, runtime composition, profile loading, or engine internals; any `@eos/agent-runtime` import is launch-port-only. |
| B02 | Source/file layout | Renamed files and folders are inspected. | `packages/pursuit`, `agent-launcher.ts`, singular `transition.ts`, and `projection/` exist; `packages/workflow`, `launcher.ts`, `transitions.ts`, and source `archive/` do not remain active. |
| A01 | Pursuit creation | Adaptive pursuit starts with `pursuit_goal`. | First leg exists immediately; `leg_goal.md` equals `pursuit_goal`; provenance is inherited from pursuit goal. |
| A02 | Planner submission | First adaptive planner omits `leg_goal`. | Submission succeeds; work items are created against existing `leg_goal`; no refocus occurs. |
| A03 | Planner submission | Adaptive planner submits successor-only `next_leg_goal`. | Submission succeeds; `next_leg_goal.md` appears; successful leg creates next leg with that value as `leg_goal`. |
| A04 | Planner submission | Adaptive retry omits both goal fields after a standing successor exists. | Current `leg_goal` and standing `next_leg_goal` are preserved. |
| A05 | Planner submission | Adaptive planner submits new `leg_goal` without `next_leg_goal`. | Current leg refocuses; prior live attempts move to `superseded/`; standing `next_leg_goal` is cleared. |
| A06 | Planner submission | Adaptive planner submits new `leg_goal` and `next_leg_goal`. | Current leg refocuses; prior live attempts move to `superseded/`; new successor is set. |
| A07 | Declaration derivation | Multiple adaptive declarations touch goals. | Latest declaration wins; `is_consistent_with_leg_goal` is false only for displaced attempts. |
| A08 | Planner failure | Planner dies before valid adaptive payload. | Attempt fails with `fail_reason.md`; `plan_summary.md` is absent; retry budget behavior is unchanged. |
| S01 | Pursuit creation | Sequential pursuit starts with `pursuit_goal` and `leg_goals`. | First leg exists immediately; `leg_goal.md` equals `leg_goals[0]`; provenance is predefined. |
| S02 | Planner submission | Sequential planner omits `leg_goal` and `next_leg_goal`. | Submission succeeds; work items are created against predefined current leg goal. |
| S03 | Planner submission | Sequential planner submits `leg_goal`. | Submission is rejected as correctable; no work items are created; attempt budget is not consumed. |
| S04 | Planner submission | Sequential planner submits `next_leg_goal`. | Submission is rejected as correctable; predefined list remains the only next-leg source. |
| S05 | Retry behavior | Sequential leg retries after an attempt failure. | Retry attempt keeps the same predefined `leg_goal`; planner still cannot refocus. |
| S06 | Leg promotion | Sequential non-final leg succeeds. | Next leg is created from the next `leg_goals` entry; provenance is predefined. |
| S07 | Pursuit success | Sequential final leg succeeds. | Pursuit closes `Success`; no extra leg is created. |
| S08 | Pursuit failure | Sequential leg exhausts retry budget before final leg. | Current leg and pursuit close `Failed`; later predefined legs are not created. |
| P01 | Context path universe | Load context tree after creation and submissions. | Paths use `pursuit_<id>/leg_<id>/attempt_<id>`; no path contains `workflow_`, `iteration_`, `/plan_`, `focus.md`, `deferred_goal.md`, or `archived/`. |
| P02 | Projection mirror | Disk mirror writes context tree. | Mirror root is `.eos-agents/pursuit/context/pursuit_<id>/`; stale `.eos-agents/workflow/context` output is not written. |
| P03 | `leg_goal.md` rendering | Render all provenance sources. | First adaptive, adaptive successor, adaptive refocus, and sequential predefined legs render the correct provenance line. |
| P04 | `next_leg_goal.md` rendering | Compare absent, adaptive declared, adaptive reset, and sequential preview cases. | File absence/presence/content matches effective successor semantics for each mode. |
| P05 | Superseded relocation | Adaptive refocus displaces older live attempts. | Whole attempt subtree moves under `superseded/`; live location is pruned from DB projection and disk mirror. |
| P06 | Projection listing/search | Query context with and without superseded scope. | Search excludes `superseded/` by default and includes it only when explicitly scoped. |
| O01 | Attempt outcome | Work items finish success/failure/cancelled in planner order. | `# Attempt outcome` renders ordered rows with worker summaries or `(no summary)`. |
| O02 | Planner-death outcome | Planner dies before work items. | Attempt `outcome.md` renders `(no work items)` and no plan context folder appears. |
| O03 | Leg outcome | Retry attempt fails before budget exhaustion. | Leg `outcome.md` is absent until success or final failure. |
| O04 | Pursuit outcome | Multi-leg adaptive and sequential pursuits close. | Root `# Pursuit outcome` renders closed leg sections in sequence order with `leg_<id>` labels. |
| O05 | Cancellation | Running pursuit is cancelled. | Non-terminal descendants are `Cancelled`; business outcome files are not created for cancelled attempts/legs; root cancellation marker may render. |
| M01 | Script variable map | Load `.eos-agents/pursuit/scripts/variable_reference_map.cjs`. | Exposes pursuit/leg names only; no workflow/iteration/focus/deferred variables remain. |
| M02 | Planner script | Adaptive first-leg input is rendered. | Initial messages include pursuit context, current leg goal, omit-`leg_goal` guidance, and no old vocabulary. |
| M03 | Planner script | Adaptive retry with standing `next_leg_goal` is rendered. | Message says omission preserves standing successor and refocus resets it. |
| M04 | Planner script | Sequential input is rendered. | Message says caller predefined the leg goal and instructs omission of both `leg_goal` and `next_leg_goal`. |
| M05 | Worker script | Worker input with dependencies is rendered. | Initial messages include assigned work and direct dependencies only; they prohibit planning legs or deciding `next_leg_goal`. |
| L01 | Agent launcher | Planner launch is claimed after pursuit commit. | Launch port receives current pursuit/leg/attempt/plan locator and `pursuit_context`; no launch occurs before commit. |
| L02 | Agent launcher | Worker launches after accepted plan. | Launch port receives current work item locator and direct dependency context. |
| L03 | Agent launcher | Context composition fails. | Service synthesizes a failed planner/worker settlement through existing failure path. |
| H01 | Caller handle | Non-agent caller creates pursuit and calls `settle`. | Settlement resolves to terminal pursuit result without requiring background-supervisor ownership. |
| H02 | Caller handle | Non-agent caller calls `cancel`. | Pursuit and non-terminal descendants cancel; repeated cancel is idempotent. |
| H03 | Tool adapter | Agent caller delegates pursuit. | Tool adapter registers the pursuit handle as background session type `"pursuit"` and exposes normal cancel behavior. |
| V01 | Identifier scan | Product TypeScript source and active scripts are scanned. | No active `iteration_focus`, `deferred_goal`, `workflow_context`, `workflow_<id>`, `iteration_<id>`, `archived/`, `focus.md`, `delegate_workflow`, `@eos/workflow`, or `.eos-agents/workflow/scripts` remains. |

## 17. Verification Commands

Focused commands:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm exec vitest run \
  packages/contracts/tests/pursuit.test.ts \
  packages/pursuit/tests/package-boundary.test.ts \
  packages/pursuit/tests/creation-schedule.test.ts \
  packages/pursuit/tests/context.test.ts \
  packages/pursuit/tests/mirror.test.ts \
  packages/pursuit/tests/lifecycle.test.ts \
  packages/agent-runtime/tests/pursuit-runtime.test.ts
pnpm run typecheck
pnpm run lint
pnpm run test
```

Docs hygiene:

```bash
git diff --check -- docs/plans/agent-core-rust-to-typescript-migration eos-agent-core .eos-agents/pursuit/scripts
```

Identifier-boundary scans:

```bash
rg -n "iteration_focus|deferred_goal|workflow_context|workflow_<id>|iteration_<id>|archived/|focus\\.md" eos-agent-core .eos-agents/pursuit/scripts
rg -n "delegate_workflow|type: \"workflow\"|workflow_id|iteration_id|@eos/workflow|packages/workflow|workflowDb|workflowContextRoot|\\.eos-agents/workflow/scripts" eos-agent-core .eos-agents/pursuit/scripts
```

## 18. Acceptance Criteria

Phase 05.3 is accepted when:

- product-facing contracts use pursuit/leg vocabulary,
- planner payloads use `leg_goal` and `next_leg_goal`,
- first planner submissions no longer require a focus/leg-goal declaration,
- `next_leg_goal` is accepted without a sibling `leg_goal`,
- refocus with `leg_goal` supersedes prior live attempts and resets standing
  successor scope,
- `create_pursuit(pursuit_goal, [leg_goal...])` runs sequentially with
  predefined leg goals and rejects planner refocus/successor declarations,
- pursuit handles expose caller-agnostic `cancel` and `settle` behavior,
- rendered context paths use `pursuit_<id>/leg_<id>/superseded/`,
- the context mirror root is `.eos-agents/pursuit/context`,
- `leg_goal.md` exists at leg creation and includes provenance,
- `focus.md`, `deferred_goal.md`, and `archived/` are gone from the live context
  universe,
- `@eos/workflow` / `packages/workflow` are replaced by `@eos/pursuit` /
  `packages/pursuit`,
- package/file names use `agent-launcher.ts`, singular `transition.ts`,
  `projection/`, and `.eos-agents/pursuit/scripts`,
- `Plan` remains DB/launch/submission state and does not reappear as a rendered
  context entity,
- `.eos-agents/workflow/scripts` is no longer an active initial-message script
  root,
- planner and worker scripts emit the Phase 05.3 pursuit/leg initial messages,
- focused tests plus `pnpm run test` pass.
