# Four-Role + Recursive-Orchestrator Implementation Roadmap

**Status:** Roadmap
**Combines:** `four-role-advisor-gated-design.md` §14 + `recursive-orchestrator-design.md` §13
**Goal:** Single ordered plan landing the four-role design (planner, executor, verifier, evaluator) with advisor-gating and the recursive-orchestrator pattern.

Each stage is independently shippable and leaves the system in a
consistent state. Stages are ordered for minimum disruption per
landing.

---

## Critical Path

```
Stage 0 ✓ (verifier package)
   │
   ▼
Stage 1 (runtime restructure — foundation)
   ├─► Stage 2 (verifier lifecycle, degraded)
   │       └─► Stage 6 (fix-executor) ──────┐
   ├─► Stage 3 (plan terminal split) ───────┤
   │       ├─► Stage 4 (advisor + pre-hooks)┤
   │       └─► Stage 5 (partial chain) ─────┤
   │               depends on 3 + 4         │
   └────────────────────► Stage 7 (evaluator narrows + legacy cleanup)
                                  │
                                  ▼
                          Stage 8 (persistence + telemetry)
                                  │
                                  ▼
                          Phase 2 (caps, strict mode, etc.)
```

**Parallelizable after Stage 1:** stages 2, 3, and 4 are independent
and can split across PRs / contributors. Stage 5 funnels them back
together.

**Riskiest stage:** Stage 1 — touches every lifecycle module and every
test that constructs `Task` or `HarnessGraph`. Land it as a single PR
so the codebase only ever has one shape of task creation at a time.

---

## Stage 0 — Verifier package + TaskRole extension *(DONE)*

Already landed. Verifier directory present at
`backend/src/task_center/harness_agents/verifier/`. `TaskRole` literal
includes `"verifier"`. No code wires it yet — pure surface area.

**Files (already present):**
- `harness_agents/verifier/__init__.py`
- `harness_agents/verifier/agent.md`
- `harness_agents/verifier/distilled_rules.md`
- `harness_agents/verifier/context.py`
- `harness_agents/verifier/definition.py`

---

## Stage 1 — Runtime restructure: Orchestrator + RunController + TaskCenter split

**Largest single change. Pure refactor — no behavior change.**

### Scope

- Rename `runtime/orchestrator.py` → `runtime/task_center.py` (still
  houses `TaskCenter`).
- New `runtime/orchestrator.py` houses the `Orchestrator` class
  (constructors only — `Orchestrator.spawn`, view constructor,
  accessors; mutating methods stubbed or simplest-possible).
- New `runtime/run_controller.py` houses `RunController`.
- Add `_create_<role>` primitives to `TaskCenter`:
  `_create_executor`, `_create_planner`, `_create_verifier`,
  `_create_evaluator`, `_create_advisor` (last two as stubs).
- Add `_open_graph` primitive to `TaskCenter`.
- Move every `Task(...)` and `HarnessGraph(...)` constructor call out
  of `harness_agents/<role>/lifecycle.py` into the new primitives or
  composers.
- Lifecycle modules become thin runtime-dispatcher routers.
- `HarnessGraph` data model: add `planner`, `dag_nodes`, `evaluator`
  slots; preserve existing fields.
- New type alias: `GeneratorRole = Literal["executor", "verifier"]`
  in `model/role.py`.
- `Orchestrator.spawn` wires up new graph + planner via existing
  `submit_plan_handoff` flow (the legacy terminal still works at
  this stage).

### Ship state

Existing tests pass without modification. Add unit tests for each
`_create_<role>` primitive that exercises construction directly with
synthetic `TaskCenter` state.

### Depends on

Stage 0.

### Files touched

- `backend/src/task_center/runtime/task_center.py` (renamed)
- `backend/src/task_center/runtime/orchestrator.py` (new)
- `backend/src/task_center/runtime/run_controller.py` (new)
- `backend/src/task_center/runtime/spawn.py` (unchanged — keeps narrow `SpawnFunc` role)
- `backend/src/task_center/model/role.py` (new — GeneratorRole alias)
- `backend/src/task_center/model/harness.py` (HarnessGraph slots)
- `backend/src/task_center/harness_agents/{executor,planner,evaluator}/lifecycle.py` (lose constructors, gain router calls)

---

## Stage 2 — Verifier lifecycle (degraded — no fix-executor yet)

### Scope

- New `harness_agents/verifier/lifecycle.py`: routers for
  `submit_verification_success` and `submit_verification_failure`.
- `Status.FIXING` added to enum (in-memory only; DB migration in
  Stage 8).
- Success path: mark verifier DONE, refresh dependents.
- Failure path *(temporary)*: mark verifier FAILED, cascade-fail
  dependents. **No fix-executor yet** — full recovery lands in Stage 6.
- Verifier nodes can now appear in DAGs (planner agent.md unchanged
  for now — still emits role-less nodes that default to executor).
- Runtime dispatcher routes verifier terminals to `Orchestrator`
  methods (failure stub returns to cascade-fail until Stage 6).

### Ship state

Verifier failures fail the graph (degraded but consistent).
Verifier successes work normally. Verifier as DAG sink not yet
forbidden — that constraint enforces in Stage 3.

### Depends on

Stage 1.

### Files touched

- `backend/src/task_center/harness_agents/verifier/lifecycle.py` (new)
- `backend/src/task_center/model/task.py` (`Status.FIXING`)
- `backend/src/task_center/runtime/orchestrator.py` (verifier-failure router stub)

---

## Stage 3 — Plan terminal split + materialization validation

### Scope

- New planner terminals: `submit_full_plan`, `submit_partial_plan`.
- DAG entry shape gains `role` field (`GeneratorRole`).
- `HarnessGraph`: add `plan_shape`, `what_to_do_next` fields.
- `Orchestrator.materialize_full_plan` and
  `Orchestrator.materialize_partial_plan` with full validation
  per `recursive-orchestrator-design.md` §9.3, including the
  `verifier_sink` rule.
- `MaterializationFailure` result type.
- Tool-result failure path on validation failure (lenient
  advisor-accept — Phase 1).
- Legacy `submit_plan_handoff` aliased to `materialize_full_plan` for
  transition.
- Planner agent.md updated to emit `role` field per DAG entry.
- DAG validation in `graph/dag.py` extended for new role field.

### Ship state

Planners can emit `submit_partial_plan`, **but partial plans behave
like full plans at closure** (continuation chain not wired until
Stage 5). Document this clearly so prompts don't rely on partial
behavior yet.

### Depends on

Stage 1.

### Files touched

- `backend/src/task_center/harness_agents/planner/{lifecycle.py,agent.md,definition.py}`
- `backend/src/task_center/runtime/orchestrator.py` (`materialize_*` methods)
- `backend/src/task_center/model/harness.py` (`plan_shape`, `what_to_do_next`)
- `backend/src/task_center/graph/dag.py` (validation)

---

## Stage 4 — Advisor + `ask_advisor` + pre-hooks

### Scope

- New `harness_agents/advisor/{__init__.py, agent.md,
  distilled_rules.md, context.py, definition.py, lifecycle.py}`.
- `ask_advisor` tool implementation under `tools/ask_advisor.py`.
- `runtime/pre_hooks.py` — pre-hook layer enforcing advisor-accept
  matching per `four-role-advisor-gated-design.md` §3.2.
- `AdvisorLaunchContext`.
- `TaskCenter.create_advisor` + `_create_advisor` primitive (stub
  upgraded to real impl).
- **First cut:** gate one terminal (`submit_full_plan`) end-to-end
  to prove the pre-hook + advisor-spawn flow.
- **Then:** expand to all gated terminals listed in
  `four-role-advisor-gated-design.md` §3.3.

### Ship state

Advisor gating live for all high-stakes terminals. Lenient mode:
failed materialization keeps the accept token (Phase 1 behavior —
strict consumption deferred to Phase 2).

### Depends on

Stages 1 and 3 (gates need the new terminal surface).

### Files touched

- `backend/src/task_center/harness_agents/advisor/` (new directory)
- `backend/src/task_center/tools/ask_advisor.py` (new)
- `backend/src/task_center/runtime/pre_hooks.py` (new)
- `backend/src/task_center/runtime/orchestrator.py` (advisor-gate enforcement)

---

## Stage 5 — Partial-plan chain (continuation)

### Scope

- `Orchestrator.close_partial_success` implementation.
- `Orchestrator.build_continuation_note` (graph-local helper; walks
  prior chain via `prior_graph_id` back-link).
- `HarnessGraph.prior_graph_id` field.
- Evaluator success branches on `graph.plan_shape` in the runtime
  dispatcher.
- Continuation graphs spawn via
  `Orchestrator.spawn(..., prior_graph_id=...)`.

### Ship state

Partial chains live. A planner emitting `submit_partial_plan` now
produces a real continuation chain that terminates when some graph
in the chain closes full.

### Depends on

Stages 1, 3, 4.

### Files touched

- `backend/src/task_center/runtime/orchestrator.py` (`close_partial_success`, `build_continuation_note`)
- `backend/src/task_center/harness_agents/evaluator/lifecycle.py` (branch on `plan_shape`)
- `backend/src/task_center/model/harness.py` (`prior_graph_id`)

---

## Stage 6 — Fix-executor (verifier recovery)

### Scope

- `Orchestrator.create_harness_fix_executor`: deterministic input
  synthesis from verifier's failure summary + verifier input + dep
  summaries.
- `spawn_reason='fix_verification'` tag on the fix-executor task.
- Fix-executor prompt fragment via system reminder.
- Tool-surface restriction (no `request_plan` terminal for
  fix-executors).
- Verifier re-runs on fix-executor success (F2 wiring per
  `four-role-advisor-gated-design.md` §6).
- Replace Stage 2's "verifier failure → FAILED" with
  "verifier failure → FIXING → fix-executor".
- `Task.fix_target_id` back-pointer.

### Ship state

Verifier failures recoverable end-to-end. Bounded recovery: if the
fix-executor itself fails, the verifier's chain fails as before.

### Depends on

Stages 1, 2.

### Files touched

- `backend/src/task_center/runtime/orchestrator.py` (`create_harness_fix_executor`)
- `backend/src/task_center/harness_agents/executor/agent.md` (fix-mode system reminder)
- `backend/src/task_center/harness_agents/verifier/lifecycle.py` (re-run on fix-target success)
- `backend/src/task_center/model/task.py` (`fix_target_id`, `spawn_reason`)

---

## Stage 7 — Evaluator narrows + legacy cleanup

### Scope

- Remove REPLAN_AFTER prose and partial-plan branching from
  `harness_agents/evaluator/agent.md`.
- Rename `submit_task_success` → `submit_evaluation_success` for the
  evaluator role.
- Drop legacy `submit_plan_handoff`; planner now emits only
  `submit_full_plan` / `submit_partial_plan`.
- Clean up evaluator's `definition.py` and `distilled_rules.md`.

### Ship state

Evaluator scope narrowed to a single decision: "was the planning
unit's goal met?" No plan-shape reasoning anywhere in agent prompts.

### Depends on

Stages 3, 5 (new terminals must be fully wired).

### Files touched

- `backend/src/task_center/harness_agents/evaluator/{agent.md,distilled_rules.md,definition.py,lifecycle.py}`
- `backend/src/task_center/runtime/orchestrator.py` (rename terminal dispatcher)
- `backend/src/task_center/harness_agents/planner/lifecycle.py` (drop legacy alias)

---

## Stage 8 — Persistence migrations + telemetry

### Scope

- DB schema migrations in `db/stores/task_center_store.py`:
  - `HarnessGraph`: `planner`, `dag_nodes`, `evaluator`,
    `plan_shape`, `what_to_do_next`, `prior_graph_id`.
  - `Status.FIXING` enum value.
  - `TaskRole` literal extension (`verifier`, `advisor`).
  - `Task.fix_target_id`, `Task.spawn_reason`.
- Migration scripts under `migrations/`.
- Telemetry updates: trace orchestrator method calls, advisor
  consultations, materialization failures, partial-chain depth.

### Ship state

Schema matches code; runs survive restart and replay correctly.

### Depends on

Stages 1–6 (every schema field has a code consumer; no dead columns).

### Files touched

- `backend/src/db/stores/task_center_store.py`
- `backend/migrations/` (new migration files)
- `backend/src/task_center/runtime/telemetry.py` (or wherever traces are emitted)

---

## Phase 2 (deferred — separate roadmap)

These items are tracked but explicitly out of scope for Phase 1.
Each requires its own design pass.

1. **Recursion depth caps.** Bound `request_plan` recursion (Case A)
   and partial-chain depth (Case B).
2. **Structural depth control.** Block `submit_partial_plan` when
   parent graph's `plan_shape == "partial"`. Forces partial chains
   to exist at one level only.
3. **Strict advisor-accept consumption.** Failed materialization
   burns the accept; agent must re-consult before retrying.
4. **Pre-hook structural validation.** Move cycle and verifier-sink
   checks ahead of the advisor so the advisor only sees
   structurally-valid plans.
5. **Continuation-note compaction.** Summary-aware truncation for
   long partial chains.
6. **Context fidelity standard.** Per-role artifact capture
   (commands + exit codes, paths + content hashes, diff hashes,
   probe results) for advisor effectiveness.

---

## Cross-Stage Notes

### Test strategy

- **Stage 1:** unit tests per `_create_<role>` primitive; existing
  integration tests should pass unchanged.
- **Stage 2:** verifier success/failure tests (failure asserts
  cascade-fail behavior — temporary).
- **Stage 3:** materialization validation matrix (one test per
  `MaterializationFailure.code` value).
- **Stage 4:** advisor pre-hook tests (accept, reject, payload-drift,
  intervening-tool-call).
- **Stage 5:** partial-chain tests (multi-segment chains, terminate
  on full closure).
- **Stage 6:** fix-executor tests (success → verifier re-run;
  failure → cascade-fail).
- **Stage 7:** evaluator-prompt regression tests (no plan-shape
  reasoning leakage).
- **Stage 8:** schema migration tests (round-trip persistence).

### Rollback strategy

Each stage is independently reversible. If Stage 5 (partial chain)
ships broken, revert it; partial plans degrade to full-plan-shape
closure (Stage 3 behavior). Stage 1 is the only stage that's hard
to roll back — its scope makes a partial revert messy. Land it as a
single PR with full review.

### Owner / contributor split

Stages 2, 3, and 4 can be parallelized after Stage 1 lands. Suggested
split if multiple contributors:

- **Contributor A:** Stage 2 → Stage 6 (verifier track).
- **Contributor B:** Stage 3 → Stage 5 → Stage 7 (planner / closure track).
- **Contributor C:** Stage 4 (advisor track) → Stage 8 (persistence).

Sync points: end of Stage 4 (everyone needs advisor gating live) and
end of Stage 5 (continuation-chain affects everyone's tests).

---

## References

- `four-role-advisor-gated-design.md` — design spec for the four-role
  pattern + advisor gating.
- `recursive-orchestrator-design.md` — design spec for the
  Orchestrator class + RunController + recursive graph spawning.
- `gan-task-graph-recovery-cap.md` — verifier/fix-executor recovery
  semantics.
- `runtime-behavioral-shaping.md` — notification rules used at
  runtime (referenced by Phase 2 depth-cap work).
