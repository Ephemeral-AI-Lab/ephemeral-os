# Task Center Harness — Recursive Harness-Graph Migration

> Migration plan that reshapes the harness around **nested harness graphs**, each
> with its own local orchestrator. Every spawn (`REQUEST_PLAN`,
> `RETRY_ON_FAILURE`, `CONTINUE_AFTER_PARTIAL_PLAN`) becomes a child harness
> graph rooted under the spawning context.
>
> Scope: harness-graph hierarchy, orchestrator locality, agent roles, terminal
> tools, retry mechanic, partial-plan continuation, and runtime tool gating.

***

## §1. Architecture (recursive new world)

The harness is a **tree of harness graphs**. Each node owns its agents, its DAG,
and a local orchestrator. A spawn produces a child node. The session ends when
the root node closes.

```
                                    ┌── USER QUERY ──┐
                                    ▼                ▼
                       ╔════════════════════════════════════════╗
                       ║  HARNESS GRAPH  G_root                  ║
                       ║  (no parent; bound to the session)      ║
                       ║                                         ║
                       ║   Orchestrator_root                     ║
                       ║      │ init → spawn root executor       ║
                       ║      ▼                                  ║
                       ║   [root generator/executor]             ║
                       ║      │  submit_request_plan(note)       ║
                       ║      ▼                                  ║
                       ║   Orchestrator_root.spawn_child(        ║
                       ║       reason = REQUEST_PLAN,            ║
                       ║       parent_task = root_executor)      ║
                       ╚═══════════════╪═════════════════════════╝
                                       │
                                       ▼
            ╔══════════════════════════════════════════════════════════╗
            ║  HARNESS GRAPH  G1   (child of G_root via REQUEST_PLAN)   ║
            ║                                                           ║
            ║   Orchestrator_G1                                         ║
            ║      │ init → spawn planner                               ║
            ║      ▼                                                    ║
            ║   [planner] ── submit_full_plan ──► materialize DAG        ║
            ║                          (or submit_partial_plan, gated)   ║
            ║                                                           ║
            ║   ┌────────────────────────────────────────────────────┐  ║
            ║   │ DAG: generator/executor → generator/verifier       │  ║
            ║   │                                                    │  ║
            ║   │   any executor here may itself call                │  ║
            ║   │   submit_request_plan ──► Orchestrator_G1 spawns   │  ║
            ║   │   another child harness graph G1.X                  │  ║
            ║   └────────────────────────────────────────────────────┘  ║
            ║                       │ all generators DONE               ║
            ║                       ▼                                   ║
            ║   Orchestrator_G1 spawns evaluator (sink)                 ║
            ║   [evaluator] ── submit_evaluation_*                       ║
            ║                       │                                   ║
            ║   close G1 ──► report (success | failed) to parent        ║
            ╚═══════════╪═══════════════════════════════════════════════╝
                        │
            ┌───────────┴───────────────────────────────┐
            ▼                                           ▼
   close success → root executor       Orchestrator_G1 spawns
   resumes inside G_root               continuation/retry child of G1
                                       (see §5.3–§5.5)

   ─── Subagent (NOT in TaskCenter; non-blocking) ───────────────────────
       explorer  ── run_subagent(name="explorer", prompt) → future result

   ─── Helpers (NOT in TaskCenter; blocking ask_* calls) ────────────────
       advisor   ── ask_advisor(tool_name, tool_payloads, prompt)
                    → {verdict, reason}                         no edits
       resolver  ── ask_resolver(issues_to_resolve)
                    → {resolved, summaries}                      can edit
```

### Why nesting

- A child harness graph is the only place a non-root spawn can live. There is
  **no global orchestrator** — each parent graph owns the lifecycle of its
  children.
- The previous flat `prior_graph_id` chain collapses into ordinary parent–child
  edges: failures and partial completions are nested under the graph that
  triggered them, not siblings under a shared retry coordinator.
- A `REQUEST_PLAN` spawn resets the lineage history; `RETRY_ON_FAILURE` and
  `CONTINUE_AFTER_PARTIAL_PLAN` extend it.

***

## §2. Components

| Component                | Owner / scope                  | Responsibility                                                                                                                                                  |
| ------------------------ | ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `HarnessGraph`           | TaskCenter                     | Container for one attempt: tasks, DAG edges, evaluator slot, retry budget, close status. Holds `parent_harness_graph_id`, `prior_graph_id`, `spawn_reason`.     |
| `Orchestrator`           | one per `HarnessGraph`         | Drives the local graph: init step, DAG materialization, evaluator gating, child-graph spawning, close transition. **Local — no global orchestrator.** Implemented as an **in-process class instance** keyed off `HarnessGraph.id`; durable state lives on the `HarnessGraph` row, the object itself is ephemeral and looked up on demand by terminal handlers. |
| `RootHarnessGraph`       | TaskCenter (singleton/session) | Special graph wrapping only the root executor. No planner, no DAG, no evaluator. Closing it ends the session.                                                  |
| Tasks                    | per `HarnessGraph`             | Agent runs (planner, executor, verifier, evaluator) scoped to a single graph. State + tool gating evaluated against the owning graph and its lineage.          |

### §2.1 Root harness graph is special

- `G_root` exists for the lifetime of the session.
- It contains **only one task**: the root generator/executor.
- It has no planner, no DAG, no evaluator. Its orchestrator's only jobs are:
  - **Init**: spawn the root executor.
  - **Catch root executor terminals**:
    - `submit_request_plan(note)` → spawn child graph (`REQUEST_PLAN`), wait
      for child close, deliver child summary back to the root executor.
    - `submit_execution_success` / `submit_execution_failure` → close `G_root`
      → session ends.
- The root executor's tool-gating rules are identical to any other executor
  (e.g. `submit_request_plan` is disabled after the first edit).

***

## §3. Spawn reasons (3 types)

Every harness graph except `G_root` is created by exactly one of these spawns.
The orchestrator that performs the spawn is always **the orchestrator of the
graph that becomes the new parent**.

| Spawn reason                  | Trigger                                                                | Spawned by                          | `parent_harness_graph_id`            | `prior_graph_id` (lineage)         | Launch context fed to the new graph                                                                                                                                                                |
| ----------------------------- | ---------------------------------------------------------------------- | ----------------------------------- | ------------------------------------ | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `REQUEST_PLAN`                | An executor task calls `submit_request_plan(note)`                     | Orchestrator of executor's graph    | the graph that contains the executor | — (chain reset)                    | `request_plan_note` from the executor; full toolset for planner; `submit_partial_plan` allowed if no ancestor in `prior_graph_id` chain used it                                                    |
| `RETRY_ON_FAILURE`            | Parent graph enters `REQUESTING_RETRY` and `fail_count ≤ retry_budget` | Orchestrator of failing graph       | the failed graph                     | the failed graph                   | failure summaries (per-task), prior plan, retry attempt counter; planner sees `RETRY ATTEMPT N/M` block                                                                                            |
| `CONTINUE_AFTER_PARTIAL_PLAN` | Parent graph reaches success and `plan_shape == 'partial'`             | Orchestrator of completing graph    | the completed graph                  | the completed graph                | `instructions_on_what_to_do_after_completion_of_partial_plan` from the prior planner; `submit_partial_plan` blocked recursively (only `submit_full_plan` permitted)                               |

### §3.1 Visual: the three spawn paths

```
        ┌──────────────────── parent harness graph G_p ────────────────────┐
        │                                                                  │
        │  (a) REQUEST_PLAN                                                 │
        │      executor in G_p ── submit_request_plan(note) ──► Orch_p      │
        │                                                       └─► spawn G_c
        │                                                                  │
        │  (b) RETRY_ON_FAILURE                                             │
        │      G_p enters REQUESTING_RETRY                                  │
        │      (any generator FAILED/BLOCKED, or evaluator FAILED)          │
        │      Orch_p sees fail_count ≤ retry_budget                        │
        │                                                       └─► spawn G_c
        │                                                          (G_p stays
        │                                                          open until
        │                                                          G_c closes)
        │                                                                  │
        │  (c) CONTINUE_AFTER_PARTIAL_PLAN                                  │
        │      G_p reached graph success but plan_shape == 'partial'        │
        │      Orch_p reads continuation instructions                       │
        │                                                       └─► spawn G_c
        │                                                          (G_p stays
        │                                                          open until
        │                                                          G_c closes)
        └──────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
                                  new harness graph G_c
                                  parent_harness_graph_id = G_p
                                  spawn_reason            = (a|b|c)
                                  prior_graph_id          = G_p when (b) or (c)
                                                            null when (a)
```

### §3.2 Lineage chain semantics

Two distinct upward walks coexist on the same tree:

- `parent_harness_graph_id` ↑ → **containment hierarchy**. Used by orchestrator
  ownership and bubble-up of close summaries.
- `prior_graph_id` ↑ → **attempt history**. Used by tool-gating predicates
  (recursive partial-plan check) and by `build_continuation_note`.

The two walks **overlap** on `RETRY_ON_FAILURE` / `CONTINUE_AFTER_PARTIAL_PLAN`
edges and **diverge** at every `REQUEST_PLAN` boundary, where the lineage
chain resets but the containment chain continues.

***

## §4. Agent roles, helper semantics, and state policy

### §4.1 Role model

TaskCenter owns three main agent roles, all scoped to a single harness graph:

- **Planner**: decomposes a request into either a full plan or a gated partial plan.
- **Generator**: performs generation work. Two kinds — **executor** for direct work and **verifier** for checking generator output. Verifiers are still generator tasks, not evaluator sinks.
- **Evaluator**: runs only after every generator task in the graph has passed. Provides the graph-level acceptance decision.

One subagent role:

- **Explorer**: launched with `run_subagent(name="explorer", prompt)`. Non-blocking, parallel-safe, read-only, reports back through its subagent result path.

Two helper roles:

- **Advisor**: `ask_advisor(tool_name, tool_payloads, prompt)` before terminal submission. Blocking, inline, no edits.
- **Resolver**: `ask_resolver(issues_to_resolve)` when a verifier or evaluator finds issues it cannot resolve through read-only checks. Blocking; one helper call at a time. Resolver can edit and must return `resolved` plus summaries.

Failure authority is role-based. Only roles that own a `submit_*_failure` terminal may declare failure: executor, verifier, evaluator. Planner has no failure terminal.

### §4.2 State-dependent tool policy

Tool availability depends on graph depth, lineage chain, task role, and tool-step history. The runtime composes two layers — neither mutates the system prompt nor changes tool registration:

- **Soft layer**: system reminders inject which terminal tools are currently disabled or required.
- **Hard layer**: terminal-tool prehooks enforce the same policy before the terminal handler runs.

***

## §5. Workflows

### §5.1 Happy path (full plan, single graph)

```
G_root.Orch: init → spawn root executor
     │
     ▼
[root executor] ── submit_request_plan(note)
     │
G_root.Orch catches the terminal
spawns child G1 (REQUEST_PLAN)
     │
     ▼
G1.Orch: init → spawn planner
     │
     ▼
[planner G1] ── submit_full_plan ──► materialize DAG
     │
     ├── [generator/executor 1] ── submit_execution_success
     ├── [generator/executor 2] ── submit_execution_success
     │                                  │
     │                                  ▼
     └── [generator/verifier]  ── submit_verification_success
                                  │
                       all generators DONE
                                  │
G1.Orch spawns evaluator
                                  │
                                  ▼
[evaluator G1] ── submit_evaluation_success
                                  │
G1.Orch closes G1 success
                                  │
                                  ▼
G_root.Orch delivers child_success summary to root executor
                                  │
                                  ▼
[root executor] resumes — may submit_request_plan again or finish
                                  │
                                  ▼
G_root closes → session ends
```

### §5.2 Resolver loop inside a verifier or evaluator

(Behaviour unchanged from the prior plan; runs entirely within one harness graph.)

```
[verifier or evaluator] ── ask_resolver(issues)  [BLOCK] ──► resolver runs
                                                              │ may edit
                                                              ▼
                                          submit_resolver_result(resolved, summaries)
                                                              │
                       ┌──────────────────────────────────────┘
                       ▼
              read {resolved, summaries}
                       │
              ┌────────┴────────┐
       resolved=True       resolved=False
              │                 │
              ▼                 ▼
        re-check &           counter++
        decide               another ask_resolver?
                                   │
                       at counter=5 with resolved=False:
                       prehook BLOCKS submit_*_success;
                       agent must submit_*_failure
```

### §5.3 Generator failure → spawn `RETRY_ON_FAILURE` child of failing graph

```
[generator/executor 2 in G1] ── submit_execution_failure(summary)
                                       │
G1.Orch marks executor_2 FAILED
                                       │
                          dependent generators → BLOCKED
                                       │
                  remaining non-blocked generators keep running
                                       │
                                       ▼
                          generators quiescent in G1
                          (every generator ∈ {DONE, FAILED, BLOCKED})
                                       │
                          any FAILED or BLOCKED?
                                       │
                                  yes ─┘
                                       │
                                       ▼
                          G1.status = REQUESTING_RETRY
                          G1.fail_count += 1
                                       │
                          G1.fail_count ≤ G1.retry_budget?
                                       │
                       ┌───────────────┴───────────────┐
                       ▼ yes                           ▼ no
       G1.Orch spawns child G2                G1.Orch closes G1 failed
       (RETRY_ON_FAILURE)                     parent (G_root) delivers
       parent_harness_graph_id = G1           child_failure summary upward
       prior_graph_id          = G1
       fail_count              = G1.fail_count
       retry_budget            = G1.retry_budget
                       │
                       ▼
       planner G2 launches with context:
         ROOT_GOAL: ...
         PRIOR ATTEMPT (G1):
           PLAN: <G1 dag + details>
           OUTCOMES:
             generator/executor 1: SUCCESS — <summary>
             generator/executor 2: FAILURE — <summary>
             generator/verifier:   blocked by failed dependency
         RETRY ATTEMPT 1/N
```

`G1` stays open while `G2` runs. When `G2` closes, its summary bubbles
through `G1` to whatever requested `G1` (root executor in this example).

### §5.4 Evaluator failure → spawn `RETRY_ON_FAILURE` child

```
[evaluator G1] ── submit_evaluation_failure(summaries)
                       │
                       ▼
G1.status = REQUESTING_RETRY
G1.fail_count += 1
       │
       ▼
(same branch as §5.3)

If under budget:
  G1.Orch spawns G2 (RETRY_ON_FAILURE)
  → planner G2 sees: all generators passed, evaluator rejected,
    replan accordingly
Otherwise:
  G1.Orch closes G1 failed → bubble up
```

### §5.5 Partial plan → spawn `CONTINUE_AFTER_PARTIAL_PLAN` child

```
[planner G1] ── submit_partial_plan(
                    dag,
                    details,
                    instructions_on_what_to_do_after_completion_of_partial_plan
                )
                       │
                       ▼
G1 runs the partial DAG (executors → verifiers → evaluator)
                       │
                       ▼
              evaluator submits success
                       │
                       ▼
G1.Orch: graph reached success, plan_shape == 'partial'
                       │
                       ▼
            spawn child G_next (CONTINUE_AFTER_PARTIAL_PLAN)
            parent_harness_graph_id = G1
            prior_graph_id          = G1
            launch context = continuation_instructions
                           + prior segment summaries
                       │
                       ▼
G_next.Orch: init → spawn planner
                       │
            planner G_next sees:
              prior_graph_id chain already contains plan_shape='partial'
              ⇒ submit_partial_plan is GATED (soft + hard)
              must use submit_full_plan
```

`G1` stays open while `G_next` runs. The closure of `G_next` bubbles through
`G1` (which then closes itself with the same outcome) up to whoever requested
`G1`.

### §5.6 Nested `REQUEST_PLAN` (executor inside a DAG requests its own subplan)

```
G1 has [generator/executor 7] in its DAG
[generator/executor 7] ── submit_request_plan(note)
                       │
G1.Orch catches the terminal
spawns child G1.X (REQUEST_PLAN)
parent_harness_graph_id = G1
prior_graph_id          = null   ◄── chain RESET (fresh attempt)
spawn_reason            = REQUEST_PLAN
                       │
                       ▼
G1.X runs to completion (planner → DAG → evaluator)
                       │
G1.Orch delivers child_success/failure summary
back to executor 7 inside G1
                       │
executor 7 resumes inside G1's DAG
```

This is the recursive case: `REQUEST_PLAN` may happen at any depth.
Tool-gating predicates that walk the lineage chain (e.g. recursive
partial-plan check) walk `prior_graph_id` only — they do not cross the
`REQUEST_PLAN` reset boundary.

### §5.7 Closure decision tree (per local orchestrator)

```
Orchestrator_G observes a generator terminal-transition
        │
        ▼
generators quiescent? (all ∈ {DONE, FAILED, BLOCKED})
        │
   ┌────┴────┐
  no        yes
   │         │
   ▼         ▼
keep      any FAILED or BLOCKED?
running         │
        ┌───────┴───────┐
       yes             no
        │               │
        ▼               ▼
   REQUESTING_RETRY  spawn evaluator
   then retry-or-fail        │
        │                    ▼
        │              evaluator runs
        │                    │
        │              submit_evaluation_*
        │                    │
        │              ┌─────┴─────┐
        │              ▼           ▼
        │           success     failure
        │              │           │
        │              ▼           ▼
        │       plan_shape    REQUESTING_RETRY
        │       == 'partial'? then retry-or-fail
        │              │
        │      ┌───────┴───────┐
        │     yes             no
        │      │               │
        │      ▼               ▼
        │   spawn          close G success
        │   CONTINUE…      deliver to parent
        │   child
        ▼
under retry budget?
        │
   ┌────┴────┐
  yes       no
   │         │
   ▼         ▼
spawn      close G failed
RETRY…     deliver failure
child      to parent
```

***

## §6. Tool gating matrix

The matrix is unchanged in spirit; what changed is **where state lives**.
State now lives on the harness graph and its `prior_graph_id` chain, evaluated
by the local orchestrator. The reminder layer is advisory; the prehook layer
is authoritative.

| Terminal                                                                         | Block when                                                                  | State source                                                                                                       | Soft (notification)                                                                                              | Hard (prehook)                                                                                              |
| -------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `submit_partial_plan`                                                            | planner's `prior_graph_id` chain already contains `plan_shape='partial'`    | local graph + walk `prior_graph_id` upward (does **not** cross `REQUEST_PLAN` reset boundaries)                    | opening reminder injects "this is a continuation graph; only `submit_full_plan` is permitted" when chain says so | prehook walks `prior_graph_id` chain; returns block on recursive partial                                    |
| `submit_request_plan`                                                            | this generator/executor has called any tool ∈ EDIT_TOOLS ≥ 1                | agent message history (`ToolExecutionContextService.get("conversation_messages")`)                                 | inject after first edit: "edits made; `submit_request_plan` is now disabled"                                     | prehook counts EDIT_TOOLS calls; block if ≥1                                                                |
| `submit_evaluation_success`                                                      | this evaluator has ≥5 `ask_resolver` calls returning `resolved=False`       | agent message history                                                                                              | warn at 4: "4/5 resolver calls used; next outcome must be `submit_evaluation_failure`"                           | prehook counts qualifying ask_resolver calls; block if ≥5                                                   |
| `submit_verification_success`                                                    | this verifier has ≥5 `ask_resolver` calls returning `resolved=False`        | agent message history                                                                                              | warn at 4: "4/5 resolver calls used; next outcome must be `submit_verification_failure`"                         | prehook counts qualifying ask_resolver calls; block if ≥5                                                   |
| (evaluator spawn — orchestrator-internal, not a terminal)                        | any generator task in this graph is not DONE                                | local `HarnessGraph` task statuses                                                                                 | n/a — structural                                                                                                 | local orchestrator only spawns the evaluator after every generator in its graph has passed (DONE)           |
| `submit_evaluation_failure`, `submit_verification_failure`, `submit_execution_*` | never blocked for roles that own those terminals                            | —                                                                                                                  | —                                                                                                                | —                                                                                                           |

### §6.1 Gate enforcement runtime

```
agent decides → calls submit_<terminal>(input)
                     │
                     ▼
        ┌──────────────────────────────────────────┐
        │ prehook(tool_input, tool_context)         │
        │                                           │
        │   tool_context.task_center      ──┐       │
        │   tool_context.harness_graph    ──┤       │
        │   conversation_messages         ──┤       │
        │                                   ▼       │
        │            evaluate gate condition        │
        │                       │                   │
        │              ┌────────┴────────┐          │
        │              ▼                 ▼          │
        │            ALLOW            BLOCK         │
        └──────────────┬──────────────────┬────────┘
                       │                  │
                       ▼                  ▼
            run terminal handler    ToolResult(
            (local orchestrator       output=reason,
             picks up the             is_error=True)
             transition)            → agent sees error,
                                      chooses different terminal
```

Soft layer (per-turn notification rules) examples:
- first-edit-detected → "submit_request_plan disabled"
- resolver_count == 4 → "1 resolver call left; plan to fail"
- in `prior_graph_id` chain that contains `partial` → "only submit_full_plan permitted"

The two layers compose:
- **Notification** = the agent *sees* the constraint in-context, on the turn it matters.
- **Prehook** = the harness *enforces* the constraint even if the agent ignores the notification.

***

## §7. Retry mechanic — single mechanism, run by the local orchestrator

**Key insight:** retry = a `RETRY_ON_FAILURE` child harness graph spawned by
the failing graph's orchestrator before that graph closes. A generator or
evaluator failure first transitions the graph to `REQUESTING_RETRY`; the local
orchestrator then either spawns the retry child or closes itself failed when
the retry budget is exhausted.

### §7.1 Retry trigger routing

| Source terminal                                  | Wait point                                                           | Local orchestrator action                                                                       |
| ------------------------------------------------ | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| generator/executor `submit_execution_failure`    | generators quiescent (after dependent blocking + sibling completion) | call `request_retry_or_fail(G, summaries)` on the local graph                                    |
| generator/verifier `submit_verification_failure` | generators quiescent                                                 | same                                                                                            |
| evaluator `submit_evaluation_failure`            | immediate (generators already passed)                                | same                                                                                            |

### §7.2 `request_retry_or_fail` (per local orchestrator)

```
request_retry_or_fail(G, summaries):
    G.status            = REQUESTING_RETRY
    G.failure_summaries = summaries
    G.fail_count        = G.fail_count + 1

    if G.fail_count ≤ G.retry_budget:
        child = G.orchestrator.spawn_child(
            reason                  = RETRY_ON_FAILURE,
            parent_harness_graph_id = G.id,
            prior_graph_id          = G.id,
            request_plan_note       = build_continuation_note(G with retry flavor),
        )
        child.fail_count   = G.fail_count
        child.retry_budget = G.retry_budget
        # G stays open until child closes; child's outcome bubbles up

    else:
        close_harness_graph_failed(
            G,
            source_task_id = G.evaluator_task_id or last_failed_generator_task,
        )
        # G's parent orchestrator delivers child_failure to whatever requested G
```

### §7.3 `build_continuation_note`

`build_continuation_note` walks the `prior_graph_id` chain of `G` and renders
one block per ancestor:

```
build_continuation_note(graph G):
    walk prior_graph_id chain → [G_old_oldest ... G_old_newest, G]
    for each prior in chain:
        if prior was retry-trigger:
            # any generator FAILED, generator BLOCKED, or evaluator FAILED
            render as RETRY ATTEMPT block
              - prior's plan
              - per-task outcomes
              - failure summaries
        else (partial-plan success):
            render as SEGMENT block (existing behaviour)
    render CURRENT REQUEST
```

The chain stops at the first `REQUEST_PLAN` boundary — a fresh executor
request resets lineage history; only `RETRY_ON_FAILURE` and
`CONTINUE_AFTER_PARTIAL_PLAN` extend it.

### §7.4 Budget propagation across spawn reasons

| From → To spawn reason          | `fail_count`              | `retry_budget`            |
| ------------------------------- | ------------------------- | ------------------------- |
| `RETRY_ON_FAILURE`              | inherited                 | inherited                 |
| `CONTINUE_AFTER_PARTIAL_PLAN`   | inherited (not consumed)  | inherited                 |
| `REQUEST_PLAN`                  | reset to 0                | freshly configured        |

A `REQUEST_PLAN` spawn always resets the retry budget (because the lineage
chain itself resets). Partial-plan continuation does **not** consume retry
attempts; it carries the same budget forward without incrementing
`fail_count`.

***

## §8. Bubble-up and close summary

When a child graph `G_c` closes, its orchestrator hands a summary to its
parent's orchestrator:

```
ChildCloseSummary {
    harness_graph_id:  G_c.id
    spawn_reason:      REQUEST_PLAN | RETRY_ON_FAILURE | CONTINUE_AFTER_PARTIAL_PLAN
    outcome:           success | failed
    fail_count:        N
    retry_budget:      M
    plan_shape:        full | partial
    headline:          short description
    evidence:          per-task summaries / artifacts
}
```

Routing of the summary depends on `spawn_reason`:

| Spawn reason                  | Where the summary lands                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `REQUEST_PLAN`                | back to the executor task that called `submit_request_plan`; that executor resumes inside its own graph                    |
| `RETRY_ON_FAILURE`            | the parent graph adopts the child's outcome verbatim and closes itself with the same outcome (cascading bubble-up)       |
| `CONTINUE_AFTER_PARTIAL_PLAN` | same as `RETRY_ON_FAILURE` — the parent's success only finalizes once the continuation chain terminates                  |

This makes the visible result always be the **deepest leaf**: every
intermediate retry/continuation graph is transparent to the original requester.

***

## §9. Open questions / migration sequencing

1. **`G_root` retry budget** — root executor itself has no retry. Confirm that
   `G_root.retry_budget = 0` and that `REQUEST_PLAN` children of `G_root`
   carry their own freshly configured budget.
2. **Cross-graph evidence visibility** — when planner `G_next` is spawned via
   `CONTINUE_AFTER_PARTIAL_PLAN`, does it see executor edits made in `G_p`?
   The sandboxed workspace already handles this; confirm the planner
   transcript builder includes prior-segment outcomes from the
   `prior_graph_id` chain.
3. **Concurrency model for parent-while-child-runs** — the parent graph stays
   open while its retry/continuation child runs. Define whether the parent's
   evaluator task slot is reused or freshly allocated when the bubble-up
   eventually closes the parent.
4. **Bubble-up summary schema** — finalize the `ChildCloseSummary` shape
   (status, attempt counts, headline, evidence links) and confirm it survives
   multiple cascading bubble-ups without information loss.
