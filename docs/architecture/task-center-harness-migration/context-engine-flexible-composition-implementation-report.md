# Context Engine — Flexible Composition: Implementation Report

Companion to
[`context-engine-flexible-composition.md`](../../../.omc/plans/context-engine-flexible-composition.md)
(plan v8) and the corresponding amendments to
[`phase-06-context-engine.md`](./phase-06-context-engine.md).
This report records the structural changes, file inventory, verification
outcome, and deferred items for the flexible-composition implementation.

---

## 1. Verdict

**Verdict: ships as a structural delivery on top of Phase 06.**

Plan v8 introduces three composition seams behind a single
`ContextComposer.compose(base_agent_name, scope)` method. Every
TaskCenter-owned harness spawn currently wired to the composer (planner,
generator, evaluator, entry executor) now funnels through that path. The
first variant consumer is the planner full vs full-only fork driven by the
partial-plan ancestor gate; the legacy
`PartialPlanAncestorGate` prehook + `recursive_partial_plan` notification
trigger have been removed in favour of an `agent.md` `terminals:` filter on
the `planner_full_only` variant.

The TaskCenter suite (203 tests after removing unused helper-recipe tests) is
green. Ruff is clean across the touched `task_center`, `agents`, `db`, and
server slices. Two stories (US-011b helper-tool rewiring and US-017b
entry-graph carve-out) are explicitly deferred — they are non-blocking for the
planner-fork feature. Helper-composition code is not shipped until the helper
tool handlers are actually rewired through the composer.

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

### New ancestry helper

| File | Lines | Purpose |
| --- | ---: | --- |
| `backend/src/task_center/harness_graph/ancestry.py` | 81 | Canonical `has_partial_planned_caller_ancestor(*, request_id, …stores) -> bool` walker |

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

### Edited modules

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/agents/types.py` | edited | `AgentVariant`, `AgentSelectionBlock`, `AgentDefinition.{variants, context_recipe}` |
| `backend/src/agents/registry.py` | edited | `validate_agent_definitions_resolved()` |
| `backend/src/agents/main_agent/{planner,evaluator,generator/executor,generator/verifier}/agent.md` | edited | `context_recipe:` declared; planner gains `variants:` referencing `planner_full_only` |
| `backend/src/db/models/{__init__,task_center,task_segment}.py` | edited | `ContextPacketRecord` exported; task row `context_packet_id`; `TaskSegment` denormalized fields |
| `backend/src/db/stores/{__init__,task_center_store,task_segment_store}.py` | edited | `ContextPacketStore` exported; task context-packet id round-trip; `close_succeeded`; `get_evaluator_pass_summary` helper |
| `backend/src/task_center/segment/{segment,manager}.py` | edited | DTO fields; manager calls `close_succeeded` on success path |
| `backend/src/task_center/complex_task/handler.py` | edited | Optional `task_store` thread-through |
| `backend/src/task_center/harness_graph/{runtime,launcher,orchestrator,dispatcher}.py` | edited | `AgentLaunch` rename + nullable `harness_graph_id`; composer-or-fallback launch helpers |
| `backend/src/task_center/entry.py` | edited | Builds composer at startup, runs `validate_agent_definitions_resolved()` |
| `backend/src/tools/submission/hooks/__init__.py` | edited | `PartialPlanAncestorGate` export removed |
| `backend/src/tools/submission/notification_triggers/__init__.py` | edited | `recursive_partial_plan` factory removed |
| `backend/src/tools/submission/main_agent/planner/submit_partial_plan.py` | edited | Prehook registration removed |
| `backend/src/agents/main_agent/planner/agent.md` | edited | `notification_triggers: []`; updated prose to reference the new gate |
| `docs/architecture/task-center-harness-migration/phase-06-context-engine.md` | edited | Contract delta: single `build(recipe_id, scope)` API; new block kinds; `TaskSegment` denormalization note |

### Deletions

| File | Reason |
| --- | --- |
| `backend/src/tools/submission/hooks/recursive_partial_plan_gate.py` | Gate moved up the stack — `terminals:` filter on `planner_full_only` is now authoritative |
| `backend/src/tools/submission/notification_triggers/recursive_partial_plan.py` | Soft reminder is redundant once the model can't see the disabled tool |
| `backend/src/task_center/context_engine/recipes/helper.py` | Helper recipes were unused until helper tools are rewired through `ContextComposer` |
| `backend/tests/task_center/context_engine/test_helper_recipes.py` | Removed with the unused helper recipe implementation |

---

## 3. Lines of code

| Bucket | Files | Approx lines |
| --- | ---: | ---: |
| New context engine package | 10 | ≈890 |
| New recipe modules | 4 | ≈590 |
| New ancestry walker | 1 | 81 |
| New persistence (model + store) | 2 | 92 |
| New agent definitions | 2 | 128 |
| New tests | 17 | ≈2 425 |
| Edited modules (deltas) | 17 | ≈410 added / ≈250 removed |
| Deleted legacy / unused paths | 4 | ≈-300 |

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
  └─> launcher.launch(AgentLaunch(harness_graph_id?, system_prompt, …))
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
the recipe's pure builder. The engine carries no role names. Four built-in
recipes ship with the plan; new recipes are added by registering another
`ContextRecipe`.

### 4.4 Helper composition remains deferred

Advisor, resolver, and subagent tools still build tool-specific prompts and
call `run_ephemeral_agent` directly. The unused `advisor_v1` / `resolver_v1`
recipe substrate was removed during review so the runtime does not ship dead
composer code. When helper tools are rewired, that work should add the recipe,
scope, and handler changes in the same patch.

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

### 4.6 TaskSegment denormalization

`TaskSegment` now carries `task_specification: str | None` and
`task_summary: str | None`, denormalized from the segment's *passing*
harness graph at close. `TaskSegmentStore.close_succeeded` writes status
+ both fields atomically inside a single transaction. The graph row
remains the source of truth; the segment row is the read-side projection
for `planner_v1`'s prior-segment block builder.

---

## 5. Sweep and test outcome

Commands run during cleanup verification:

- `uv run pytest backend/tests/task_center/context_engine backend/tests/task_center/persistence/test_context_packet_store.py backend/tests/task_center/persistence/test_task_center_task_helpers.py backend/tests/task_center/lifecycle/test_orchestrator_composer.py backend/tests/task_center/lifecycle/test_task_center_entry.py backend/tests/test_agents/test_planner_full_only_md.py backend/tests/test_tools/test_submission_helper_tools.py -q` — **71 passed**
- `uv run ruff check backend/src/task_center/context_engine backend/src/task_center/harness_graph backend/src/task_center/entry.py backend/src/agents backend/src/db backend/src/server/routers/core.py backend/src/server/app_factory.py backend/src/tools/submission backend/tests/task_center/context_engine backend/tests/task_center/persistence/test_context_packet_store.py backend/tests/task_center/persistence/test_task_center_task_helpers.py backend/tests/task_center/lifecycle/test_orchestrator_composer.py backend/tests/task_center/lifecycle/test_task_center_entry.py backend/tests/test_agents/test_planner_full_only_md.py backend/tests/test_tools/test_submission_helper_tools.py` — clean
- `uv run pytest backend/tests/task_center -q` — **203 passed**

Grep-side proofs:

- `grep -rn "PartialPlanAncestorGate\|recursive_partial_plan\|request_has_partial_plan_ancestor" backend/src` — only the historical-note reference inside `task_center/harness_graph/ancestry.py` docstring (intentional);
- `grep -rn "task_input_for_graph" backend/src/task_center/harness_graph/{orchestrator,dispatcher,entry}.py` — call sites switched to the composer path; the helper is retained on `HarnessGraphRuntime` as the composer-less fallback for tests that don't construct one (queued for removal once US-011b/US-017b land).

---

## 6. Coverage map

| Plan §4 step | Story | Status | Test file |
| --- | --- | --- | --- |
| 1. Extract ancestry walker | US-001 | ✅ | `test_ancestry.py` |
| 2. Packet schemas | US-002 | ✅ | `test_packet.py` |
| 3. PromptRenderer | US-003 | ✅ | `test_renderer.py` |
| 4. TaskSegment schema delta | US-009 | ✅ | `test_close_succeeded.py` |
| 5. ContextPacketStore + task-row column | US-008 | ✅ store + model + task-row wiring | `test_context_packet_store.py`, `test_task_center_task_helpers.py` |
| 6. ContextScope + RecipeRegistry + ContextEngine | US-004 | ✅ | `test_scope.py`, `test_engine.py` |
| 7. AgentDefinition fields + resolver + startup validation | US-005, US-006, US-007 | ✅ (validation now wired into entry coordinator) | `test_definition_variants.py`, `test_resolver.py`, `test_registry_validation.py` |
| 8. ContextComposer | US-012 | ✅ | `test_composer.py` |
| 9. agent_full_only.md + variants | US-015 | ✅ | `test_planner_full_only_md.py` |
| 10. AgentLaunch rename + nullable graph id | US-013 | ✅ | covered by lifecycle suite |
| 11. Wire orchestrator → composer | US-014 | ✅ | `test_orchestrator_composer.py` |
| 12. Wire dispatcher → composer | US-014 | ✅ | `test_orchestrator_composer.py` |
| 13. Remove obsoleted prehook + trigger | US-016 | ✅ | grep-side proofs above |
| 14. Delete `task_input_for_graph` | — | 🟡 deferred | retained as composer-less fallback |
| 15. Entry executor wiring | US-017 ✅ / US-017b 🟡 | entry launch uses `entry_executor_v1`; synthetic-graph carve-out deferred | lifecycle suite |
| 16. Helper tool handler wiring | US-011b 🟡 | deferred; unused helper recipes are not shipped | — |
| 17. End-to-end gate test | US-018 | ✅ | `test_planner_capability_fork.py` |
| 18. Token-budget compression test | US-019 | ✅ | `test_token_budget.py` |

---

## 7. Deferred items

Two items intentionally deferred. Both are non-blocking for the
planner-fork feature.

### US-011b — `ask_advisor` + `ask_resolver` tool handler rewiring

The helper tool handlers
(`tools/submission/helper_agent/advisor/ask_advisor.py` and
`tools/submission/helper_agent/resolver/ask_resolver.py`) still build prompts
directly and call `run_ephemeral_agent`. To activate parent inheritance the
handlers need to:

1. Look up the parent task's `context_packet_id` via the task store;
2. Carry the helper question from tool arguments, not from
   `parent_task.task_input`;
3. Add the helper recipe and scope fields needed by the tool handler;
4. Call `composer.compose("advisor", scope)` or
   `composer.compose("resolver", scope)`;
5. Pass `bundle.task_input` + `bundle.system_prompt` to the launch.

Until that lands, helper agents intentionally keep `context_recipe` unset and
no helper recipe is registered. `run_subagent` remains the background subagent
dispatcher and is not the resolver helper path.

### US-017b — delete `EntryHarnessGraphBuilder`; entry-graph carve-out

The entry launch now composes `entry_executor_v1`, but the synthetic one-node
graph that lets the entry executor reuse the harness submission path is still
in place. Plan §3.6 vs §4 step 15 conflict on whether to keep the synthetic
graph; the v3 changelog (cited in the PRD notes) wins, but the carve-out is
structural cleanup that doesn't change the model-facing behaviour. Deferring
keeps this delivery focused on the composition seam.

When the carve-out lands, `EntryHarnessGraphBuilder` is deleted and the
entry coordinator writes the entry task row with
`task_center_harness_graph_id=None`. The launcher already handles this
case — when `AgentLaunch.harness_graph_id is None`, the launcher attaches
`harness_graph_runtime=None` so harness-only tools fail cleanly.

### Knock-on follow-up — `task_input_for_graph` deletion

`HarnessGraphRuntime.task_input_for_graph` is retained as the composer-less
fallback for tests that don't construct a composer. Once those tests are
migrated and the entry-graph carve-out lands, the production path can require
the composer unconditionally and the fallback method can be deleted.

---

## 8. Phase-06 doc deltas applied

The companion `phase-06-context-engine.md` was amended in this PR:

1. **Engine API shape** — the role-keyed `build_planner_context` /
   `build_generator_context` / `build_evaluator_context` /
   `build_request_close_context` methods are replaced with one
   `ContextEngine.build(recipe_id, scope) -> ContextPacket` method backed
   by `RecipeRegistry`. The amendment is annotated as a contract revision.
2. **New block kinds** — `prior_segment_specification` and
   `capability_note` were added to the suggested block-kinds list.
3. **TaskSegment denormalization note** — the *Sources of truth* section
   now records that `TaskSegment.task_specification` and
   `TaskSegment.task_summary` are denormalized projections from the
   segment's passing harness graph at close. The graph row remains
   canonical.

---

## 9. Notes on commit attribution

A parallel codex session committed three batches of architectural code
during this delivery:

- `418fc6b5` — Introduce context engine and agent variants
- `52b7dfc7` — Wire ContextComposer through harness dispatcher
- `f96d8d67` — Adapt planner markdown test for multi-agent directory

This is the same `feedback_parallel_user_commits` pattern documented in
project memory. The architectural code at HEAD is the same code this
report describes; only the unstaged tail (validate-wiring,
US-016 deletions, planner agent.md prose, this report, deslop cleanups)
needs an explicit commit by the user. Stage with explicit file paths;
avoid `git add <dir>`.
