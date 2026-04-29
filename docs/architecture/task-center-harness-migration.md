# Task Center Harness Migration

> Demonstrative migration plan for the next harness/workflow refactor.
> Scope: TaskCenter roles, terminal tools, recovery model, retry mechanic,
> and runtime tool gating.

## §0. Changes at a glance

| Category | Surface |
|---|---|
| **Removed** | `TaskRole='advisor'`, `_create_advisor`, `create_advisor`, advisor lifecycle module, `Status.FIXING`, `Task.fix_target_id`, `Task.spawn_reason`, `create_harness_fix_executor`, `reenter_after_fix_success`, `fail_after_fix_failure` |
| **Renamed** | `submit_task_success` → `submit_execution_success` · `submit_task_failure` → `submit_execution_failure` · `request_plan` → `submit_request_plan` |
| **Added** | `TaskRole='evaluator'` · `TaskRole='generator'` · generator agent kind (`executor` \| `verifier`) · `_create_evaluator` · `HarnessGraph.evaluator_task_id` · `HarnessGraph.chain_fail_count` · `HarnessGraph.retry_budget` · `ask_resolver` helper tool · `submit_resolver_result` terminal · prehook gating layer + state-aware notification rules |
| **Reused** | `prior_graph_id` continuation chain — now also chains retry graphs |

---

## §1. Architecture (new world)

```
                                USER QUERY
                                    │
                                    ▼
                        ┌──────────────────────┐
                        │  ROOT GENERATOR      │  no harness graph
                        │  (executor agent)    │
                        │  (in TaskCenter)     │
                        └─────────┬────────────┘
                                  │  submit_request_plan
                                  ▼
   ╔════════════════════════════ HarnessGraph Gn ═══════════════════════════╗
   ║                                                                         ║
   ║       ┌─────────┐  submit_full_plan                                     ║
   ║       │ planner │ ───────────────────────────► materialize DAG          ║
   ║       └─────────┘  (or submit_partial_plan, gated)                      ║
   ║                                                                         ║
   ║       ┌──────────────────────────────────────────────────────────┐      ║
   ║       │ DAG (planner-emitted generator tasks):                   │      ║
   ║       │   generator/executor ──┐                                 │      ║
   ║       │   generator/executor ──┼─► generator/verifier            │      ║
   ║       │   generator/executor ──┘   (not a sink; verifier is      │      ║
   ║       │                             still part of generation)     │      ║
   ║       └──────────────────────────────────────────────────────────┘      ║
   ║                              │ all generator tasks passed (DONE)        ║
   ║                              ▼                                          ║
   ║       ┌─────────────────────────────────────┐                           ║
   ║       │ evaluator   (system-spawned;        │                           ║
   ║       │             sink; not in generator   │                           ║
   ║       │             DAG)                    │                           ║
   ║       └─────────────────────────────────────┘                           ║
   ║                              │ submit_evaluation_*                      ║
   ╚══════════════════════════════╪══════════════════════════════════════════╝
                                  │
                  ┌───────────────┴───────────────┐
                  ▼                               ▼
            close success                 retry continuation OR
            (root DONE)                   close failed (root FAILED)

   ─── Helpers (NOT in TaskCenter, run inline via ask_*) ───────────────────
       advisor   ── ask_advisor(...)  → {verdict, reason}        no edits
       resolver  ── ask_resolver(...) → {resolved, summaries}    can edit
```

---

## §2. Component table

| Role | Lives in TaskCenter? | Spawned by | Spawn primitive | Terminal tools | Work tools | Helpers it can call |
|---|---|---|---|---|---|---|
| **generator / executor (root)** | yes | `RunController.start` | `_create_generator(kind="executor")` | `submit_execution_success`, `submit_execution_failure`, `submit_request_plan` | DIRECT_WORK + `run_subagent` | advisor, resolver |
| **generator / executor (DAG)** | yes | planner (materialize) | `_create_generator(kind="executor")` | same | DIRECT_WORK + `run_subagent` | advisor, resolver |
| **planner** | yes | generator/executor `submit_request_plan` *or* retry continuation | `_create_planner` | `submit_full_plan`, `submit_partial_plan` | PLANNER_TOOLS (read-only + run_subagent) | advisor |
| **generator / verifier** | yes | planner (materialize as generator task) | `_create_generator(kind="verifier")` | `submit_verification_success`, `submit_verification_failure` | READ_ONLY + `run_subagent` (no edit) | advisor, resolver |
| **evaluator** | yes | orchestrator after all generator tasks have passed (DONE) | `_create_evaluator` (NEW) | `submit_evaluation_success`, `submit_evaluation_failure` | READ_ONLY + `run_subagent` (no edit) | advisor, resolver |
| **advisor** | no — inline | `ask_advisor` tool | `execute_ephemeral_agent_run` | `submit_advisor_feedback` (in-tool callback) | none | none |
| **resolver** | no — inline | `ask_resolver` tool | `execute_ephemeral_agent_run` | `submit_resolver_result(resolved, summaries)` (in-tool callback) | DIRECT_WORK + READ_ONLY | none |
| **explorer** | no — subagent | `run_subagent` (non-blocking) | subagent runtime | `submit_exploration_result` | READ_ONLY | none |

---

## §3. Data model deltas

### `Task`

| Field | State |
|---|---|
| `role: TaskRole` | enum updated: `planner`, `generator`, `evaluator`; `−advisor`; executor/verifier selected by generator agent kind |
| `agent_kind` / equivalent discriminator | **NEW** for generator tasks — `executor` or `verifier`; evaluator remains a separate role |
| `fix_target_id` | **DROPPED** |
| `spawn_reason` | **DROPPED** |
| `id`, `input`, `status`, `task_center_harness_graph_id`, `needs`, `summaries`, `created_at` | unchanged |

### `Status`

| Status | State |
|---|---|
| `PENDING`, `READY`, `RUNNING`, `HANDOFF`, `DONE`, `FAILED` | unchanged |
| `FIXING` | **DROPPED** |

### `HarnessGraph`

| Field | State |
|---|---|
| `evaluator_task_id: TaskId \| None = None` | **NEW** — system-spawned evaluator id |
| `chain_fail_count: int = 0` | **NEW** — retries used in this segment |
| `retry_budget: int = 1` | **NEW** — retries allowed before close-fail |
| `prior_graph_id` | reused — now chains both partial-plan and retry continuations |
| `plan_shape` | reused — `'full' \| 'partial'`; retries inherit prior shape |
| (other fields) | unchanged |

### `SummaryKind`

| Kind | State |
|---|---|
| `handoff`, `success`, `failure`, `dependency_blocked`, `child_success`, `child_failure`, `segment_success` | unchanged |
| `advisor_feedback` | **DROPPED** (advisor no longer a Task) |
| `retry_segment` | **NEW** — appended to root_task on retry continuation, mirrors `segment_success` |

---

## §4. Workflows

### §4.1 Happy path (full-plan)

```
[user]
   │
   ▼
[ROOT generator/executor] ──submit_request_plan(note)──► open G1; spawn planner
                                                 │
[planner G1] ──submit_full_plan(dag,details)────► materialize DAG; planner→HANDOFF
                                                 │
        ┌────────────────────────────────────────┘
        │
   ┌────┴───────────────────────────────────────┐
   ▼                                            ▼
[generator/executor 1]──submit_execution_success    [generator/executor 2]──submit_execution_success
   │                                            │
   └─────────────►[generator/verifier]◄─────────┘
                       │
                       │  may call ask_resolver(issue) inline
                       │  if 5x resolved=False → forced submit_verification_failure
                       │
                       └──submit_verification_success──► verifier DONE
                                                              │
                                                  all generator tasks passed
                                                  (DONE)
                                                              │
                                                              ▼
                                              orchestrator spawns evaluator
                                              (READY, harness_graph_id=G1,
                                               graph.evaluator_task_id=eval.id)
                                                              │
                                                  [evaluator] reads DAG summaries
                                                              │
                                                       submit_evaluation_success
                                                              │
                                                              ▼
                                              close_harness_graph_success(G1)
                                              ROOT generator/executor gets
                                              child_success summary and resumes
```

### §4.2 Generator/verifier or evaluator with inline resolver loop

```
                ┌─────────────────────────────────────────────────────┐
                │  generator/verifier (or evaluator) — running          │
                │                                                      │
                │  scans evidence / runs checks                        │
                │  finds issue                                         │
                │             │                                        │
                │             ▼                                        │
                │     ask_resolver(issues_to_resolve, ctx)   [BLOCK]   │
                │             │                                        │
                │   ┌─────────┘                                        │
                │   ▼                                                  │
                │  ┌─────────────────────────────────────────────────┐ │
                │  │ resolver — inline ephemeral run (no Task)       │ │
                │  │   - reads files                                 │ │
                │  │   - edits files (DIRECT_WORK)                   │ │
                │  │   - submit_resolver_result(resolved, summaries) │ │
                │  └─────────────────────────────────────────────────┘ │
                │             │                                        │
                │             ▼                                        │
                │  read {resolved, summaries}                          │
                │             │                                        │
                │     ┌───────┴────────┐                               │
                │     ▼                ▼                               │
                │  resolved=True    resolved=False                     │
                │     │                │                               │
                │     ▼                ▼                               │
                │  re-check;       another ask_resolver?               │
                │  decide:           (counter++)                       │
                │  submit_*_success                                    │
                │  or submit_*_failure                                 │
                │                                                      │
                │  GATE: at 5 resolver calls with resolved=False,      │
                │  submit_*_success is BLOCKED by prehook;             │
                │  agent must submit_*_failure                         │
                └─────────────────────────────────────────────────────┘
```

### §4.3 Generator failure → generators quiescent → retry continuation

```
[generator/executor 2] ──submit_execution_failure(summary)──► executor FAILED
                                                  │
                                                  ▼
                                  dependents cascade FAILED
                                  (existing dependency_blocked logic)
                                                  │
                                  remaining generator tasks keep running
                                                  │
                                                  ▼
                                  generators quiescent (all ∈ {DONE, FAILED})
                                                  │
                                                  ▼
                                  any generator FAILED?
                                                  │
                                            yes ──┘
                                                  │
                                                  ▼
                                  request_retry_or_fail(G1, failure_summaries)
                                                  │
                                  G1.chain_fail_count + 1 ≤ G1.retry_budget?
                                                  │
                              ┌───────────────────┴─────────────────────┐
                              ▼ yes                                     ▼ no
                      spawn retry continuation                close_harness_graph_failed
                      G2 = Orchestrator.spawn(                root_task FAILED,
                          root=G1.root,                        propagate up
                          prior_graph_id=G1,
                          chain_fail_count=G1.chain_fail_count+1,
                          request_plan_note=<retry note>)
                              │
                              ▼
                      planner G2 launches with context:
                        ROOT_GOAL: ...
                        PRIOR ATTEMPT (G1):
                          PLAN: <G1 dag + details>
                          OUTCOMES:
                            generator/executor 1: SUCCESS — <summary>
                            generator/executor 2: FAILURE — <summary>
                            generator/verifier: did not run (dep blocked)
                        RETRY ATTEMPT 1/1
```

### §4.4 Evaluator failure → retry (or close)

```
[evaluator] ──submit_evaluation_failure(summary)──► evaluator FAILED
                                                       │
                                                       ▼
                                       request_retry_or_fail(G1, [eval_summary])
                                                       │
                                              (same branch as §4.3)
                                                       │
                                  ┌────────────────────┴─────────────────────┐
                                  ▼ under budget                             ▼ at budget
                          spawn retry continuation                   close_harness_graph_failed
                          (planner sees all generators passed        root_task FAILED
                          but evaluator rejected → replan
                          accordingly)
```

### §4.5 Closure decision tree (single pivot point)

```
              ┌───────────────────────────────────────────┐
              │  Any generator terminal-transition fires    │
              └────────────────────┬──────────────────────┘
                                   ▼
                       are generator tasks quiescent?
                       (every generator ∈ {DONE, FAILED})
                                   │
                          ┌────────┴────────┐
                         no                yes
                          │                 │
                          ▼                 ▼
                    keep running     any generator FAILED?
                                            │
                                   ┌────────┴────────┐
                                  yes              no
                                   │                 │
                                   ▼                 ▼
                         request_retry_or_fail   spawn evaluator sink (READY)
                                                       │
                                                       ▼
                                                [evaluator runs]
                                                       │
                                              submit_evaluation_*
                                                       │
                                            ┌──────────┴──────────┐
                                            ▼                     ▼
                                         success               failure
                                            │                     │
                                            ▼                     ▼
                                  close_harness_graph_   request_retry_or_fail
                                  success
```

---

## §5. Tool gating matrix

| Terminal | Block when | State source | Soft (notification) | Hard (prehook) |
|---|---|---|---|---|
| `submit_partial_plan` | planner's graph chain has any ancestor with `plan_shape='partial'` | TaskCenter graph (`ctx.task_center.graph.get_harness_graph(...)` + walk `prior_graph_id`) | opening reminder injects "this is a continuation graph; only `submit_full_plan` is permitted" when chain says so | prehook walks chain; returns block on recursive partial |
| `submit_request_plan` | this generator/executor has called any tool ∈ EDIT_TOOLS ≥ 1 | agent message history (`ToolExecutionContextService.get("conversation_messages")`) | inject after first edit: "edits made; `submit_request_plan` is now disabled" | prehook counts EDIT_TOOLS calls; block if ≥1 |
| `submit_evaluation_success` | this evaluator has ≥5 `ask_resolver` calls returning `resolved=False` | agent message history | warn at 4: "4/5 resolver calls used; next outcome must be `submit_evaluation_failure`" | prehook counts qualifying ask_resolver calls; block if ≥5 |
| `submit_verification_success` | same shape as evaluator | agent message history | same | same |
| (evaluator spawn) | any generator task FAILED | TaskCenter graph | n/a — structural | not a terminal; orchestrator spawns the evaluator only after all generator tasks have passed (DONE) |
| `submit_evaluation_failure`, `submit_verification_failure`, `submit_execution_*` | never blocked | — | — | — |

### Gate enforcement runtime

```
   agent decides → calls submit_<terminal>(input)
                            │
                            ▼
              ┌─────────────────────────────────────────┐
              │ prehook(tool_input, tool_context)       │
              │                                         │
              │   tool_context.task_center  ──┐         │
              │   conversation_messages     ──┤         │
              │                               ▼         │
              │            evaluate gate condition       │
              │                       │                  │
              │              ┌────────┴────────┐         │
              │              ▼                 ▼         │
              │           ALLOW              BLOCK       │
              └──────────────┬─────────────────┬────────┘
                             │                 │
                             ▼                 ▼
                  run terminal handler   tool returns ToolResult(
                                            output=reason,
                                            is_error=True)
                                          → agent sees error,
                                          chooses different terminal


   Soft layer (notification rules, fired each turn):
        if predicate(messages, query_context) → inject <system-reminder>
   Examples:
        - first-edit-detected → "submit_request_plan disabled"
        - resolver_count == 4  → "1 resolver call left; plan to fail"
        - in continuation chain → "only submit_full_plan permitted"
```

The two layers compose:
- **Notification** = the agent *sees* the constraint in-context, on the turn it matters.
- **Prehook** = the harness *enforces* the constraint even if the agent ignores the notification.

---

## §6. Retry mechanic — single mechanism

**Key insight:** retry = continuation graph with a failure-flavored launch context. Same `prior_graph_id` chain, two branches in `build_continuation_note`:

```
build_continuation_note(graph G):
    walk prior_graph_id chain → [G_old_oldest ... G_old_newest, G]
    for each prior in chain:
        if prior was retry-trigger (any generator FAILED OR evaluator failed):
            render as RETRY ATTEMPT block
              - prior's plan
              - per-task outcomes
              - failure summaries
        else (partial-plan success):
            render as SEGMENT block (existing behavior)
    render CURRENT REQUEST
```

`request_retry_or_fail(G, summaries)` is the only new closure helper:

```
request_retry_or_fail(G, summaries):
    if G.chain_fail_count + 1 ≤ G.retry_budget:
        Orchestrator.spawn(
            tc,
            root_task_id=G.root_task_id,
            request_plan_note=build_continuation_note(G with retry flavor),
            prior_graph_id=G.id,
        )
        new_graph.chain_fail_count = G.chain_fail_count + 1
        new_graph.retry_budget = G.retry_budget
    else:
        close_harness_graph_failed(G, source_task_id=G.evaluator_task_id or last_failed_generator_task)
```

Failure-trigger routing table:

| Source terminal | Wait point | Calls |
|---|---|---|
| generator/executor `submit_execution_failure` | generators quiescent (after dep_blocked + sibling completion) | `request_retry_or_fail(G, summaries)` |
| generator/verifier `submit_verification_failure` | generators quiescent | same |
| evaluator `submit_evaluation_failure` | immediate (generators already passed) | same |

---

## §7. Migration ordering

| # | Stage | What ships |
|---|---|---|
| 0 | Expose transcript to prehooks | inject `conversation_messages` into `ToolExecutionContextService` metadata |
| 1 | Drop advisor as TaskCenter task | `ask_advisor` runs advisor inline via `execute_ephemeral_agent_run` + callback buffer |
| 2 | Strip Stage-6 fix-executor | remove `Status.FIXING`, `fix_target_id`, `spawn_reason`, `create_harness_fix_executor`, reenter/fail helpers |
| 3 | Add resolver helper | `harness_agents/resolver/` + `ask_resolver` tool + `submit_resolver_result` |
| 4 | Rename terminals | `submit_task_*` → `submit_execution_*` · `request_plan` → `submit_request_plan` |
| 5 | Collapse executor/verifier into generator role | `TaskRole='generator'` plus generator agent kind for executor/verifier creation and prompts |
| 6 | Add evaluator role | new role, `_create_evaluator`, `evaluator_task_id`, evaluator agent files, spawn-in-closure wiring |
| 7 | Retry continuation | `chain_fail_count`, `retry_budget`, `request_retry_or_fail`, retry branch in `build_continuation_note` |
| 8 | Tool gating layer | prehook framework + 4 prehooks + state-aware notification rules |
| 9 | Persistence schema sweep | DB columns: `+evaluator_task_id`, `+chain_fail_count`, `+retry_budget`, generator kind discriminator, `−fix_target_id`, `−spawn_reason` |

---

## §8. Test impact

### Already-touched on this branch (per `git status`)

- `test_center.py` — drop advisor cases, drop fix-executor cases, add evaluator + retry cases
- `test_evaluator_rename.py` (deleted) — replace with new evaluator role tests
- `test_submission_tools.py` — terminal renames
- `test_partial_chain.py` — extend with retry continuation cases
- `test_persistence_stage8_fields.py` — schema additions
- `test_runtime_primitives.py` — new `_create_evaluator`, generator executor/verifier creation, dropped `_create_advisor`
- `test_materialization.py` — DAG validator unchanged; generator-kind assertions + evaluator-spawn assertion added downstream
- `test_harness_agent_prompts.py` — evaluator + resolver prompt assertions
- `test_verifier_closure.py` (untracked) — adapt to no-fix-executor world

### New test files

- `test_evaluator_lifecycle.py`
- `test_retry_continuation.py`
- `test_resolver_helper.py`
- `test_tool_gates.py` (prehook + notification combos)

---

## §9. Open / verify

1. Stage 0 — confirmed/fixed: tool prehooks can read the live transcript through `ToolExecutionContextService.get("conversation_messages")`. `run_query(context, messages)` still owns the transcript and notification rules still receive `(messages, context)` directly; tool execution now injects `conversation_messages` into execution metadata before hook dispatch.
2. `ask_resolver` input shape — propose `(issues_to_resolve: str, calling_agent_context: str)` mirroring advisor.
3. `submit_resolver_result.summaries` — `list[str]` or richer struct?
4. Retry-graph launch context — beyond plan + outcomes + failure summaries, do you also want successful executor outputs surfaced so the new planner can avoid re-doing solved work?
