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
TaskCenter-owned spawn (planner, generator, evaluator, entry executor,
advisor, resolver) now funnels through that path. The first variant consumer
is the planner full vs full-only fork driven by the partial-plan ancestor
gate; the legacy `PartialPlanAncestorGate` prehook + `recursive_partial_plan`
notification trigger have been removed in favour of an `agent.md`
`terminals:` filter on the `planner_full_only` variant.

US-011b (helper tool rewiring) landed in `d6665799`: `ask_advisor` and
`ask_resolver` now compose `advisor_v1` / `resolver_v1` packets, persist them,
and pass the rendered prompt + selected agent definition into
`run_ephemeral_agent`. The composer-less helper path is gone. The TaskCenter
suite is green at 218 tests; the broader backend sweep is green at 958 tests.
Ruff is clean across the touched `task_center`, `agents`, `db`, server, and
`tools/submission` slices.

One story remains intentionally deferred: **US-017b — delete
`EntryHarnessGraphBuilder` and carve the entry executor out of the harness
submission path.** It is non-blocking for the planner-fork feature and does
not change model-facing behaviour; see §7 for what the carve-out entails and
what blocks it.

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
| `backend/src/task_center/complex_task/ancestry.py` | 81 | Canonical `has_partial_planned_caller_ancestor(*, request_id, …stores) -> bool` walker |

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
| `backend/src/task_center/harness_graph/runtime.HarnessGraphRuntime.task_input_for_graph` | Composer is unconditional; the legacy fallback method is gone (`c6296d59`) |
| `backend/src/task_center/harness_graph/runtime.HarnessAgentLaunch` (back-compat alias) | Removed once every consumer migrated to `AgentLaunch` (`c6296d59`) |

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

### 4.6 TaskSegment denormalization

`TaskSegment` now carries `task_specification: str | None` and
`task_summary: str | None`, denormalized from the segment's *passing*
harness graph at close. `TaskSegmentStore.close_succeeded` writes status
+ both fields atomically inside a single transaction. The graph row
remains the source of truth; the segment row is the read-side projection
for `planner_v1`'s prior-segment block builder.

---

## 5. Sweep and test outcome

Commands run during verification:

- `.venv/bin/pytest backend/tests/task_center -q` — **211 passed**
- `.venv/bin/pytest backend/tests/test_tools/test_submission_helper_tools.py backend/tests/task_center -q` — **218 passed**
- `.venv/bin/pytest backend/tests --ignore=test_e2e --ignore=test_benchmarks --ignore=experiments -q` — **958 passed**
- `uv run ruff check backend/src/task_center backend/src/agents backend/src/db backend/src/server/routers/core.py backend/src/server/app_factory.py backend/src/tools/submission backend/tests/task_center backend/tests/test_tools/test_submission_helper_tools.py` — clean

Grep-side proofs:

- `grep -rn "PartialPlanAncestorGate\|recursive_partial_plan\|request_has_partial_plan_ancestor" backend/src` — only the historical-note reference inside `task_center/complex_task/ancestry.py` docstring (intentional);
- `grep -rn "task_input_for_graph" backend/src` — no matches; the composer-less fallback was deleted alongside the launcher composer-required cutover;
- `grep -rn "composer.compose" backend/src` — five live call sites: orchestrator (planner), dispatcher (generator + evaluator), entry coordinator (entry executor), and the shared helper-tool builder at `tools/submission/helper_agent/_compose.py` (advisor + resolver via `ask_advisor` / `ask_resolver`).

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
| 14. Delete `task_input_for_graph` | — | ✅ | composer-less fallback removed in `c6296d59`; launcher requires composer |
| 15. Entry executor wiring | US-017 ✅ / US-017b 🟡 | entry launch uses `entry_executor_v1`; synthetic-graph carve-out deferred | lifecycle suite |
| 16. Helper tool handler wiring | US-011b ✅ | `ask_advisor` + `ask_resolver` rewired through `compose_helper_bundle`; advisor / resolver agents now declare `context_recipe` | `test_submission_helper_tools.py`, `test_helper_recipes.py` |
| 17. End-to-end gate test | US-018 | ✅ | `test_planner_capability_fork.py` |
| 18. Token-budget compression test | US-019 | ✅ | `test_token_budget.py` |

---

## 7. Deferred items

One story remains intentionally deferred. It is non-blocking for the
planner-fork feature and does not change model-facing behaviour.

### US-017b — delete `EntryHarnessGraphBuilder`; entry-graph carve-out

**Scope.** The entry launch already composes `entry_executor_v1` and persists
its packet, but the synthetic one-node `HarnessGraph` that wraps the entry
executor is still in place. `TaskCenterEntryCoordinator.start` builds it via
`EntryHarnessGraphBuilder.create`, which:

* creates the initial `TaskSegment` + `TaskSegmentManager`;
* creates a one-node `HarnessGraph`, stamps a synthetic plan contract
  (`task_specification=prompt`, single evaluation criterion), and pins it to
  `STAGE.GENERATING`;
* writes the entry task row with `role=GENERATOR`,
  `task_center_harness_graph_id=<graph.id>`, and
  `spawn_reason="entry_executor"`;
* registers a `HarnessGraphOrchestrator` so the orchestrator-registry lookup
  for the synthetic graph id resolves to a live state machine.

The synthetic graph exists so the entry executor's three terminals
(`request_complex_task_solution`, `submit_execution_success`,
`submit_execution_failure`) and its delegated-complex-task resume path
(`apply_complex_task_close_report` on the orchestrator) can reuse the
existing harness-submission infrastructure unchanged.

**Why this is debt, not a feature.** The synthetic plan contract is a lie —
there is no real planner / generator / evaluator triplet. Plan §3.6 of the
v8 spec says the entry executor is *not* a harness graph; §4 step 15 still
provisions one for submission-tool reuse. The v3 changelog (referenced in
the PRD notes) resolves the conflict in favour of §3.6. Until the carve-out
lands, every read of an entry-segment graph shows a passing one-node graph
that never actually planned anything.

**What the carve-out requires.** Roughly two days of work spread across:

1. **Submission tools (`request_complex_task_solution`,
   `submit_execution_success`, `submit_execution_failure`).** Add a code path
   that operates on the entry task directly when `harness_graph_runtime is
   None` instead of delegating to `HarnessGraphOrchestrator.apply_*`.
   `submit_execution_success` / `submit_execution_failure` close the
   `TaskCenter` run via `_finish_entry_run` directly;
   `request_complex_task_solution` parks the entry task in
   `WAITING_COMPLEX_TASK` without touching a graph.
2. **Resume path.** Replace the orchestrator's
   `apply_complex_task_close_report` for the entry case. Either thread the
   resume callback through the entry coordinator (single-task awareness) or
   rebuild it as a `TaskSegmentManager`-level resume that doesn't assume a
   `generator_task_ids` list.
3. **`HarnessGraph.is_passed` invariants.** The synthetic graph currently
   passes when the entry task succeeds, which downstream code reads. Audit
   `harness_graph_store.list_for_segment` consumers (especially the planner
   recipe's failed-graph landscape) for entry-segment edge cases.
4. **Entry coordinator.** Delete `EntryHarnessGraphBuilder`; replace
   `_create_runtime` / `_launch_entry_executor` with a path that builds the
   `AgentLaunch` with `harness_graph_id=None` and writes the entry task row
   with `task_center_harness_graph_id=None`. The launcher already handles
   this: when `launch.harness_graph_id is None`, it attaches
   `harness_graph_runtime=None`, so harness-only tools fail cleanly.
5. **`AgentLaunch.harness_graph_id` typing.** Currently `str | None`; it
   already has the right shape, but document the entry-executor case as the
   live consumer.
6. **Tests.**
   * `tests/task_center/lifecycle/test_task_center_entry.py` — assertions
     about the synthetic graph need to flip to "no graph row written for the
     entry segment".
   * `tests/task_center/lifecycle/test_phase04_complex_task_handoff.py` and
     `test_phase04_close_report_delivery.py` — exercise both the harness-graph
     path (delegated complex tasks) and the new entry-task-only path.
   * Add a regression test pinning that no `HarnessGraph` row exists for an
     entry-only `TaskSegment`.
7. **Documentation.** Amend `phase-06-context-engine.md` *Sources of truth*
   to record that an entry segment may have zero `HarnessGraph` rows.

**What blocks doing it now.** Nothing technical. Skipped because:
* Entry-executor path is exercised end-to-end and is not the rotting
  surface — the synthetic graph is consistent and the model never sees it.
* The submission-tool changes ripple into terminal-tool gates that haven't
  been touched in this delivery; cleaner as its own PR with its own review
  surface.
* The carve-out is structural cleanup, not a feature. Bundling it into this
  delivery would inflate the diff and mix architectural cleanup with the
  composition seam that is the actual deliverable.

**Recommended sequencing.** Land the carve-out as a follow-up PR before any
work that adds new entry-executor terminals or new entry-segment behaviours,
so the new code does not bake in the synthetic-graph assumption.

---

## 8. Phase-06 doc deltas applied

The companion `phase-06-context-engine.md` was amended in this PR:

1. **Engine API shape** — the role-keyed `build_planner_context` /
   `build_generator_context` / `build_evaluator_context` /
   `build_request_close_context` methods are replaced with one
   `ContextEngine.build(recipe_id, scope) -> ContextPacket` method backed
   by `RecipeRegistry`. The amendment is annotated as a contract revision.
2. **New block kinds** — `prior_segment_specification`, `parent_question`,
   and `capability_note` were added to the suggested block-kinds list.
   `parent_question` is the helper-recipe contract: the parent's task input
   carried as the helper's `priority=required` framing block.
3. **TaskSegment denormalization note** — the *Sources of truth* section
   now records that `TaskSegment.task_specification` and
   `TaskSegment.task_summary` are denormalized projections from the
   segment's passing harness graph at close. The graph row remains
   canonical.

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
- `c6296d59` (codex) — Refine context engine composition wiring (composer
  required, `task_input_for_graph` removed, `context_packet_id` threaded
  onto the task row, `ContextPacketStore` wired through bootstrap; helper
  recipes were also deleted in this commit on the cleanup-of-unused-code
  reading)
- `8a07402a` (Claude) — Restore helper-recipe substrate (advisor / resolver
  recipes, scope `parent_*` fields, `parent_question` block kind, renderer
  inherited-block grouping); reverses the helper-recipe deletes from
  `c6296d59` so US-011b can land on top
- `d6665799` (Claude) — Wire `ask_advisor` / `ask_resolver` through
  `ContextComposer` (US-011b); helpers now actually call
  `composer.compose("advisor"/"resolver", scope)` with the parent's
  `context_packet_id` from the task row

Stage with explicit file paths; avoid `git add <dir>`.
