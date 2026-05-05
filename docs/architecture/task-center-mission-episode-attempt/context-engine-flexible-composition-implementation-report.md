# Context Engine — Flexible Composition: Implementation Report

Companion to
[`context-engine-flexible-composition.md`](../../../.omc/plans/context-engine-flexible-composition.md)
(plan v8) and the corresponding amendments to
[`phase-06-context-engine.md`](./phase-06-context-engine.md).
This report records the structural changes, file inventory, and
verification outcome for the flexible-composition implementation. The
prior "deferred items" bucket has been emptied: US-011b (helper-tool
rewiring) landed in `d6665799` and US-017b (entry-graph carve-out)
landed in this PR; see §6 for the full coverage map.

---

## 1. Verdict

**Verdict: ships as a structural delivery on top of Phase 06.**

Plan v8 introduces three composition seams behind a single
`ContextComposer.compose(base_agent_name, scope)` method. Every
TaskCenter-owned spawn (planner, generator, evaluator, entry executor,
advisor, resolver) now funnels through that path. The first variant consumer
is the planner full vs full-only fork driven by the partial-plan ancestor
gate; the legacy `PartialPlanAncestorGate` prehook + `recursive_partial_plan`
notification trigger have been removed in favour of an `agent.md`
`terminals:` filter on the `planner_full_only` variant.

US-011b (helper tool rewiring) landed in `d6665799`: `ask_advisor` and
`ask_resolver` now compose `advisor_v1` / `resolver_v1` packets, persist them,
and pass the rendered prompt + selected agent definition into
`run_ephemeral_agent`. The composer-less helper path is gone.

US-017b (entry-graph carve-out) **landed in this PR**: the synthetic
one-node `Attempt` that wrapped the entry executor is removed.
`EntryTaskController` is the new lifecycle owner for graph-less entry
tasks (terminal submissions, run exhaustion, delegated close-report
resume), attached to `AttemptRuntime.entry_task_controller` peer to
`composer`. Submission tools resolve through a unified
`ExecutorSubmissionContext` that branches on graph-mode vs entry-mode.
`EntryAttemptBuilder` is deleted; the entry segment now contains
zero `Attempt` rows. The TaskCenter suite is green at 220 tests; the
broader backend sweep is green at 968 tests. Ruff is clean across the
touched `task_center`, `agents`, `db`, server, and `tools/submission`
slices.

The composer-None production fallbacks (`orchestrator._build_planner_launch`,
`dispatcher._build_generator_launch`, `dispatcher._build_evaluator_launch`,
`entry._build_entry_launch`) and `runtime.task_input_for_graph` were also
deleted as part of this PR — the previous report claim that c6296d59 had
removed them was incorrect (only the `HarnessAgentLaunch` back-compat alias
went in that commit). Production paths now require a composer; runtimes
without one raise a clean `GraphInvariantViolation` from
`AttemptRuntime.require_composer()` at first launch.

---

## 2. File inventory

### New context engine package

| File | Lines | Responsibility |
| --- | ---: | --- |
| `backend/src/task_center/context_engine/__init__.py` | 42 | Public surface re-exports |
| `backend/src/task_center/context_engine/errors.py` | 25 | `ContextEngineError`, `RecipeScopeError`, `MissingContextRecipeError`, `AgentDefinitionValidationError` |
| `backend/src/task_center/context_engine/packet.py` | 98 | `ContextPacket`, `ContextBlock`, `ContextRefs`, `ContextPriority`, `ContextBlockKind` |
| `backend/src/task_center/context_engine/scope.py` | 38 | `ContextScope` frozen dataclass + `assert_fields` |
| `backend/src/task_center/context_engine/recipes_registry.py` | 62 | `ContextRecipe`, `RecipeRegistry` |
| `backend/src/task_center/context_engine/engine.py` | 67 | `ContextEngine.build(recipe_id, scope)`, `ContextEngineDeps` |
| `backend/src/task_center/context_engine/renderer.py` | 238 | `MarkdownPromptRenderer`, `HeadingTemplate`, token-budget compression |
| `backend/src/task_center/context_engine/predicates.py` | 90 | `PredicateRegistry`, `ResolverContext`, `register_builtin_predicates()` |
| `backend/src/task_center/context_engine/resolver.py` | 132 | `RuleBasedAgentResolver`, `AgentSelection` |
| `backend/src/task_center/context_engine/composer.py` | 98 | `ContextComposer`, `LaunchBundle` |

### New recipe modules

| File | Lines | Recipe |
| --- | ---: | --- |
| `backend/src/task_center/context_engine/recipes/__init__.py` | 58 | `register_builtin_recipes()` (idempotent) |
| `backend/src/task_center/context_engine/recipes/planner.py` | 239 | `planner_v1` — seg-1 / seg-N branches, multi-prior priority split, `MAX_FAILED_GRAPHS_RENDERED=6` cap |
| `backend/src/task_center/context_engine/recipes/generator.py` | 126 | `generator_v1` — planned task spec + spec framing + dependency summaries |
| `backend/src/task_center/context_engine/recipes/evaluator.py` | 104 | `evaluator_v1` — task spec + criteria + completed-task summaries |
| `backend/src/task_center/context_engine/recipes/entry_executor.py` | 61 | `entry_executor_v1` — single `entry_request` block |
| `backend/src/task_center/context_engine/recipes/helper.py` | 143 | `advisor_v1` + `resolver_v1` — parent-packet inheritance with priority demotion (`required → high → medium → low → low`); first block is the parent's task as `parent_question`, rest are inherited blocks tagged `metadata['inherited_from_parent']='true'` |

### New helper-tool composer plumbing

| File | Lines | Purpose |
| --- | ---: | --- |
| `backend/src/tools/submission/helper_agent/_compose.py` | 90 | `compose_helper_bundle(helper_role, base_agent_name, context)` — shared parent-task lookup + scope construction + `composer.compose(...)` call for `ask_advisor` / `ask_resolver` |

### New ancestry helper

| File | Lines | Purpose |
| --- | ---: | --- |
| `backend/src/task_center/mission/ancestry.py` | 81 | Canonical `has_partial_planned_caller_ancestor(*, request_id, …stores) -> bool` walker |

### New entry-mode controller (US-017b)

| File | Lines | Purpose |
| --- | ---: | --- |
| `backend/src/task_center/entry_task_controller.py` | 240 | `EntryTaskController` — graph-less entry executor lifecycle owner. Receives `apply_executor_success`, `apply_executor_failure`, `apply_run_exhausted`, `apply_mission_close_report`, `mark_waiting_mission`, `restore_running_after_failed_mission_start`. Closes entry segment + complex_request via the shared request handler so the existing `deliver_close_report` callback path finishes the run. |

### New persistence

| File | Lines | Purpose |
| --- | ---: | --- |
| `backend/src/db/models/context_packet.py` | 38 | `ContextPacketRecord` table |
| `backend/src/db/stores/context_packet_store.py` | 54 | Write-once `ContextPacketStore.insert(packet) -> id` / `get(id)` |
| `backend/src/db/models/task_center.py` | delta | `TaskCenterTaskRecord.context_packet_id` stores the packet id for composed launches |

### New agent definitions

| File | Lines | Agent |
| --- | ---: | --- |
| `backend/src/agents/main_agent/planner/agent_full_only.md` | 89 | Full-plan-only planner variant target (terminals filter is the gate) |
| `backend/src/agents/main_agent/entry_executor/agent.md` | 39 | Top-level entry executor profile (replaces the implicit `executor` use at top level) |

### New tests

| File | Lines | Coverage |
| --- | ---: | --- |
| `backend/tests/task_center/domain/test_ancestry.py` | 280 | Walker correctness (no caller / full / partial / mixed) + structural enforcement (resolver predicate + ResolverContext both call into canonical) |
| `backend/tests/task_center/context_engine/test_packet.py` | 88 | `ContextPacket` / `ContextBlock` validation |
| `backend/tests/task_center/context_engine/test_scope.py` | 53 | `ContextScope.assert_fields` accept/reject |
| `backend/tests/task_center/context_engine/test_engine.py` | 110 | Engine routes by recipe id; unknown id raises |
| `backend/tests/task_center/context_engine/test_renderer.py` | 116 | Priority order, never-compress required, deterministic output |
| `backend/tests/task_center/context_engine/test_recipes_planner.py` | 220 | seg-1 / seg-2 / seg-3 multi-prior + retry-cap + missing-prior-spec error |
| `backend/tests/task_center/context_engine/test_recipes_other.py` | 165 | generator, evaluator, entry_executor recipes + idempotent register |
| `backend/tests/task_center/context_engine/test_resolver.py` | 175 | Variant resolution, declared-order priority, nested target rejected, no-fail-open |
| `backend/tests/task_center/context_engine/test_composer.py` | 165 | Single-method orchestration + persistence + required-block append |
| `backend/tests/task_center/context_engine/test_token_budget.py` | 130 | Required survives byte-for-byte; low truncates before medium |
| `backend/tests/task_center/persistence/test_context_packet_store.py` | 80 | Round-trip insert/get; immutability; missing id returns None |
| `backend/tests/task_center/persistence/test_close_succeeded.py` | 110 | `close_succeeded` atomicity + evaluator pass-summary helper |
| `backend/tests/task_center/lifecycle/test_orchestrator_composer.py` | 235 | Composer-wired orchestrator: base when no ancestor; full-only when caller partial-planned |
| `backend/tests/task_center/lifecycle/test_planner_capability_fork.py` | 189 | E2E: child planner spawned under partial-plan caller is `planner_full_only` |
| `backend/tests/test_agents/test_definition_variants.py` | 60 | `AgentVariant` / `AgentSelectionBlock` round-trip |
| `backend/tests/test_agents/test_registry_validation.py` | 130 | `validate_agent_definitions_resolved` rejects every wiring mistake |
| `backend/tests/test_agents/test_planner_full_only_md.py` | 65 | Drift checks + `submit_partial_plan` absence + shared `planner_v1` recipe |
| `backend/tests/task_center/context_engine/test_helper_recipes.py` | 220 | Demotion table + `parent_question` shape + missing-packet-store / missing-parent-task / missing-parent-packet error paths |
| `backend/tests/test_tools/test_submission_helper_tools.py` (delta) | +120 | `ask_advisor` and `ask_resolver` end-to-end through composer: parent context inherited, "# Parent context" + "# Advisor request" / "# Resolver request" sections present, missing-composer is a hard error |
| `backend/tests/task_center/lifecycle/test_entry_task_controller.py` | 320 | US-017b: terminal success / failure / run exhaustion close entry task + segment + complex_request + finish run; mark-waiting + close-report (success/failed) + idempotency + restore-running rollback |
| `backend/tests/task_center/lifecycle/test_task_center_entry.py` (delta) | +90 | Two tests: `test_entry_executor_runs_in_graph_less_mode` pins `task_center_attempt_id is None` + run exhaustion via controller; `test_entry_segment_has_zero_attempt_rows` is the regression pin (`graph_store.list_for_episode(entry_segment_id) == []`) |

### Edited modules

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/agents/types.py` | edited | `AgentVariant`, `AgentSelectionBlock`, `AgentDefinition.{variants, context_recipe}` |
| `backend/src/agents/registry.py` | edited | `validate_agent_definitions_resolved()` |
| `backend/src/agents/main_agent/{planner,evaluator,generator/executor,generator/verifier}/agent.md` | edited | `context_recipe:` declared; planner gains `variants:` referencing `planner_full_only` |
| `backend/src/db/models/{__init__,task_center,episode}.py` | edited | `ContextPacketRecord` exported; task row `context_packet_id`; `Episode` denormalized fields |
| `backend/src/db/stores/{__init__,task_center_store,episode_store}.py` | edited | `ContextPacketStore` exported; task context-packet id round-trip; `close_succeeded`; `get_evaluator_pass_summary` helper |
| `backend/src/task_center/segment/{segment,manager}.py` | edited | DTO fields; manager calls `close_succeeded` on success path |
| `backend/src/task_center/mission/handler.py` | edited | Optional `task_store` thread-through; `close_mission.final_attempt_id` widened to `str \| None` |
| `backend/src/task_center/mission/handoff.py` | edited | `ComplexTaskHandoffCoordinator.start(parent_attempt_id: str \| None)`; `_assert_parent_running_and_no_open_child` accepts entry-mode (no parent graph); `ComplexTaskHandoffResult.parent_attempt_id: str \| None` |
| `backend/src/task_center/mission/close_report_delivery.py` | edited | Routes graph-less parents through `runtime.entry_task_controller.apply_mission_close_report` |
| `backend/src/task_center/mission/request.py` | edited | `MissionCloseReport.final_attempt_id` widened to `str \| None`; `to_final_outcome()` returns `dict[str, str \| None]` |
| `backend/src/task_center/attempt/{runtime,launcher,orchestrator,dispatcher}.py` | edited | `AgentLaunch` nullable `attempt_id`; composer is required (composer-None fallbacks deleted); `runtime.require_composer()` is the new clean error path; runtime gains `entry_task_controller: EntryTaskController \| None`; launcher exhaustion routes graph-less launches through the controller |
| `backend/src/task_center/entry.py` | edited | Rewrites coordinator: no synthetic graph; constructs `EntryTaskController` from run/task/segment/request ids; runtime built with `composer` + `entry_task_controller`; `AgentLaunch` carries `attempt_id=None`; startup-failure compensation drives controller's `apply_run_exhausted`. `TaskCenterEntryHandle` drops the `attempt_id` field. |
| `backend/src/tools/submission/context.py` | edited | New `ExecutorSubmissionContext` + `resolve_executor_submission_context` — unified resolver branching graph-mode vs entry-mode; exposes `submit_executor_success` / `submit_executor_failure` / `start_mission_handoff` |
| `backend/src/tools/submission/main_agent/generator/{request_mission_solution,executor/submit_execution_success,executor/submit_execution_failure}.py` | edited | Resolve through `ExecutorSubmissionContext` and call its operations; graph-only path retired |
| `backend/src/tools/submission/hooks/__init__.py` | edited | `PartialPlanAncestorGate` export removed |
| `backend/src/tools/submission/notification_triggers/__init__.py` | edited | `recursive_partial_plan` factory removed |
| `backend/src/tools/submission/main_agent/planner/submit_partial_plan.py` | edited | Prehook registration removed |
| `backend/src/agents/main_agent/planner/agent.md` | edited | `notification_triggers: []`; updated prose to reference the new gate |
| `docs/architecture/task-center-mission-episode-attempt/phase-06-context-engine.md` | edited | Contract delta: single `build(recipe_id, scope)` API; new block kinds; `Episode` denormalization note |

### Deletions

| File | Reason |
| --- | --- |
| `backend/src/tools/submission/hooks/recursive_partial_plan_gate.py` | Gate moved up the stack — `terminals:` filter on `planner_full_only` is now authoritative |
| `backend/src/tools/submission/notification_triggers/recursive_partial_plan.py` | Soft reminder is redundant once the model can't see the disabled tool |
| `backend/src/task_center/attempt/entry_builder.py` | US-017b: synthetic entry-graph removed. `EntryAttempt` + `EntryAttemptBuilder` deleted; `ENTRY_AGENT_NAME` / `ENTRY_SPAWN_REASON` constants moved to `task_center/entry.py` |
| `backend/src/task_center/attempt/runtime.AttemptRuntime.task_input_for_graph` | Composer is now required at every harness launch; the legacy fallback method is gone. (Earlier report incorrectly attributed this to `c6296d59`; the deletion actually landed in this PR.) |
| `backend/src/task_center/attempt/dispatcher.AttemptDispatcher._evaluator_task_input` | Only callsite was the deleted composer-None evaluator fallback. |
| `backend/src/task_center/attempt/runtime.HarnessAgentLaunch` (back-compat alias) | Removed by `c6296d59` once every consumer migrated to `AgentLaunch` |

---

## 3. Lines of code

| Bucket | Files | Approx lines |
| --- | ---: | ---: |
| New context engine package | 10 | ≈890 |
| New recipe modules | 6 | ≈730 |
| New helper-tool composer plumbing | 1 | 90 |
| New ancestry walker | 1 | 81 |
| New persistence (model + store) | 2 | 92 |
| New agent definitions | 2 | 128 |
| New tests | 18 | ≈2 645 |
| Edited modules (deltas) | 19 | ≈430 added / ≈260 removed |
| Deleted legacy / unused paths | 2 | ≈-130 |

---

## 4. Architecture summary

### 4.1 Composition seam

```
caller (orchestrator / dispatcher / entry coordinator)
  └─> ContextComposer.compose(base_agent_name, scope)
        ├─ AgentResolver.resolve(...)            → AgentSelection
        ├─ ContextEngine.build(recipe_id, scope) → ContextPacket
        ├─ packet.blocks.extend(selection.required_context_blocks)
        ├─ ContextPacketStore.insert(packet)     → context_packet_id
        └─ PromptRenderer.render(packet)         → task_input
      returns LaunchBundle(agent_def, system_prompt, task_input,
                           packet, context_packet_id)
  └─> task row stores context_packet_id
  └─> launcher.launch(AgentLaunch(attempt_id?, system_prompt, …))
```

`ContextComposer` has one method. Every TaskCenter-owned role currently wired
to composition goes through it; adding a new composed role is a recipe
registration plus an `agent.md` declaration — no engine, resolver, composer,
or launcher edits.

### 4.2 Variants and predicates

Selection rules live on the **base agent definition**, not on a global
rules list:

```yaml
variants:
  - when: partial_plan_caller_ancestor
    use: planner_full_only
    note: "ancestry contains a partial-planned caller graph"
    required_context_blocks:
      - kind: capability_note
        priority: required
        text: "Partial planning is disabled in this request's ancestry."
```

`PredicateRegistry` registers named predicates in code; `agent.md` only
references them by id. Variant chaining is forbidden (`use:` target's
`variants:` must be empty); `validate_agent_definitions_resolved()` raises
at startup on any unresolved reference.

### 4.3 Recipes are data, engine is generic

`ContextEngine.build(recipe_id, scope)` looks up the recipe in
`RecipeRegistry`, validates the scope's required fields, and delegates to
the recipe's pure builder. The engine carries no role names. Six built-in
recipes ship: `planner_v1`, `generator_v1`, `evaluator_v1`,
`entry_executor_v1`, `advisor_v1`, `resolver_v1`. New recipes are added by
registering another `ContextRecipe`.

### 4.4 Helper composition (advisor / resolver)

`ask_advisor` and `ask_resolver` no longer hand-build a prompt string. Both
tools call `tools.submission.helper_agent._compose.compose_helper_bundle`,
which:

1. reads the parent task id from `context.task_center_task_id` (set by the
   harness launcher on every spawn);
2. reads the parent task row's `context_packet_id` (persisted by the
   composer at parent-spawn time) — without it the helper errors clean
   instead of inheriting from a stale or missing frame;
3. derives `request_id` (preferring the typed `task_center_request_id`
   metadata field, falling back to `parent_packet.canonical_refs.request_id`);
4. builds a `ContextScope(request_id, task_id=helper_task_id,
   parent_packet_id, parent_task_id)` and calls `composer.compose("advisor"
   | "resolver", scope)`.

The recipe (`advisor_v1` / `resolver_v1`) demotes every parent-packet block
by one priority level (`required → high → medium → low → low`), tags each
with `metadata['inherited_from_parent']='true'`, and prepends the parent's
task input as a new `priority=required` `parent_question` block. The
renderer segregates inherited blocks under a single "# Parent context"
heading so the helper sees its own contract first and the parent's frame
underneath. The user-supplied tool arguments
(`tool_name` / `tool_payloads` / `prompt` for advisor, `issues_to_resolve`
/ `issue_context` for resolver) are appended as a tail "# Advisor request"
or "# Resolver request" section.

The composer is required at the helper call site — there is no legacy
fallback. Tools without a composer in metadata raise a `HelperComposeError`
that becomes a `is_error` `ToolResult`. The harness launcher always sets
`composer=runtime.composer` on per-spawn metadata, so any agent spawned via
the production path can call `ask_advisor` / `ask_resolver` without extra
plumbing.

`run_subagent` is intentionally **not** rewired. It dispatches arbitrary
subagents (`explorer`, future workers) that do not declare a
`context_recipe`; helper inheritance is only meaningful for the advisor /
resolver helper agents.

### 4.5 The gate moves up the stack

The legacy `PartialPlanAncestorGate` prehook ran after the model called
`submit_partial_plan`. The new gate is the `agent.md` `terminals:` filter on
`planner_full_only` — when the variant fires, `submit_partial_plan` is
never bound to the LLM's tool registry. Defense in depth:

1. `validate_agent_definitions_resolved()` at startup catches frontmatter
   wiring mistakes;
2. Structural-enforcement test in `test_ancestry.py` pins resolver
   predicate + `ResolverContext` helper to the same canonical function;
3. Resolver predicate exceptions abort the spawn (no fail-open).

### 4.6 Episode denormalization

`Episode` now carries `task_specification: str | None` and
`task_summary: str | None`, denormalized from the segment's *passing*
harness graph at close. `EpisodeStore.close_succeeded` writes status
+ both fields atomically inside a single transaction. The graph row
remains the source of truth; the segment row is the read-side projection
for `planner_v1`'s prior-segment block builder.

---

## 5. Sweep and test outcome

Commands run during verification:

- `.venv/bin/pytest backend/tests/task_center -q` — **220 passed** (was 211 before US-017b — `+9` for `test_entry_task_controller.py` and `+1` for the new entry-segment regression test in `test_task_center_entry.py`).
- `.venv/bin/pytest backend/tests/task_center backend/tests/test_tools -q` — **387 passed**.
- `.venv/bin/pytest backend/tests --ignore=test_e2e --ignore=test_benchmarks --ignore=experiments -q` — **968 passed** (was 958 before US-017b).
- `.venv/bin/ruff check backend/src backend/tests` — clean.

Grep-side proofs:

- `grep -rn "PartialPlanAncestorGate\|recursive_partial_plan\|request_has_partial_plan_ancestor" backend/src` — only the historical-note reference inside `task_center/mission/ancestry.py` docstring (intentional);
- `grep -rn "task_input_for_graph" backend/src` — no matches; the legacy fallback was deleted in this PR (correcting the previous report claim that c6296d59 had removed it);
- `grep -rn "composer is None" backend/src` — two legitimate hits: `runtime.require_composer()` self-check and `tools/submission/helper_agent/_compose.py` runtime guard. No production fallback paths;
- `grep -rn "EntryAttemptBuilder\|EntryAttempt\b" backend/` — no matches. `entry_builder.py` is gone; the constants moved to `task_center/entry.py`;
- `grep -rn "composer.compose" backend/src` — five live call sites: orchestrator (planner), dispatcher (generator + evaluator), entry coordinator (entry executor), and the shared helper-tool builder at `tools/submission/helper_agent/_compose.py` (advisor + resolver via `ask_advisor` / `ask_resolver`).

---

## 6. Coverage map

| Plan §4 step | Story | Status | Test file |
| --- | --- | --- | --- |
| 1. Extract ancestry walker | US-001 | ✅ | `test_ancestry.py` |
| 2. Packet schemas | US-002 | ✅ | `test_packet.py` |
| 3. PromptRenderer | US-003 | ✅ | `test_renderer.py` |
| 4. Episode schema delta | US-009 | ✅ | `test_close_succeeded.py` |
| 5. ContextPacketStore + task-row column | US-008 | ✅ store + model + task-row wiring | `test_context_packet_store.py`, `test_task_center_task_helpers.py` |
| 6. ContextScope + RecipeRegistry + ContextEngine | US-004 | ✅ | `test_scope.py`, `test_engine.py` |
| 7. AgentDefinition fields + resolver + startup validation | US-005, US-006, US-007 | ✅ (validation now wired into entry coordinator) | `test_definition_variants.py`, `test_resolver.py`, `test_registry_validation.py` |
| 8. ContextComposer | US-012 | ✅ | `test_composer.py` |
| 9. agent_full_only.md + variants | US-015 | ✅ | `test_planner_full_only_md.py` |
| 10. AgentLaunch rename + nullable graph id | US-013 | ✅ | covered by lifecycle suite |
| 11. Wire orchestrator → composer | US-014 | ✅ | `test_orchestrator_composer.py` |
| 12. Wire dispatcher → composer | US-014 | ✅ | `test_orchestrator_composer.py` |
| 13. Remove obsoleted prehook + trigger | US-016 | ✅ | grep-side proofs above |
| 14. Delete `task_input_for_graph` | — | ✅ | composer-None fallbacks deleted across orchestrator + dispatcher + entry; `task_input_for_graph` and `_evaluator_task_input` deleted; `require_composer()` is the new clean error path |
| 15. Entry executor wiring | US-017 ✅ / US-017b ✅ | entry launch uses `entry_executor_v1`; synthetic graph removed; entry segment has zero `Attempt` rows; `EntryTaskController` owns graph-less lifecycle | `test_task_center_entry.py`, `test_entry_task_controller.py` |
| 16. Helper tool handler wiring | US-011b ✅ | `ask_advisor` + `ask_resolver` rewired through `compose_helper_bundle`; advisor / resolver agents now declare `context_recipe` | `test_submission_helper_tools.py`, `test_helper_recipes.py` |
| 17. End-to-end gate test | US-018 | ✅ | `test_planner_capability_fork.py` |
| 18. Token-budget compression test | US-019 | ✅ | `test_token_budget.py` |

---

## 7. US-017b: entry-graph carve-out (shipped)

The synthetic one-node `Attempt` that previously wrapped the entry
executor is gone. `TaskCenterEntryCoordinator.start` now:

1. Creates the top-level run + entry task id;
2. Builds `MissionHandler` once, reused for both the entry
   request and any delegated-complex-task requests the entry executor
   spawns. The handler's `deliver_close_report` is the run-finalization
   sink that finishes the run when the entry's complex_request closes;
3. Calls `handler.create_mission` and
   `handler.create_initial_segment_with_manager` — the segment is real,
   but no `Attempt` row is created;
4. Constructs `EntryTaskController` from the run + task + segment +
   request ids, plus the handler;
5. Builds `AttemptRuntime` with `composer` and the new
   `entry_task_controller` field wired;
6. Writes the entry task row with `task_center_attempt_id=None`,
   `agent_name="entry_executor"`, `spawn_reason="entry_executor"`;
7. Composes the entry launch via `ContextComposer.compose("entry_executor",
   ContextScope(request_id, task_id))` and calls
   `agent_launcher.launch(AgentLaunch(attempt_id=None, ...))`.

When `attempt_id` is `None`, the launcher attaches
`attempt_runtime=None` to the agent's tool metadata so harness-only
tools fail cleanly outside the controller-aware paths.

### Lifecycle dispatch in graph-less mode

| Event | Owner |
| --- | --- |
| `submit_execution_success` / `submit_execution_failure` | `EntryTaskController.apply_executor_*` (via `ExecutorSubmissionContext`) |
| `request_mission_solution` | `ComplexTaskHandoffCoordinator.start(parent_attempt_id=None)` (entry mode) |
| Delegated complex-task close report | `MissionCloseReportRouter` → `EntryTaskController.apply_mission_close_report` |
| Run exhaustion (agent ended without terminal) | `EphemeralAttemptAgentLauncher._report_unfinished_running_task` → `EntryTaskController.apply_run_exhausted` |
| Startup-time launch failure | `TaskCenterEntryCoordinator._compensate_startup_failure` → `EntryTaskController.apply_run_exhausted` |

Each terminal event drives a single fan-out:
1. CAS the entry task to `DONE`/`FAILED` (idempotent — late races short-circuit);
2. Close the entry segment via `EpisodeStore.close_succeeded` (success)
   or `set_status(FAILED)` (failure / exhaustion);
3. Deregister the segment manager from `EpisodeManagerRegistry`;
4. Call `request_handler.close_mission(...,
   final_attempt_id=None)` — which closes the request and delivers a
   `MissionCloseReport` to the entry coordinator's
   `_finish_entry_run` callback, finishing the run.

### API widenings

* `MissionCloseReport.final_attempt_id: str | None` — `None` for
  entry-segment closes (no graph backing the close).
* `ComplexTaskHandoffResult.parent_attempt_id: str | None` — `None`
  when the caller is the graph-less entry executor.
* `ComplexTaskHandoffCoordinator.start(parent_attempt_id: str | None,
  ...)` — accepts `None` for the entry-mode caller; the
  `_assert_parent_running_and_no_open_child` graph match relaxes when both
  the task row and the caller report no graph.
* `AttemptRuntime.entry_task_controller: EntryTaskController | None` —
  new field, peer to `composer`. Set by the entry coordinator only;
  delegated-only runtimes leave it `None`.

### Regression test pins

* `test_task_center_entry.py::test_entry_executor_runs_in_graph_less_mode`
  asserts `task["task_center_attempt_id"] is None`,
  `metadata.attempt_runtime is None`, and the run finishes via the
  controller's `apply_run_exhausted` path (run.status="failed").
* `test_task_center_entry.py::test_entry_segment_has_zero_attempt_rows`
  asserts `graph_store.list_for_episode(entry.episode_id) == []`.
* `test_entry_task_controller.py` covers each controller method:
  terminal success, terminal failure, run exhaustion, mark-waiting,
  close-report success, close-report failure, idempotency, restore-running.

### What was deleted

* `backend/src/task_center/attempt/entry_builder.py` (full file:
  `EntryAttempt` dataclass, `EntryAttemptBuilder`,
  `ENTRY_AGENT_NAME` / `ENTRY_SPAWN_REASON` constants — the constants moved
  to `task_center/entry.py`).
* `AttemptRuntime.task_input_for_graph` (the legacy fallback method).
* `AttemptDispatcher._evaluator_task_input` (only callsite was a
  composer-None fallback).
* The `composer is None` branch in
  `AttemptOrchestrator._build_planner_launch`,
  `AttemptDispatcher._build_generator_launch`,
  `AttemptDispatcher._build_evaluator_launch`, and
  `TaskCenterEntryCoordinator._build_entry_launch`. Production paths now
  raise `GraphInvariantViolation` via `AttemptRuntime.require_composer()`
  if a composer is missing.

---

## 8. Phase-06 doc deltas applied

The companion `phase-06-context-engine.md` was amended:

1. **Engine API shape** — the role-keyed `build_planner_context` /
   `build_generator_context` / `build_evaluator_context` /
   `build_request_close_context` methods are replaced with one
   `ContextEngine.build(recipe_id, scope) -> ContextPacket` method backed
   by `RecipeRegistry`. The amendment is annotated as a contract revision.
2. **New block kinds** — `prior_episode_specification`, `parent_question`,
   and `capability_note` were added to the suggested block-kinds list.
   `parent_question` is the helper-recipe contract: the parent's task input
   carried as the helper's `priority=required` framing block.
3. **Episode denormalization note** — the *Sources of truth* section
   records that `Episode.task_specification` and `Episode.task_summary`
   are denormalized projections from the segment's passing harness graph
   at close. The graph row remains canonical.
4. **US-017b carve-out amendment (this PR)** — *Sources of truth* now also
   records that an entry segment may have **zero** `Attempt` rows. The
   entry executor lives in graph-less mode receiving lifecycle events
   through `EntryTaskController` rather than a `AttemptOrchestrator`.
   `MissionCloseReport.final_attempt_id` is widened to
   `str | None`; `None` identifies the entry-segment close path.

---

## 9. Notes on commit attribution

The delivery landed across two parallel sessions (codex and Claude Code),
following the `feedback_parallel_user_commits` pattern documented in
project memory:

- `418fc6b5` (codex) — Introduce context engine and agent variants
- `52b7dfc7` (codex) — Wire ContextComposer through harness dispatcher
- `f96d8d67` (codex) — Adapt planner markdown test for multi-agent directory
- `cb313c88` (codex) — Move partial-plan gate to planner agent variant
- `bdc05726` (codex) — Refine context engine composition helpers
- `93c5bc4a` (codex) — Remove planner initial-segment packet metadata
- `c6296d59` (codex) — Refine context engine composition wiring
  (`HarnessAgentLaunch` back-compat alias removed, `context_packet_id`
  threaded onto the task row, `ContextPacketStore` wired through
  bootstrap; helper recipes were also deleted in this commit on the
  cleanup-of-unused-code reading). The earlier report's claim that this
  commit also removed `task_input_for_graph` and the composer-None
  fallbacks was over-reach — those landed in the US-017b PR (below).
- `8a07402a` (Claude) — Restore helper-recipe substrate (advisor / resolver
  recipes, scope `parent_*` fields, `parent_question` block kind, renderer
  inherited-block grouping); reverses the helper-recipe deletes from
  `c6296d59` so US-011b can land on top
- `d6665799` (Claude) — Wire `ask_advisor` / `ask_resolver` through
  `ContextComposer` (US-011b); helpers now actually call
  `composer.compose("advisor"/"resolver", scope)` with the parent's
  `context_packet_id` from the task row
- *(this PR — Claude)* — US-017b carve-out: delete
  `EntryAttemptBuilder`; introduce `EntryTaskController` peer to the
  composer on `AttemptRuntime`; rewrite `TaskCenterEntryCoordinator`
  to launch the entry executor with `attempt_id=None`; rewire
  submission tools through the unified `ExecutorSubmissionContext`;
  generalize `ComplexTaskHandoffCoordinator` and
  `MissionCloseReportRouter` to handle entry mode; widen
  `MissionCloseReport.final_attempt_id` to `str | None`;
  delete `task_input_for_graph` and the composer-None production
  fallbacks; introduce `runtime.require_composer()`. Phase-06 *Sources of
  truth* amended with the entry-segment-may-have-zero-graphs invariant.

Stage with explicit file paths; avoid `git add <dir>`.
