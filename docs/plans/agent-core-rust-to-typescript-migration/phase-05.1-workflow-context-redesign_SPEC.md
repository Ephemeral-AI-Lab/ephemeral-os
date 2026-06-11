# EOS Agent Core Rust to TypeScript Migration - Phase 05.1 Workflow Context Redesign

Status: Proposed
Date: 2026-06-11
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Base spec: `phase-05-workflow-orchestration_SPEC.md` (Phase 05 is not yet
implemented; this spec amends its context model, projection layer, and
launch-context policy before implementation - where the two conflict, this
spec wins, and both land as one combined effort)
Companion spec status: `docs/plans/workflow_context_projection_SPEC.md` is
retired as the rendering contract for this surface (§4)
Depends on: Phase 04.5 (`@eos/agent-runtime` profiles, hooks, launch shape),
Phase 04 (`@eos/tool`, submission seam), Phase 03 (`@eos/engine`),
Phase 02 (`@eos/contracts`)

## 1. Intent

Phase 05 gives every retry plan its own `plan_spec`, so a closed iteration's
achievement is a collection of attempts whose intents may all differ - the
iteration has no stable identity to judge its closure against. Phase 05.1
replaces that model with a planner-declared **iteration focus**:

- the workflow splits its goal into an immutable `original_goal` and a derived
  `current_goal` (what remains to be done),
- each iteration commits to one `focus` - the slice of `current_goal` it will
  complete - optionally peeling off a `deferred_goal` (the declared remainder,
  promoted to the next iteration's goal on success),
- declarations are append-only rows on plans; the current focus, the current
  goal, and every archived predecessor are all derived views over them,
- the closure outcome of an iteration comes from its last attempt, and only
  attempts consistent with the final focus count as the iteration's
  achievement.

The projection layer is rebuilt to match: composed `spec.md`/`brief.md`
projections are replaced by a per-field file universe (one fact, one path)
with derived `archived/` sections, persisted to disk as a post-commit
mirror (§2.17), and the fixed launch-context policy is replaced by
full-variable snapshots composed either by a built-in default policy or by
a user-configured context script with the same ergonomics as the existing
`.eos-agents/hooks` command hooks.

Phase 05's durable orchestration spine is untouched: claim-in-transaction /
launch-after-commit, settlement synthesis (§2.7 there), the one-session
supervisor story, the one-open-workflow guard, `AgentLaunchPort`, and the
revision counter with its `(revision, path)` render memoization all survive
exactly as specified (the in-memory `WorkflowCell`, `liveRuns`, serial
reconcile queue, and the §2.19 in-content stamp do not - §2.7, §2.21).

## 2. Design Decisions

1. **Goal model: immutable `original_goal`, derived `current_goal`.** The
   workflow stores only the caller's ask. `current_goal` is the head of the
   deferral chain: it equals `original_goal` until an iteration closes
   `Success` carrying a `deferred_goal`, at which point it advances to that
   deferral. It is computed in `loadAggregate`, never stored - storing it
   beside a reconstructible chain would create a second copy to keep in sync.
2. **Focus is iteration-scoped and planner-declared.** `plan_spec` is
   deleted. The first planner submission of an iteration must declare
   `focus`; until then the iteration has no focus and the planner's job is
   exactly to peel one off `current_goal`. Scoping moves from
   creation-time inheritance (Phase 05 `iterations.goal`) to plan-time
   declaration.
3. **Declarations are append-only and atomic.** A declaration is the pair
   `(iteration_focus, deferred_goal?)` - one peel of `current_goal` -
   recorded on the submitting plan row as `declared_focus` /
   `declared_deferred_goal`. Submitting the pair resets both; omitting it
   keeps the standing declaration. No iteration column mutates: the
   iteration's current focus and deferred goal are views over its ordered
   plan rows, which is also what makes the §9 archives derivable. A
   `deferred_goal` can never exist without the focus that produced it.
4. **Refocus supersedes in place; the budget counts all attempts.** A retry
   planner that re-declares focus supersedes the prior declaration inside
   the same iteration - it does not open a new one (a new iteration would
   refresh the attempt budget after a failure, letting a pivoting planner
   loop forever). An attempt is **consistent** iff no later plan in its
   iteration declared a focus. `max_attempts` counts every attempt in the
   iteration, refocused or not; a planner that wants a fresh budget has the
   honest path of declaring the work in `deferred_goal`.
5. **`deferred_goal` is a handoff declaration, not load-bearing state.**
   `current_goal` advances only at successful iteration close, so a refocus
   that drops the previous deferral loses nothing: the next planner re-peels
   from an unchanged `current_goal`. This is also why retry-planner context
   omits the standing `deferred_goal` by default (§2.13) - it is not part of
   the iteration's focus.
6. **Plan survives as the planning-act record.** With `plan_spec` gone a
   plan carries `status`, `planner_summary`, the declared pair, and
   `agent_run_id`. Folding it into Attempt was considered and rejected: the
   plan row keeps the launch queue uniform (`kind: 'plan' | 'work_item'`)
   and gives the planner run its binding point.
7. **Per-field projection: one field, one file.** Composed `spec.md` /
   `brief.md` are not implemented. Every entity-local field projects as one
   file named for the field; an absent field is an absent path, never a
   placeholder. Status never gets a file - it rides directory listings. A
   field-file render is the field text, verbatim - no embedded metadata:
   revision and status ride the structured DTO layer (`ContextPage`,
   listing rows, the §7 variables), never the content. The revision
   survives as the concurrency token - read pinning and the render memo
   key always rode the tool input/output, not the rendered text.
8. **Archives hold what the parent's achievement story excludes.** Two
   kinds, both derived:
   - `workflow/archived/iteration_<k>/current_goal.md` is the superseded
     goal *value* iteration `k` pursued; it exists iff a successor
     iteration exists. The iteration folder itself stays live - closed
     iterations are the workflow's achievement chain, not abandoned work.
   - `iteration/archived/attempt_<a>/` is a *drifted attempt* - one
     superseded by a later focus declaration - relocated whole: its
     `fail_reason.md`, plan summary, and work items render there in their
     live shapes, plus `focus.md` / `deferred_goal.md` at the attempt root
     when attempt `a`'s plan made the now-superseded declaration.
     Non-declaring drifted attempts relocate without declaration files;
     they ran under the nearest preceding sibling's declaration.
   Nothing archives at mutation time: "archived" is purely derived (a
   later declaration exists), so archives stay automatically correct under
   idempotent transitions and cancel races, and the live attempt set under
   `iteration_<id>/` is exactly the consistent set - an iteration's folder
   always reads as the current focus's story. A refocus is the one event
   that changes an entity's path; §9 names the recovery rules.
9. **The tree listing is the overview projection.** A read path (the
   `read_workflow_context` surface - tool exposure deferred, §2.18; the
   resolver and listing ship package-side and these semantics bind when
   the tool lands) resolving to a directory (the root by default) returns the subtree
   listing: one row per path with the owning entity's status and, where a
   summary field exists, its first line. This replaces the Phase 05 default
   root `brief.md`.
10. **One fact, one path - `archived/` excluded from search by default.**
    With no composed projections, every fact has exactly one live path, so
    Phase 05 §2.17's dedup rule holds by construction and `field` in a
    search hit is simply the filename. The archive reintroduces controlled
    duplication (an archived `current_goal` repeats the predecessor
    iteration's `deferred_goal` declaration in a different role), so
    `query_workflow_context` (deferred tool, §2.18) skips `archived/`
    subtrees unless `scope` names a path inside one. Drifted attempts ride the exclusion:
    abandoned-direction outcomes stop surfacing to current-focus agents by
    default, while retry planners still receive them through the §2.11
    variables, which read the aggregate, not paths.
11. **Launch context = full variable snapshot + pluggable composer.** The
    runtime-side variable builders produce a versioned, typed snapshot per
    agent kind containing *all* facts - including ones the default policy
    hides (standing `deferred_goal` on retry, superseded declarations).
    Hiding is policy; the composer decides. The scheduler takes one injected
    `composeLaunchContext(agentName, input)` function and calls it after
    commit, before `port.launch`.
12. **Context scripts are hook-parity subprocesses bound by agent kind.**
    Scripts live in `.eos-agents/workflow/scripts/` and bind by filename:
    `planner.(cjs|mjs)` / `worker.(cjs|mjs)`, falling back to the built-in
    default policy. Kind is also the input shape (§7), so one script
    serves every profile of its kind; per-profile (agent-name) overrides
    stay a deferred seam (§5). The runtime spawns the bound script
    per launch with the JSON snapshot on stdin and parses
    `{ messages: [{ role: "user", content }] }` from stdout - the same
    mental model, trust level, and execution discipline as the
    `.eos-agents/hooks` command hooks (spawned, never imported). The
    script's output IS the launch's complete ordered `initialMessages` -
    replace, never merge: the runtime appends no preamble or directive,
    and the only other model-visible context is the profile's system
    prompt and tool exposure. Without a matching script, the built-in
    default policy (an in-package pure function) composes - so the
    workflow suite spawns no processes.
13. **Default composition policy.** Initial planner: `current_goal`, then a
    directive to declare focus (and optionally `deferred_goal`) and plan
    work items. Retry planner: `current_goal`, the standing focus, the
    *consistent* failed attempts (work items with summaries/outcomes and
    `fail_reason`), then a directive to re-plan within the focus or refocus
    (naming that refocus resets both fields) - the consistent failed
    attempts arrive fully expanded in the variables, so no read escalation
    exists or is needed this round (§2.18); the standing `deferred_goal`
    and superseded attempts are deliberately omitted (§2.5). Worker: the
    iteration focus, dependency outcomes, own `work_item_spec`, submit
    directive.
14. **Compose failures ride the §2.7 uniform rule.** A script that exits
    non-zero, times out, or emits output failing the Zod parse means the
    launch never happens: the scheduler synthesizes a failed settlement for
    the claimed entity, recording `fail_reason: "context_script_error: …"`,
    and the ordinary retry path runs. `max_attempts` bounds the damage from
    a broken user script; nothing can wedge in `Running`.
15. **Submission validation is in-run, end to end.** With the bound
    submission seam (§2.19) the old "cannot check in-run" constraint is
    gone: shape (Zod), structure (unique local ids, declared `needs`, no
    cycles), and materialization rules (first declaration present,
    `agent_name` registered) all return an error result the agent corrects
    before terminating. No attempt burns for a correctable payload; the
    fail-the-attempt path survives only as death synthesis (§2.19) for
    runs that settle without ever submitting validly.
16. **Closure outcomes derive from the last attempt.** An iteration's
    `outcome.md` is composed at render time from its closing attempt's plan
    summary and work-item summaries/outcomes. Prior iterations collapse to
    a status row in listings (the Phase 05 §2.20 rule), and rows under
    `archived/` subtrees render as status rows only; with drifted attempts
    relocated (§2.8) the live iteration subtree needs no further collapse -
    it contains only consistent attempts. The workflow terminal summary
    mechanism is unchanged.
17. **The context tree persists to disk as a post-commit mirror.** Each
    mutation, after commit and before guarded launches, re-renders the §9
    universe from the fresh aggregate and mirrors it under
    `<workflowContextRoot>/workflow_<id>/` (default
    `.eos-agents/workflow/context/`): temp-file + atomic rename per file,
    and paths that left the universe (a refocus relocation) are pruned.
    The DB stays authoritative, rendering never reads these files, and the
    tools keep rendering from the aggregate - the mirror serves humans
    tailing a workflow and the deferred sandboxed-worker seam. Per-field
    files keep each write small, and a launch-token guard (§2.21) prevents a
    stale post-commit projector/launcher from starting work after a competing
    cancel or settlement changed the entity. A write failure is non-fatal:
    logged, state untouched, healed by the next mutation's re-projection.
    `.eos-agents/workflow/` splits cleanly: `scripts/` is user-authored,
    `context/` is machine-written.
18. **The workflow tool family is `delegate_workflow`, alone.** No
    `cancel_workflow`: cancellation rides the background family -
    `cancel_background_session` on the registered `workflow` session
    reaches the handle's cancel, which runs the Phase 05 §8 cascade. No
    read/query tools this round: the addressing, resolver, and listing
    layers ship package-side (§9), but tool exposure awaits a later
    discussion - the §7 variables carry prior-attempt outcomes fully
    expanded, so the default policies need no read escalation.
19. **Submissions validate and mutate in-run through an entity-bound
    seam.** Amends Phase 05 §2.7: `AgentLaunchPort.launch` gains an
    optional `SubmissionBinding` - `{ kind, submit(payload) }` - built by
    the scheduler per claimed entity and wired by the runtime into the
    child run's terminal submission tool. `execute` validates (§2.15) and
    awaits `submit`, which runs one DB transaction (mutate + claim), then
    mirrors and launches through guarded claim tokens (§2.21), and returns
    `{ ok }` or `{ ok: false, error }` for in-run correction. Settlement
    consumption reduces to death synthesis: `onSettlement` synthesizes a
    failed submission only for an entity still `Running` when its run
    settles; an entity already terminal is a no-op through idempotent DB
    guards. Runs launched outside a workflow carry no binding and keep the
    shipped service-free submission tools.
20. **Attempt failure cancels the attempt's remaining work.** The mutation
    that fails an attempt marks its other non-terminal work items `Cancelled`
    in the same transaction and advances the workflow abort generation
    (reason `attempt_failed`) so their runs observe cancellation through the
    workflow signal; their late settlements find terminal entities and no-op.
    No zombie `Running` rows, no tokens spent on a doomed attempt - the cancel
    cascade's shape, one level down.
21. **No `WorkflowCell`, no `liveRuns`, no in-memory workflow queues.**
    `WorkflowService` keeps only the minimal active handles that the caller
    session needs: a terminal resolver per active workflow, and a workflow
    `AbortController` per active workflow so all planner/worker launches share
    one cancellation signal. Ordering and deduplication are DB facts:
    mutations reload fresh state, terminal guards no-op, `claimLaunchable`
    stamps a unique `launch_token`, and the post-commit launcher rechecks that
    token and `Running` status before stamping `agent_run_id` and calling
    `AgentLaunchPort.launch`. A cancel or settlement that wins the race changes
    the row first; the guarded launcher skips instead of starting stale work.

## 3. Phase 05 Amendments

Recorded deltas against `phase-05-workflow-orchestration_SPEC.md`;
everything not listed is implemented as written there.

| Phase 05 item | Amendment | Decision |
| --- | --- | --- |
| §2.2 projection is virtual only; the physical writer is a deferred seam | the disk mirror is in scope as a post-commit cache under `.eos-agents/workflow/context/`; virtual rendering stays the tool contract | §2.17 |
| §2.13 ten brief/spec renderers + two combinators; companion §8 templates | replaced by field-file renders and the tree listing; no composed projections exist | §2.7, §2.9 |
| §2.14 goals ride the launch directive, briefs stay goal-free | moot - the composer owns all placement over full variables | §2.11, §2.13 |
| §2.20 prior iterations collapse in the workflow brief | becomes listing policy; drifted attempts relocate under `archived/` instead of collapsing in place | §2.8, §2.16 |
| §2.17 search over entity-local fields only, dedup rule | one fact one path by construction; `field` = filename; `archived/` excluded by default | §2.10 |
| §6 `PlannerOutcomePayloadSchema` (`plan_spec`, top-level `deferred_goal_for_next_iteration`) | atomic optional `focus` group replaces both; `plan_spec` deleted | §7 |
| §6 schema: `workflows.goal`, `iterations.goal`, `plans.plan_spec`/`deferred_goal` | `workflows.original_goal`; iterations carry no goal/focus columns; plans gain `declared_focus`/`declared_deferred_goal` | §8 |
| §7 `context.ts` fixed launch policy | variable builders + injected composer + default policy + kind-bound `workflow_context/` scripts | §2.12, §10 |
| §7 default read at workflow root = `brief.md` | directory paths (root included) return subtree listings | §2.9 |
| §2.19 every rendered projection opens with a revision stamp line | dropped: content is verbatim field text; revision and status are DTO fields (`ContextPage`, listing rows), and the revision survives as concurrency token + memo key | §2.7 |
| §13 step 3 renderer tests bind the companion §12 criteria | replaced by the §15 projection/derivation tables | §15 |
| §14 case 3 rendering assertions | replaced by §15 case 3 | §15 |

| §2.7 submission tools are service-free; the scheduler is the submission's only consumer | workflow-launched runs get entity-bound submission execute - validate + mutate in-run through DB-guarded transitions; settlement consumption reduces to death synthesis against still-`Running` entities | §2.19, §2.21 |
| §2.16/§8 `AgentLaunchPort.launch(agentName, initialMessages)` | gains an optional `SubmissionBinding` third parameter | §2.19 |
| §2.17/§9 tool family: `delegate_workflow` + `read_workflow_context` + `query_workflow_context` | the family is `delegate_workflow` alone; cancel rides `cancel_background_session`; the read/query tool surface is deferred to a later discussion | §2.18 |
| §2.8-§2.9 `WorkflowCell`, `liveRuns`, and per-workflow promise queue | removed; DB rows carry claims, launch tokens, terminal guards, and cancellation generations; `WorkflowService` keeps only active terminal resolvers and workflow abort controllers | §2.21 |

Unchanged and re-affirmed: §2.3 status enum, §2.4 minted IDs, §2.8-2.12
session machinery, §2.18 bound functions. §2.7 (settlement consumption),
§2.8-§2.9 (active scheduler shape), §2.16 (port signature), and §2.17
(read tools) are amended by the rows above; the §2.7 synthesis rule itself
survives as the death path.

## 4. Companion Spec Status

`docs/plans/workflow_context_projection_SPEC.md` remains the historical
record of the entity model and the §9 lifecycle flows, but its rendering
contract (§1 spec/brief model, §6, §8, the §12 rendering criteria, and
invariants 6-15) is retired for the TypeScript surface: this spec's per-field
projection replaces it. The Phase 05 §3 amendment rows that adjusted that
rendering contract are subsumed by §3 here.

## 5. Scope

In scope (all as amendments to the Phase 05 packages, landed together with
Phase 05):

- `@eos/contracts`: the reshaped planner payload schema, context-script IO
  DTOs (`PlannerContextInput`, `WorkerContextInput`, `ContextScriptOutput`),
- `@eos/db`: the reshaped schema and the derived views in `loadAggregate`,
- `@eos/workflow`: per-field projection + tree listing (replacing
  `render/`), the §2.17 disk mirror, variable builders + default
  composition policy (replacing `context.ts`), the composer seam on the
  scheduler, materialization-time declaration rules, the §14
  entity-oriented module layout,
- `@eos/tool`: `tools/workflow/delegate_workflow.ts` (the family's only
  tool, §2.18) with supervisor registration and the one-open guard, the
  per-kind submission schemas with bound-mutation execute (§2.19),
  `cancel_background_session` type union gaining `"workflow"`,
- `@eos/agent-runtime`: the `.eos-agents/workflow/scripts/` registry
  (loaded and validated at startup), the script-runner composer adapter,
  the `workflowContextRoot` mirror dependency.

Out of scope: everything Phase 05 §11 defers except the physical projector
(now in scope, §2.17), plus the context read/query tools
(`read_workflow_context` / `query_workflow_context` - deferred to a later
discussion; their resolver and listing layers ship package-side),
context-script sandboxing beyond the hook trust model, per-profile
(agent-name) context-script overrides (one extra registry lookup when
wanted), non-workflow uses of the composer,
dirty-subtree mirror optimization (the mirror re-projects the workflow per
mutation), an on-disk per-workflow index file for human status visibility
(the mirror carries no metadata), and any stored focus history beyond the
plan rows (none is needed).

## 6. Goal and Focus Model

```text
delegate_workflow(goal)
  Workflow: original_goal  (immutable, the caller's ask)
            current_goal   (derived head of the deferral chain)
    │
    ▼
  Iteration: focus = none until the first planner declares
    │
    ├─ Attempt 1 → planner sees (current_goal)
    │              submits (focus, deferred_goal?, work_items)   focus REQUIRED
    │
    ├─ Attempt n (retry) → planner sees (current_goal, focus,
    │              consistent prior attempts + fail_reasons)
    │              submits (work_items)                          keep focus
    │              or (focus, deferred_goal?, work_items)        refocus: resets
    │                                                            BOTH, supersedes
    │                                                            prior attempts
    └─ closes Success from the last attempt:
         deferred_goal declared → current_goal := deferred_goal,
                                  next Iteration (origin 'deferred_goal')
         none                   → Workflow Success
       closes Failed (budget exhausted) → Workflow Failed
```

Invariants:

1. `current_goal` advances only when an iteration closes `Success` carrying
   a `deferred_goal`; it never changes mid-iteration.
2. Every non-first iteration's predecessor closed `Success` with a deferral,
   so the goal chain has no gaps.
3. `(focus, deferred_goal)` declare and reset atomically; a deferral never
   exists without the focus that produced it.
4. Declarations are append-only; the current focus/deferred pair is the
   latest declaration among the iteration's plans.
5. An attempt is consistent iff no later plan in its iteration declared;
   closure outcomes and retry context consider only consistent attempts,
   and the live attempt paths are exactly the consistent attempts -
   drifted attempts resolve under `archived/` (§2.8).
6. `max_attempts` bounds the iteration's total attempts across refocuses.
7. The iteration's first materialized plan must carry a declaration
   (§2.15); the first declaration may come from a later attempt when an
   earlier planner died before submitting.

## 7. Contracts (`@eos/contracts`)

```ts
const PlannerOutcomePayloadSchema = z.object({
  summary: z.string().min(1),
  focus: z.object({                          // one atomic peel of current_goal
    iteration_focus: z.string().min(1),
    deferred_goal: z.string().min(1).optional(),
  }).optional(),                             // required for the iteration's
                                             // first declaration (§2.15);
                                             // optional (= keep) on retries
  work_items: z.array(z.object({
    id: z.string().min(1),
    agent_name: z.string().min(1),
    work_item_spec: z.string().min(1),
    needs: z.array(z.string()).default([]),
  })).min(1),
});
// WorkerOutcomePayloadSchema is unchanged from Phase 05 §6.
```

Context-script IO, versioned (snake_case serialized DTOs):

```ts
interface PlannerContextInput {
  input_version: 1;
  kind: "planner";
  revision: number;                               // concurrency token (§2.7)
  workflow: { id: string; original_goal: string; current_goal: string };
  iteration: {
    id: string; sequence: number; origin: "initial" | "deferred_goal";
    focus: string | null;                          // null ⇔ no declaration yet
    deferred_goal: string | null;                  // present even on retry (§2.11)
    max_attempts: number;
  };
  attempt: { id: string; sequence: number };
  prior_attempts: Array<{                          // same iteration, ordered
    id: string; status: string; fail_reason: string | null;
    consistent: boolean;                           // §6 invariant 5
    declared_focus: string | null;                 // null = kept
    declared_deferred_goal: string | null;
    work_items: Array<{ id: string; agent_name: string; spec: string;
                        status: string; summary: string | null;
                        outcome: string | null }>;
  }>;
  prior_iterations: Array<{ focus: string; status: string; summary: string }>;
  paths: { iteration: string; last_attempt: string | null; archived: string };
}

interface WorkerContextInput {
  input_version: 1;
  kind: "worker";
  revision: number;
  workflow: { id: string; original_goal: string; current_goal: string };
  iteration: { id: string; sequence: number; focus: string;
               deferred_goal: string | null };
  work_item: { id: string; agent_name: string; spec: string };
  dependencies: Array<{ id: string; spec: string; status: string;
                        summary: string | null; outcome: string | null }>;
  paths: { attempt: string; work_item: string };
}

const ContextScriptOutputSchema = z.object({
  messages: z.array(z.object({
    role: z.literal("user"),
    content: z.string().min(1),
  })).min(1),
});
```

`ContextSearch` keeps its Phase 05 shape; `ContextPage` gains `status`
(the owning entity's), replacing the dropped in-content status line
(§2.7); a search hit's `field` is the filename of the matched file.

## 8. Store (`@eos/db`)

```text
workflows    id PK, parent_run_id, original_goal, status, revision,
             created_at, updated_at, closed_at
iterations   id PK, workflow_id, sequence, origin ('initial'|'deferred_goal'),
             max_attempts, status, timestamps          -- no goal/focus columns
attempts     id PK, workflow_id, iteration_id, sequence, status, fail_reason,
             timestamps                                 -- unchanged
plans        id PK, workflow_id, iteration_id, attempt_id, agent_run_id,
             status, declared_focus, declared_deferred_goal,   -- null = kept
             planner_summary, timestamps                -- plan_spec deleted
work_items   unchanged from Phase 05 §6
launch_queue gains `launch_token`; otherwise unchanged from Phase 05 §6
```

The derived views are computed once per load: the entity `state.ts` modules
(§14) decorate the store's frozen aggregate immediately after
`loadAggregate` - `@eos/db` stays row-shaped and never imports workflow
logic - and renderers and variable builders never re-derive:

| View | Derivation |
| --- | --- |
| goal in effect for iteration `k` | `original_goal` for the first iteration; otherwise iteration `k-1`'s effective `deferred_goal` (§6 invariant 2) |
| `current_goal` | goal in effect for the latest iteration |
| iteration focus / deferred goal | latest plan in the iteration with non-null `declared_focus` |
| attempt consistency | no later plan in the iteration declared |
| workflow archive set | every iteration with a successor (it advanced the goal) |
| iteration archive set | every non-latest declaration, keyed by its declaring attempt |
| iteration outcome | closing attempt's plan summary + work-item summaries/outcomes |

## 9. Context Path Universe and Projection

```text
workflow_<id>/
  original_goal.md
  current_goal.md                          head of the goal chain (derived)
  outcome.md                               terminal only (derived)
  archived/
    iteration_<id>/
      current_goal.md                      the goal in effect DURING that
                                           iteration; exists iff a successor
                                           iteration exists
  iteration_<id>/
    focus.md                               latest declaration (derived)
    deferred_goal.md                       absent if none declared
    outcome.md                             terminal only (derived, §2.16)
    archived/
      attempt_<id>/                        a drifted attempt, relocated whole
        focus.md                           the superseded declaration; both
        deferred_goal.md                   files only on the attempt whose
                                           plan declared it (deferred file
                                           absent if none was carried)
        fail_reason.md                     …plus the attempt's full content,
        plan_<id>/                         identical shapes to a live attempt
          summary.md
        work_item_<id>/
          spec.md
          summary.md
          outcome.md
    attempt_<id>/                          consistent attempts only (§2.8)
      fail_reason.md                       failed attempts only
      plan_<id>/
        summary.md
      work_item_<id>/
        spec.md
        summary.md
        outcome.md
```

Rules:

- One field, one file; an absent field is an absent path (§2.7). Status
  never projects as a file.
- A file renders as its field text, verbatim - no stamp or status line
  (§2.7). `ContextPage` carries `revision` and `status`; listing rows
  carry status; revision pinning for paged reads is unchanged.
- A path resolving to a directory (the workflow root by default) renders the
  subtree listing: per row the relative path, the owning entity's status,
  and the first line of the owning entity's summary field where one exists.
  Prior iterations and rows under `archived/` subtrees appear as their
  status row only (§2.16); their files remain readable at full fidelity.
- Archive labels are the scopes that ran under the value (§2.8): the
  workflow archive by iteration id; the iteration archive keeps every
  drifted attempt under its own id, with the declaration files riding the
  attempt that declared.
- A refocus is the one event that changes entity paths: from the next
  render the drifted attempts resolve only under `archived/`. A fresh read
  against an old live path errors naming the valid children (`archived/`
  among them), and a paging continuation across the move already fails its
  revision pin - the refocusing materialization bumped the revision.
- The same universe persists on disk: the §2.17 mirror writes it 1:1 under
  `<workflowContextRoot>/workflow_<id>/` (default
  `.eos-agents/workflow/context/`), where real directories play the
  listing's role. Tools never read the mirror; it exists for humans
  tailing a workflow and for the deferred sandboxed-worker seam.
- Renders stay memoized per `(revision, path)`; unknown paths error naming
  the valid children at the deepest resolved segment (both unchanged from
  Phase 05).

## 10. Launch Context Pipeline

The post-commit launch step becomes:

```text
for each claimed entity:
  input    = buildPlannerVariables(aggregate, plan)        // or buildWorker…
  messages = composeLaunchContext(agentName, input)        // injected (§2.11)
  guardedStampLaunch(entity, launch_token)                 // still Running?
  port.launch(agentName, messages, submission, workflowSignal)
  launched.outcome.then((s) => onSettlement(entity, s))     // no liveRuns map
```

The guarded stamp is a short transaction after composition and mirror writes:
it verifies that the entity is still `Running` and still carries the
claim's `launch_token`, stamps `agent_run_id`, and returns permission to
launch. If a cancel, attempt failure, or settlement reached the row first,
the guard returns false and no agent run starts.

`buildPlannerVariables` / `buildWorkerVariables` are pure functions in
`@eos/workflow` over the frozen aggregate, producing the §7 snapshots with
every variable populated. The composer is one injected async function; the
package default is the §2.13 policy as a pure function (no subprocess, so
the workflow suite stays engine-free and spawn-free).

The runtime's composer adapter owns script resolution. At startup it loads
the context-script registry from `.eos-agents/workflow/scripts/`:

```text
.eos-agents/workflow/
  scripts/                       user-authored composers
    planner.cjs                  binds every agent_kind: planner profile
    worker.cjs                   binds every agent_kind: worker profile
  context/                       machine-written §2.17 mirror
    workflow_<id>/…              the §9 path universe on disk
```

Resolution per launch: `<agent_kind>` match, else the package default
policy. `.cjs` and `.mjs` both load - scripts are spawned, never imported,
so module flavor is the script's own business. The registry is validated
at `createAgentRuntime`: every filename must name an agent kind, so a typo
(`planer.cjs`) fails at startup, never mid-run - the Phase 04.5
static-validation discipline.

Per launch the adapter spawns the resolved script with the JSON-serialized
snapshot on stdin and parses stdout against `ContextScriptOutputSchema`,
under the same execution discipline as Phase 04.5 command hooks (bounded
timeout). A non-zero exit, timeout, or parse failure is a compose failure
handled by §2.14. The parsed `messages` are the launch's complete
`initialMessages` - replace, never merge (§2.12): a script that drops the
submit directive has removed it; the default policy always carries it.

Reference script shape (the user-side contract, mirroring the existing hook
scripts):

```js
// .eos-agents/workflow/scripts/planner.cjs — stdin: PlannerContextInput JSON
//                                   stdout: { messages: [{role:"user",content}] }
let input = "";
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", () => {
  const ctx = JSON.parse(input);
  const user = (content) => ({ role: "user", content });
  const messages = [user(`# Workflow goal\n${ctx.workflow.current_goal}`)];
  if (ctx.iteration.focus === null) {
    messages.push(user("Declare this iteration's focus …"));
  } else {
    messages.push(user(`# Iteration focus\n${ctx.iteration.focus}`));
    // ctx.prior_attempts.filter((a) => a.consistent) …
  }
  process.stdout.write(JSON.stringify({ messages }));
});
```

## 11. Lifecycle and Transition Flow

Against Phase 05 §8; everything not named is unchanged. The submission
tools drive every mid-workflow transition through the §2.19 bound seam;
the scheduler's settlement path contributes only death synthesis and the
cancel cascade.

```text
delegate_workflow(goal)                              caller's run
  one transaction:
    createWorkflow(Running, original_goal)
      → createIteration(Running, origin 'initial', no focus)
        → createAttempt(sequence 1)
          → createPlan(NotStarted)
            → enqueueLaunch(kind='plan')
    claimLaunchable → Plan Running + launch_token
  register the supervisor session (§12); return workflow_id
  commit → mirror → guarded stamp → launch planner with workflow signal

submit_planner_outcome(payload)                      planner run (§2.19)
  validate shape / structure / materialization       → error result,
                                                       correct in-run (§2.15)
  one transaction: Plan → Success (summary; declared pair when `focus`
                   present, superseding prior attempts §2.4/§2.8)
                   mint WorkItems (NotStarted), rewrite `needs`
                   claim ready items (`needs` empty or Success)
                     → Running + launch_token
  commit → mirror → launch claimed workers → ok → planner terminates

submit_worker_outcome({ is_pass, … })                worker run (§2.19)
  is_pass true:  WorkItem → Success
                 claim newly-ready dependents → launch
                 all items Success → Attempt → Success → Iteration → Success
                   deferred_goal declared → next Iteration + Attempt + Plan,
                     launch planner (current_goal advances by derivation)
                   none declared → Workflow → Success → resolve terminal
                     → caller's session settles
  is_pass false: WorkItem → Failed; Attempt → Failed
                cancel sibling work items + advance abort generation (§2.20)
                 attempts < max_attempts → retry Attempt + Plan → launch
                 else Iteration → Failed → Workflow → Failed → terminal

onSettlement(entity, settlement)
  entity still Running → synthesized failed submission (death, compose
    failure §2.14, interruption) → the same failure path as is_pass false
  entity already terminal → no-op (idempotent guards)
```

| Entity | → Running | → Success | → Failed | → Cancelled |
| --- | --- | --- | --- | --- |
| Workflow | created by `delegate` | final iteration closes with no deferral | an iteration exhausts `max_attempts` | cancel cascade (session cancel / caller dispose) |
| Iteration | created (initial or deferred) | closing attempt's items all `Success` | `max_attempts` exhausted | cancel cascade |
| Attempt | claim at planner launch | all its work items `Success` | any work item `Failed`, or its plan's death synthesis | cancel cascade |
| Plan | claim → planner launched | accepted `submit_planner_outcome` | death synthesis while `Running` | cancel cascade |
| WorkItem | ready claim → worker launched | accepted `is_pass: true` | `is_pass: false` or death synthesis | cancel cascade, or `attempt_failed` sibling cancel (§2.20) |

Deltas retained from the focus model:

- `delegate` stores `original_goal`; the first iteration is created with no
  focus (origin `'initial'`).
- Planner materialization: payload `focus` present → record the pair on the
  plan row (this supersedes any prior declaration, resets both fields, and
  relocates the now-drifted attempts' projections under `archived/` purely
  by derivation - no mutation step exists); absent → the plan keeps the
  standing declaration. Validation errors return in-run results (§2.15);
  only a run that settles without a valid submission burns the attempt
  through death synthesis. Work-item materialization and ready-launch are
  unchanged.
- Iteration close (`Success`, from the last attempt): derive the outcome
  (§2.16); if the effective declaration carries a `deferred_goal`, create
  the next iteration (origin `'deferred_goal'`) - `current_goal` advances by
  derivation, and the closing iteration's goal becomes archived by
  construction; otherwise close the workflow `Success`.
- Failure/retry: unchanged, except the retry planner's variables carry only
  consistent prior attempts in expanded form and the budget counts all
  attempts (§2.4).
- Every mutating transition re-projects the disk mirror after commit and
  before guarded launches (§2.17), so a launched agent's filesystem view -
  once the sandboxed-worker seam is consumed - is never older than its own
  claim.
- Compose failures synthesize failed settlements with
  `fail_reason: "context_script_error: …"` (§2.14).
- Cancel cascade, guarded launch serialization, terminal resolution:
  unchanged except for the removed cell/queue/live-run registry (§2.21).

## 12. Tool Family, Session, and Bound Submissions (`@eos/tool`)

The workflow family is one file:
`packages/tool/src/tools/workflow/delegate_workflow.ts` (§2.18). There is
no `cancel_workflow` and no read/query tool this round - cancellation
rides the background family, and the read/query surface awaits a later
discussion.

`delegate_workflow` (input `{ goal, max_attempts? }`; `goal` becomes
`original_goal`). The factory takes one bound function plus the per-run
supervisor; `cancel` folds into the returned handle, so no service method
beyond `delegate` crosses the tool boundary:

```ts
function workflowTools(
  delegate: (input: DelegateWorkflowInput,
             parent: AgentRunId) => Promise<DelegatedWorkflow>,
  supervisor: BackgroundSessionSupervisor,
): ToolDefinition[];

interface DelegatedWorkflow {
  workflowId: WorkflowId;
  terminal: Promise<WorkflowTerminal>;
  cancel(reason: string): Promise<void>;   // resolves after the cascade
  describe(): string;                      // goal one-liner
}

execute: async (input, ctx) => {
  if (supervisor.list().some((s) => s.type === "workflow"))
    return { content: "a delegated workflow is already open …",
             isError: true };                            // one-open guard
  const wf = await delegate(input, ctx.meta.run.run_id);
  supervisor.register({ type: "workflow", id: wf.workflowId },
    ctx.meta.tool_use_id, {
      settled: wf.terminal.then((t) => ({
        status: t.status === "Success" ? "completed"
              : t.status === "Cancelled" ? "cancelled" : "failed",
        summary: t.summary })),
      cancel: wf.cancel,
      describe: wf.describe,
    });
  return { content: { workflow_id: wf.workflowId } };
},
```

Registration precedes the tool result, exactly the subagent pattern:
`openBackgroundSessionCount()` covers the workflow before the model's next token,
settlement publishes one `session_settled` notification, auto-wait parks
an idle caller, the submission guard holds the caller past an unseen
settlement, and `supervisor.dispose` on caller finish cancels through the
handle. `cancel_background_session`'s `type` union gains `"workflow"` -
cancelling the session IS cancelling the workflow: the handle's `cancel`
runs the Phase 05 §8 cascade (interrupt live children, await their
outcomes, mark all non-terminal entities `Cancelled` in one transaction,
resolve the terminal `Cancelled`) and resolves only after teardown.

`tools/submission/submit_planner_outcome.ts` and
`submit_worker_outcome.ts` keep their §7 per-kind schemas and gain bound
mutation (§2.19). The scheduler builds a `SubmissionBinding` per claimed
entity and passes it through the launch port; the runtime wires it into
the child run's terminal tool:

```ts
interface SubmissionBinding {
  kind: "planner" | "worker";
  submit(payload: PlannerOutcomePayload | WorkerOutcomePayload):
    Promise<{ ok: true } | { ok: false; error: string }>;
}
// AgentLaunchPort.launch(agentName, initialMessages, submission?)

execute(payload):
  Zod shape parse                                    → error result
  structure: unique local ids, declared `needs`,
             no cycles                               → error result
  await binding.submit(payload)                      one job on the
    materialization rules: first declaration         per-workflow serial
    present, `agent_name` registered                 queue → { ok:false,
    mutate + claim in one transaction; commit;         error } in-run
    project mirror; launch claimed entities          → { ok: true }
  ok → terminal content; error → isError result for in-run correction
```

A run with no binding (a planner or worker profile started outside any
workflow) keeps the shipped service-free behavior: shape-validate and
ride `outcome.submission`.

## 13. Runtime Wiring Deltas (`@eos/agent-runtime`)

- `createAgentRuntime` loads and validates the `workflow/scripts/`
  registry (§10) beside the existing hook config loading; profiles are
  untouched.
- `AgentRuntimeDependencies` gains `workflowContextRoot?` (default
  `.eos-agents/workflow/context/`), passed to the `WorkflowService` for
  the §2.17 mirror.
- The composer adapter (kind → default resolution; script subprocess when
  bound, package default otherwise) is injected into the `WorkflowService`
  scheduler beside the launch-port adapter.
- The launch-port adapter threads each launch's `SubmissionBinding` into
  per-run tool assembly: a scheduler-launched child's terminal submission
  tool executes against `binding.submit` (§2.19); runs without a binding
  keep the service-free submission tools.
- Everything else in Phase 05 §10 (workflowDb, per-run `workflowTools`,
  name-universe validation, disposal cascade) is unchanged.

## 14. Workspace Changes

Delta to the Phase 05 §12 layout:

```text
packages/workflow/src/
├─ workflow/
│  ├─ state.ts         root aggregate state; goal-chain derivation (§8)
│  ├─ context.ts       original_goal.md / current_goal.md / outcome.md
│  └─ transitions.ts   terminal close (Success / Failed / Cancelled)
├─ iteration/
│  ├─ state.ts         ordered declarations; focus / deferred views (§8)
│  ├─ context.ts       focus.md / deferred_goal.md / outcome.md
│  └─ transitions.ts   close; deferred-goal promotion (next iteration)
├─ attempt/
│  ├─ state.ts         consistency predicate (§6 invariant 5)
│  ├─ context.ts       fail_reason.md
│  └─ transitions.ts   creation; fail/close; retry within max_attempts
├─ plan/
│  ├─ state.ts         declaration record state
│  ├─ context.ts       summary.md
│  └─ transitions.ts   materialization: §11 declaration rules + work-item
│                      creation
├─ work_item/
│  ├─ state.ts         readiness (`needs` all Success)
│  ├─ context.ts       spec.md / summary.md / outcome.md
│  └─ transitions.ts   worker-outcome recording (is_pass → status)
├─ archive/            pure addressing only - membership facts (attempt
│                      consistency, goal chain) live on the entity state
│                      modules; no archive table, mutation, or event exists
│  ├─ paths.ts         pathOf(entity, field?): live vs archived address
│                      over the state-computed membership (§2.8)
│  ├─ resolve.ts       path → file / directory / error naming valid children
│  └─ listing.ts       subtree listing rows: path, status, summary (§2.9)
├─ context_engine/
│  ├─ variables.ts     buildPlannerVariables / buildWorkerVariables (§7)
│  └─ composer.ts      the composeLaunchContext seam (§10) + the §2.13
│                      default composers (the in-package no-script path)
├─ context_projection.ts  the §2.17 disk mirror: render-all over archive
│                      paths, temp-file + atomic-rename writes, prune of
│                      departed paths
├─ scheduler.ts        cell, serial reconcile, claims, compose → project →
│                      launch; declares AgentLaunchPort / LaunchedAgent /
│                      LaunchSettlement / SubmissionBinding (Phase 05
│                      §2.16 contract + the §2.19 seam; the standalone
│                      launch-port.ts file is folded here as the scheduler
│                      is the contract's only consumer)
├─ service.ts          delegate / cancel (read/search reserved for the
│                      deferred context tools; renders from the aggregate,
│                      never from disk)
└─ index.ts            public exports (service, port types)
```

Each entity module owns its slice through one shape - `state.ts` (types +
§8 derivations), `context.ts` (its §9 field files: verbatim field text
plus the derived outcome compositions), `transitions.ts` (local status
mutations over `(trx, aggregate)`);
the scheduler's reconcile job sequences the cross-entity cascade, keeping
Phase 05 §2.15's functions-not-classes rule, distributed by owner.

`@eos/contracts` adds the §7 DTOs; `@eos/db` reshapes the migration and
`loadAggregate`; `@eos/agent-runtime` adds the `workflow/scripts/` registry
loader, the script-runner composer adapter, and `workflowContextRoot`. No new third-party dependencies. The dependency graph
is unchanged.

## 15. Migration Steps and Progress

These replace the corresponding Phase 05 §13 rows; the combined effort lands
under the Phase 05 step list with these substitutions.

| # | Step | Verify | Status |
| --- | --- | --- | --- |
| 1 | Contracts: payload focus group, context-script IO DTOs | §16 case 1 | Planned |
| 2 | `@eos/db`: reshaped schema, derived views in `loadAggregate` | §16 case 2 on `:memory:` | Planned |
| 3 | Projection: field renders, listings, archives, disk mirror | §16 cases 3 + 13 | Planned |
| 4 | Lifecycle + scheduler: declaration rules, composer seam, compose-failure synthesis | §16 cases 4-9, engine-free | Planned |
| 5 | Service delegate/cancel + the `DelegatedWorkflow` handle | §16 cases 10-11 | Planned |
| 6 | `@eos/tool`: `delegate_workflow` family + bound submissions | §16 case 11 | Planned |
| 7 | Runtime: `workflow/scripts/` registry + composer adapter, end-to-end | §16 case 12 | Planned |
| 8 | Workspace wiring + index row | `pnpm run check`; `git diff --stat -- agent-core` empty | Planned |

## 16. Verification

Same harness rules as Phase 05 §14: scripted `AgentLaunchPort`, `:memory:`
databases, engine-free except case 12. Case 12 additionally spawns one real
context script fixture.

| # | Case | Asserts |
| --- | --- | --- |
| 1 | Contracts | focus group accepts/rejects documented shapes; `deferred_goal` never validates without `iteration_focus`; `ContextScriptOutputSchema` rejects empty/role-less messages |
| 2 | Store + derivations | goal chain across iterations (first = original, then each deferral); focus/deferred views track the latest declaration; consistency flags flip on a later declaration; archive sets per §8 table; budget counts attempts across refocuses |
| 3 | Projection | field render = verbatim field text with no embedded metadata (`ContextPage` returns `revision` + `status` instead); absent field = absent path; directory paths render listings with status and summary first lines; prior iterations and `archived/` rows collapse to status rows; drifted attempts render whole under `archived/` with declaration files only on the declarer; an iteration's `outcome.md` derives from the closing attempt; live `current_goal.md` is never simultaneously archived |
| 4 | Delegation | unchanged Phase 05 case 4, plus: the supervisor session registers before the tool result returns, a second `delegate_workflow` is rejected by the one-open guard, and the launched planner's messages come from the default initial policy (goal present, focus-declaration directive present) |
| 5 | Submission validation | a valid first payload records the pair and materializes items in-run before the planner terminates; a payload without `focus`, with an unknown `agent_name`, or with dangling/cyclic `needs` returns an in-run error result and the same run corrects and resubmits successfully - no attempt burns for a correctable payload, and the accepted resubmission mutates exactly once |
| 6 | Keep vs refocus | keep: focus view unchanged, attempt consistent, paths stable; refocus: both fields reset, prior attempts relocate whole under `archived/` at the next render, the resolver errors on the old live path naming `archived/` among valid children, the retry directive carries only consistent attempts and omits the standing `deferred_goal` |
| 7 | Success cascade | unchanged Phase 05 case 6, plus: the next planner's `current_goal` is the promoted deferral; the closing iteration's goal appears under `archived/iteration_<id>/`; no deferral → workflow `Success` with `current_goal.md` still live |
| 8 | Failure and retry | unchanged Phase 05 case 7, with the budget spanning refocuses; exhaustion mid-refocus closes iteration and workflow `Failed`; a failing work item cancels its non-terminal siblings in the same transaction and interrupts their runs (`attempt_failed`, §2.20), and their late settlements no-op with no `Running` rows left |
| 9 | Death + compose synthesis | unchanged Phase 05 case 8, plus: a composer that throws/times out/returns garbage synthesizes a failed settlement with `context_script_error` recorded; synthesis keys off the entity still being `Running` - a run whose in-run submission already landed settles as a no-op; no entity stays `Running` |
| 10 | Serialization + cancel | Phase 05 cases 9-10 re-run against the new model; tool-driven submissions and settlement jobs share the one serial queue (instrumented store sees no interleaved transactions) |
| 11 | Tools | `delegate_workflow` registers the session before returning, rejects a second open delegation, and returns the workflow id; submission tools: shape, structure, and materialization error tables each correctable in-run; unbound planner/worker runs keep service-free submissions; `cancel_background_session` accepts `type: "workflow"` and resolves only after the cascade |
| 12 | Runtime end-to-end | Phase 05 case 12 amended: the caller delegates, auto-waits, drains `session_settled`, and submits; a fixture `workflow/scripts/planner.cjs` composes the planner's complete initial messages (proven by transcript inspection - nothing merged around them); a broken fixture script drives the case-9 synthesis path live; registry load fails fast on a filename naming no agent kind; `cancel_background_session` mid-workflow cascades `workflow_cancelled` into child transcripts and settles the session `cancelled` |
| 13 | Disk mirror | after each scripted lifecycle step the on-disk tree under the context root equals the rendered universe byte-for-byte; a refocus prunes the old live attempt folder and writes the archived one; a write failure (read-only root) leaves DB state and the run unaffected and the next mutation heals the mirror; tools render identically with the mirror deleted |

Commands (unchanged):

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm install
pnpm run check
```

- Rust boundary hygiene: `git diff --stat -- agent-core` stays empty.
- Docs hygiene: `git diff --check -- docs/plans/agent-core-rust-to-typescript-migration eos-agent-core`.

## 17. Coexistence and Rollback

- Coexistence: Phase 05 has no landed implementation; this spec changes
  paper only until the combined effort lands. The Rust implementation
  remains live and unchanged throughout.
- Rollback: delete this spec and its index row; Phase 05 stands as written.
  After implementation, rollback follows Phase 05 §15 unchanged.

## 18. Acceptance Criteria

Phase 05.1 is accepted when, in the combined Phase 05 + 05.1 implementation:

- iterations are governed by planner-declared focus end to end: required
  first declaration (materialization-enforced), keep vs refocus with atomic
  resets, in-place supersession with consistency flags, and a budget that
  spans refocuses,
- `current_goal`, iteration focus/deferred views, and both archive sections
  are derived views over append-only declarations - no mutable goal/focus
  columns and no archive state exist anywhere in the schema,
- the context surface is the §9 per-field path universe: one fact one path,
  verbatim field files with revision and status as DTO metadata (never
  content), directory listings as the overview, derived archives labeled by
  iteration/attempt, and no composed `spec.md`/`brief.md` anywhere,
- the context tree persists as the §2.17 post-commit mirror under
  `.eos-agents/workflow/context/workflow_<id>/`, byte-identical to the
  virtual renders, pruned on relocation, with non-fatal write failures and
  the DB remaining the only source of truth,
- launch context flows through full-variable snapshots and one composer
  seam: the default policy implements §2.13, `.eos-agents/workflow/scripts/`
  scripts override it by agent kind with hook-parity subprocess semantics
  owning the complete initial messages, and every compose failure
  synthesizes a failed settlement through the Phase 05 §2.7 path,
- a delegated workflow is exactly one supervisor session of the caller:
  `delegate_workflow` (the family's only tool) registers before returning,
  the one-open guard holds, the terminal maps onto the session outcome,
  and cancellation rides `cancel_background_session` and the caller
  disposal cascade - no `cancel_workflow` exists, and the read/query tools
  are deferred (§2.18),
- planner and worker submissions validate and mutate in-run through the
  entity-bound seam on the per-workflow serial queue, settlements reduce
  to death synthesis against still-`Running` entities, and attempt failure
  cancels the attempt's remaining work (§2.20),
- Phase 05's orchestration spine passes its suite unmodified except where
  §3 amends it, under `pnpm run check`,
- the Rust `agent-core/` tree is byte-for-byte unchanged,
- and the migration `index.md` lists Phase 05.1 with status and
  verification.
