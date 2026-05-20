# RALPLAN: XML context format + `plan_spec` / `next_iteration_handoff_goal` rename

**Mode:** deliberate (auto-enabled: public API breakage + multi-module rename + persistence boundary)
**Date:** 2026-05-18

---

## 1. Scope (what this plan delivers)

Three coupled changes, executed in one coherent migration:

1. **Replace markdown-headings rendering with XML-tagged components** in every context recipe's `user_message1` payload (planner, generator, evaluator, attempt_landscape sub-blocks).
2. **Rewrite role instructions (`user_message2`)** to reference context sections by tag mention (e.g. `<evaluation_criteria>`) instead of heading text ("Evaluation Criteria").
3. **Rename two fields end-to-end:**
   - `task_specification` → `plan_spec` (already partially done at the submission schema boundary; this plan completes the propagation through DTO / persistence / recipes / iteration state).
   - `deferred_goal` → `next_iteration_handoff_goal` (full rename, no current alias).

Semantic note on the second rename: presence of `next_iteration_handoff_goal` means "the planner believes this iteration is too risky to complete in one shot and hands off a bounded slice to the next iteration." The new name encodes that signal in the field name itself.

---

## 2. Principles

1. **One renderer, no per-agent format drift.** A single XML grammar is emitted by the renderer; every recipe just produces structured `ContextBlock`s. The renderer is the only place tag names appear in code.
2. **LLM-facing names match terminal-tool names.** The XML tag for a field is the same string the planner submits as a tool parameter — closed loop, no translation table.
3. **Body content stays verbatim.** The renderer wraps; it never rewrites recipe payloads. PR descriptions, plan prose, summaries pass through untouched.
4. **Asymmetric structure by lifecycle status.** Prior closed iterations render as `<accepted_plan>` + `<summary>` (flat); failed/current attempts render as `<attempt_plan>` with nested children. Mirrors the data model — attempts have plans, closed iterations have outcomes.
5. **Migration is runtime-one-shot.** No dual-format runtime, no compatibility shims active at the same time. Source-tree changes may stage across commits within a single PR (e.g. snapshot regen as a follow-up commit on the branch), but no co-existing renderer classes on `main`. Tests snap to the new format in the same change set.

## 3. Decision drivers (ranked)

1. **LLM legibility under long context.** Explicit XML closure beats implicit markdown closure when bodies are long or contain `#` characters (PR descriptions, code blocks). Today's `<Workspace Root>` × 2 bug is direct evidence of fragility.
2. **Field-name coherence between submission and rendering.** Planner submits `plan_spec` as a tool param, but recipes render the same value under heading `# Attempt Plan` and source field `task_specification`. Closing this loop reduces the planner's mental indirection.
3. **Recipe simplicity.** Recipes today bake heading strings into `metadata["subheading"]` like `f"Iteration {N} accepted plan"`. Moving structure to tags pushes that metadata to attributes (`iteration_no="N"`), where the data already lives.

## 4. Viable options considered

### Option A — Full XML migration + full rename (chosen)
End state: all recipes emit XML; both renames propagated through DTO / persistence / recipes; tests snap; no dual-format runtime.

- **Pros:** clean coherent surface; closes the field-name loop in one stroke; matches the agreed design.
- **Cons:** one large commit boundary; substantial test snapshot churn; needs care on persistence column rename (or column-name aliasing).

### Option B — XML at renderer only; keep field names
End state: renderer emits XML by reading existing block metadata; `task_specification` / `deferred_goal` stay everywhere in code.

- **Pros:** smallest diff; no persistence/DTO churn.
- **Cons:** widens the divergence between submission surface (`plan_spec`) and persistence (`task_specification`); leaves the asymmetry the original schema comment already flagged as scope-limited; doesn't address the *meaning* the user wants from `handoff_goal`.
- **Invalidation:** the user explicitly asked for both renames; this option only delivers the rendering change. Rejected on scope grounds.

### Option C — Dual format (markdown + XML) gated by feature flag
End state: renderer can emit either format; production agents read XML, legacy tests still pass on markdown.

- **Pros:** zero risk to existing tests; reversible.
- **Cons:** doubles the renderer surface; agents would have to learn both formats during the transition; flag becomes permanent in practice; debugging the two paths is harder than debugging one new path.
- **Invalidation:** Principle 5 explicitly rejects dual-format runtime; the migration is small enough in absolute terms that one-shot is safer than a flag that never gets removed.

## 5. Pre-mortem (deliberate-mode requirement)

| # | Failure scenario | Probability | Detection | Mitigation |
|---|---|---|---|---|
| 1 | Persistence column rename fails or partial-migrates → existing recorded iterations unreadable | medium | startup load of `.planning/` artifacts crashes; iteration restore tests fail | Keep persistence column name `task_specification` and `deferred_goal` unchanged; rename only at the DTO/dataclass layer using `dataclasses.field` mapping. Field rename is then pure Python; on-disk format is invariant. |
| 2 | Snapshot tests baked on markdown heading shape explode; lots of mechanical churn risks introducing bugs in the snap-update | medium | `make test` fails with widespread snapshot diffs; some assertions on substrings (`"# Goal"`) fail beyond simple snapshot regeneration | In-place class rename + snapshot regen on the same PR branch. If the snapshot churn is too noisy for one commit, split snapshot updates into a follow-up commit *on the same branch* — never on `main`. No co-existing `MarkdownPromptRenderer` / `XmlPromptRenderer` at any point: rename in place. |
| 3 | Role-instruction wording references stale heading text ("Prior Failed Attempts", "Partial Plan Boundary") that no longer exist; planner sees tag mentions but instruction tells it to look at headings | high if not coordinated | manual transcript inspection; e2e scenario `attempt_retry_planner_failure` | Rewrite role_instruction.py text in the SAME commit as the renderer change; assertion test that every tag mentioned in role-instruction text appears in at least one recipe's rendered output. |
| 4 | User-supplied content (PR description, error log, pasted diff) contains a literal `</goal>`, `</attempt_plan>`, or other reserved-tag-closer substring; rendered output has a prematurely closed wrapper that downstream LLMs misparse | low-medium | goals whose text includes such substrings; existing scenario fixtures don't include them so today's tests won't catch it | Renderer pre-validates `block.text` against the set of structural tag-closers derived from `_DEFAULT_TAGS`; raises `ContextEngineError` on hit. **Error contract:** message must contain (a) the offending closer string (`</goal>`), (b) the `block.source_id`, and (c) a one-line remediation hint: `"Rewrite the block body to avoid this structural closer, or use a different ContextBlockKind for this content."` No silent escaping. Document the constraint in `context_engine/__init__.py` docstring. Unit test: each `_DEFAULT_TAGS` value's closer substring (e.g. `</goal>`) embedded in a block body is rejected with an error whose `str(exc)` contains all three required parts. |

## 6. Surface-area inventory (from codebase exploration)

### Field-rename surface

| File | Symbol | Action |
|---|---|---|
| `backend/src/task_center/attempt/state.py:40,44` | `Attempt.task_specification`, `Attempt.deferred_goal` | Rename fields → `plan_spec`, `next_iteration_handoff_goal`; `has_partial_continuation` property → `has_iteration_handoff` |
| `backend/src/task_center/iteration/state.py:37,43,72` | `Iteration.task_specification`, `Iteration.deferred_goal`, `IterationProjection.task_specification` | Same rename |
| `backend/src/task_center/iteration/manager.py:189-201,314-321,347` | `set_deferred_goal()` callers, `SuccessContinue` payload | Rename method to `set_iteration_handoff_goal`; rename payload field |
| `backend/src/task_center/_core/persistence.py:103-150` | `set_deferred_goal()`, attempt insert/update signatures | Rename method names + parameter names; **keep DB column names** `task_specification` / `deferred_goal` via `dataclasses.field(metadata={"db_column": ...})` or simple `__post_init__` translation in the row mapper |
| `backend/src/task_center/_core/invariants.py:53-56` | deferred_goal invariant | Update field reference + error message |
| `backend/src/task_center/attempt/orchestrator.py:115-118,237-239` | submission validation + attempt creation | Rename references |
| `backend/src/tools/submission/planner/_schemas.py:50-77` | `PlannerSubmissionBaseInput` | `plan_spec` already exists at boundary; `deferred_goal` arg in `build_planner_submission()` becomes `next_iteration_handoff_goal`; `PlannerSubmission` DTO field rename |
| `backend/src/tools/submission/planner/submit_plan_continues_goal.py` | terminal tool | Param rename `deferred_goal` → `next_iteration_handoff_goal`; field on `SubmitPlanContinuesGoalInput` rename; tool description string rewrite |
| `backend/src/tools/submission/planner/submit_plan_closes_goal.py:51` | terminal tool | Internal call `deferred_goal=None` rename |
| `backend/src/task_center/context_engine/recipes/{generator,evaluator,attempt_landscape,goal_iteration_frame,planner}.py` | recipes | Field reads updated to new names; metadata keys updated; `iteration_sequence_no` kwarg in `planner_instruction()` becomes `iteration_no` |
| `backend/src/task_center/context_engine/recipes/role_instruction.py` | role instruction text | Full rewrite per §7.2 |
| `backend/src/task_center_runner/agent/mock/runner.py:312` | mock planner-arg emission (boundary translator) | Today translates `spec.args.get("plan_spec", "")` → emits `"task_specification"` dict key. After Phase 1 the translation collapses (both sides become `plan_spec`). After Phase 2 also rename emission `"deferred_goal"` → `"next_iteration_handoff_goal"`. |
| `backend/src/task_center_runner/audit/recorder.py:81-82,98,102` | audit recorder dict keys (ORM-column passthrough) | **Correction (per Architect re-review):** the recorder is NOT a DTO translator — it reads `record.task_specification` / `record.deferred_goal` directly off the SQLAlchemy mapped attribute (`db/models/{iteration,attempt}.py`). Per Pre-mortem #1, those ORM attribute names stay stable. **Policy: Option B — keep audit dict keys stable** (`"task_specification"`, `"deferred_goal"`) for this PR. Rationale: audit artifacts are forensic/archival; stability across versions is the feature. Bind audit-key rename to **FU-2** (same PR as the DB-column migration) so the rename is atomic with the change that breaks column-stability. Add a code comment at the read sites: `# DB column name pinned by ADR; intentional read of legacy name from stable ORM attribute.` |
| `backend/src/task_center_runner/core/runner.py:117,129` | runtime dict keys (ORM-column passthrough) | Same shape as audit-recorder. Apply Option B (keep-stable, bind rename to FU-2). |
| `backend/src/task_center_runner/scenarios/_utils/plans.py:51,69` + `scenarios/full_case_user_input.py:166,203,234,334,409,431,432` + `scenarios/full_stack_adversarial.py:200,253,354,401,504,527,528` + `scenarios/correctness_testing.py` + `scenarios/__init__.py` + `tests/test_capacity_scenario_packs.py` + `tests/sweevo/*` | scenario fixture kwargs and dict keys | Pure rename after Phase 2 finalises the tool-schema break. Mechanical; 32 call sites total per `grep -rn "deferred_goal" backend/src/task_center_runner/`. |
| `backend/src/task_center_runner/scenarios/**` and `task_center_runner/tests/**` (residual) | other scenario test fixtures + format snapshots | Mechanical rename + format-snapshot updates for anything not enumerated above. |

### Rendering-format surface

| File | Action |
|---|---|
| `backend/src/task_center/context_engine/renderer.py` | Rename `MarkdownPromptRenderer` → `XmlPromptRenderer` in place; rewrite `_render_block` (renderer.py:133-139) and `_render_group` (renderer.py:142-147) to emit `<tag>...</tag>` and nested children respectively; rewrite `_DEFAULT_HEADINGS` → `_DEFAULT_TAGS`; **delete `_humanize()` and `_heading_for()`**; add `_tag_for()` that raises `ContextEngineError` on missing `ContextBlockKind` → tag mapping; **remove `.strip()` at renderer.py:83, 97, 138, 146** (verbatim contract is renderer-global, not block-kind-scoped); preserve compression / budget policy unchanged. |
| `backend/src/task_center/context_engine/core.py:28,114,124` | Import + instantiation site renamed; otherwise no behavior change. |
| `backend/src/task_center/context_engine/packet.py` | `ContextBlockKind` enum unchanged (kinds map to tag names in renderer). |
| `backend/src/task_center/context_engine/recipes/goal_iteration_frame.py` | `GOAL_HEADING` / `CURRENT_ITERATION_HEADING` constants become `GOAL_TAG="goal"` / `CURRENT_ITERATION_TAG="current_iteration"`; metadata key `"heading"` becomes `"tag"`; `"group_heading"` becomes `"group_tag"`; `"subheading"` becomes `"child_tag"`. |
| `backend/src/task_center/context_engine/recipes/attempt_landscape.py:84-112` | `_render_failed_attempt()` rewritten to emit nested XML (`<attempt_plan><plan_spec>...</plan_spec><next_iteration_handoff_goal>...</next_iteration_handoff_goal></attempt_plan>`) — see §7.1 evaluator-judgment bypass case. |

---

## 7. The new format spec (target end state)

### 7.1 user_message1 — XML rendering by agent and case

#### Planner — Case A: iteration 1, no failed attempts (fresh start)

```xml
<goal_current_iteration>
{iteration.goal text verbatim, including any user-supplied inner tags
like <pr_description>...</pr_description>}
</goal_current_iteration>
```

#### Planner — Case B: iteration 1 with failed attempts (in-iteration retry)

```xml
<goal_current_iteration>
{iteration.goal text verbatim}
</goal_current_iteration>

<iteration iteration_no="1" status="current">
  <attempt attempt_no="1" status="failed">
    <attempt_plan>
      <plan_spec>{attempt 1 plan_spec}</plan_spec>
      <next_iteration_handoff_goal>{if present}</next_iteration_handoff_goal>
    </attempt_plan>
    <generator_outcomes>
      <status_summary>
      task_a: passed
      task_b: failed
      </status_summary>
      <task id="task_a" status="passed">{task_a summary}</task>
      <task id="task_b" status="failed">{task_b summary}</task>
    </generator_outcomes>
    <evaluator_judgment status="bypassed" reason="generator_failed">
    Evaluator skipped because generator task(s) failed: task_b.
    </evaluator_judgment>
  </attempt>
</iteration>
```

#### Planner — Case C: iteration N (N≥2), no failed attempts (continuation, fresh)

```xml
<goal>
{original goal text}
</goal>

<iteration iteration_no="1" status="prior">
  <accepted_plan>{prior accepted plan_spec verbatim}</accepted_plan>
  <summary>{prior iteration summary}</summary>
</iteration>

<iteration iteration_no="2" status="prior">
  <accepted_plan>...</accepted_plan>
  <summary>...</summary>
</iteration>

<iteration iteration_no="3" status="current">
  <iteration_goal>{current iteration goal text}</iteration_goal>
</iteration>
```

#### Planner — Case D: iteration N (N≥2) with failed attempts (full case)

Same as Case C, with `<attempt attempt_no="..." status="failed">` blocks appended inside `<iteration status="current">`. Each failed attempt uses the structure from Case B.

#### Generator — Case A: assigned task with no deps

```xml
<attempt_plan>
  <plan_spec>{attempt.plan_spec}</plan_spec>
  <next_iteration_handoff_goal>{if continues-goal}</next_iteration_handoff_goal>
</attempt_plan>

<assigned_task task_id="{task_id}">
{task.context_message — local task spec for this generator}
</assigned_task>
```

#### Generator — Case B: assigned task with deps

Case A + a `<dependency_results>` block between `<attempt_plan>` and `<assigned_task>`:

```xml
<dependency_results>
  <dependency id="task_a">{task_a latest summary}</dependency>
  <dependency id="task_b">{task_b latest summary}</dependency>
</dependency_results>
```

#### Evaluator — Case A: iteration 1, closes-goal attempt

```xml
<goal_current_iteration>
{iteration.goal text}
</goal_current_iteration>

<attempt_plan>
  <plan_spec>{attempt.plan_spec}</plan_spec>
</attempt_plan>

<completed_tasks>
  <task id="task_a" status="passed">{task_a summary}</task>
  <task id="task_b" status="passed">{task_b summary}</task>
</completed_tasks>

<evaluation_criteria>
- criterion 1
- criterion 2
</evaluation_criteria>
```

#### Evaluator — Case B: iteration 1, continues-goal attempt

Case A + `<next_iteration_handoff_goal>` child inside `<attempt_plan>`.

#### Evaluator — Case C: iteration N (N≥2), closes-goal or continues-goal

Same as Case A/B, with the multi-iteration `<goal>` + `<iteration status="prior">` × N + `<iteration status="current">` frame replacing the single `<goal_current_iteration>`.

### 7.2 user_message2 — role instruction with `<tag>` mention pattern

Replace every heading-text reference with the tag mention. Examples (full text in §8.4):

| Old text | New text |
|---|---|
| "see Prior Failed Attempts" | "see `<attempt status=\"failed\">` blocks inside `<iteration status=\"current\">`" |
| "see Dependency Results" | "see `<dependency_results>`" |
| "see Previous Iteration Results" | "see `<iteration status=\"prior\">` blocks" |
| "see Partial Plan Boundary" | "see `<next_iteration_handoff_goal>` inside `<attempt_plan>`" |
| "submit a partial plan with a deferred_goal" | "submit a continues-goal plan with a `next_iteration_handoff_goal`" |
| "use the Attempt Plan and the Evaluation Criteria as your authority" | "use `<attempt_plan>` and `<evaluation_criteria>` as your authority" |

**Test invariant (tightened per Architect review):** for each role-instruction branch B, the tags mentioned in B's text must be present in the rendered output **under B's matching context conditions**. Parameterize the test by the same branching axes as `role_instruction.py`:
- planner: `(iteration_sequence_no ∈ {1, ≥2}) × (has_failed_attempts ∈ {True, False})` — 4 branches at `role_instruction.py:42-105`
- generator: `has_deps ∈ {True, False}` — 2 branches at `role_instruction.py:109-129`
- evaluator: `is_partial ∈ {True, False}` — 2 branches at `role_instruction.py:132-154`

A naive "appears in at least one branch" check would pass spuriously when, e.g., the no-deps generator branch references `<dependency_results>` (which the recipe only emits when `needs` is truthy at `generator.py:60-65`). Enforced by `test_role_instruction_tag_consistency.py` parameterized on the branch axes above.

---

## 8. Execution plan (commit boundaries)

### Phase 0 — Branch and baseline (no behavior change)

- Branch: `xml-context-rename`
- Run `make test` on `main`; record baseline pass count + any flakes.
- **Capture per-recipe token-estimate baseline** using `renderer._estimate_tokens` for the four planner cases × generator (2 cases) × evaluator (2 cases). Persist to `docs/reports/initial_messages_cases/_baseline_tokens.json` so the §9 observability "<15% bloat" check (line 342) is executable against a recorded baseline rather than a redo-on-the-fly value.
- Commit `.0`: this plan document at `docs/reports/ralplan_xml_context_format.md` + the baseline-tokens JSON.

### Phase 1 — Rename `task_specification` → `plan_spec` end-to-end

DTO + persistence + recipes + tests in one commit:

1. Rename field on `Attempt`, `Iteration`, `IterationProjection`, `PlannerSubmission` dataclasses.
2. Update `_core/persistence.py` row mapper: keep DB column `task_specification` (translate in `_row_to_attempt` / `_row_to_iteration` mappers) OR rename column (decide in Phase 1 review — recommend keep column for migration-safety per pre-mortem #1).
3. Update all recipe reads (`attempt.task_specification` → `attempt.plan_spec`).
4. Update `build_planner_submission` parameter name; remove the LLM-boundary-only alias comment in `_schemas.py`.
5. Run `make test`; expect mechanical pass after one round of test fixture renames.

Acceptance: `grep -rn "task_specification" backend/src/` returns only DB-column references (in persistence row mappers).

### Phase 2 — Rename `deferred_goal` → `next_iteration_handoff_goal` end-to-end

Same shape as Phase 1:

1. Rename on `Attempt`, `Iteration`, `IterationProjection`, `PlannerSubmission`, `SuccessContinue` payload.
2. Rename `set_deferred_goal` method on persistence + iteration manager.
3. Rename `submit_plan_continues_goal` parameter and `SubmitPlanContinuesGoalInput` field; update tool description string to use the new name and the "risky to complete in one iteration" semantic. **Tool-schema break policy** (resolved per Architect review): the rename is a hard break — the new param is `next_iteration_handoff_goal`; the old `deferred_goal` is not accepted. Justification: no agent run spans the deploy boundary in this harness (each attempt is a fresh process spawn reading the current tool schema at startup), so there are no "in-flight agents" to worry about. If that assumption is later invalidated (e.g. persistent agent sessions), revisit by adding `Field(alias="deferred_goal")` for one release with a deprecation log.
4. Update all recipe reads.
5. Update `planner_instruction` text in role_instruction.py to use new name (text-only edit, no logic).
6. Run `make test`; mechanical pass.

Acceptance: `grep -rn "deferred_goal" backend/src/` returns only DB-column references.

### Phase 3 — Renderer: markdown → XML

The behavior change commit:

1. Rewrite `renderer.py`:
   - `MarkdownPromptRenderer` → `XmlPromptRenderer` (rename class — in-place, no co-existence).
   - `_render_block`: `<{tag}>{text}</{tag}>` instead of `# {heading}\n\n{text}`.
   - `_render_group`: nested children with the parent tag wrapping siblings.
   - `_DEFAULT_HEADINGS` dict → `_DEFAULT_TAGS` dict mapping `ContextBlockKind` → tag name (e.g. `goal_statement` → `"goal"`, `iteration_statement` → `"current_iteration"`).
   - **Delete the fallback ladder.** `_humanize()` (renderer.py:40-41) and `_heading_for()` (renderer.py:44-49) are deleted; replace with a single `_tag_for(block, tags) -> str` that reads `block.metadata.get("tag") or tags.get(block.kind)` and raises `ContextEngineError(f"No tag mapping for kind {block.kind!r}")` on miss. Half-migrated recipes fail loudly rather than emitting markdown headings.
   - **Delete the `subtitle` branch** at renderer.py:135-137. No recipe currently writes `metadata["subtitle"]` (verified via grep); the branch is dead code. Removing it now prevents accidental revival under the new tag-attribute model.
   - **Verbatim body contract (per Principle 3):** the renderer wraps `block.text` *verbatim* — no `.strip()`, no whitespace normalization, no newline reflow. All current `.strip()` sites are removed: `renderer.py:97` (`render_role_instruction`), `renderer.py:138` (`_render_block`), `renderer.py:146` (`_render_group`), and `renderer.py:83` (the trailing-strip on the joined packet). Verbatim contract is renderer-global, not block-kind-scoped. Unit test: a body whose leading whitespace is semantically meaningful (fenced code block; `pr_description` with leading indentation) round-trips byte-for-byte.
2. Rewrite `goal_iteration_frame.py`:
   - `GOAL_HEADING` etc. constants → tag-name constants.
   - Iteration 1 single-block emits `<goal_current_iteration>`; iteration N+ emits `<goal>` + `<iteration status="prior">` blocks + `<iteration status="current"><iteration_goal>...`.
   - Block metadata changes: `"heading"` → `"tag"`, `"group_heading"` → `"group_tag"`, `"subheading"` → `"child_tag"`. Attributes go in `"attrs"` dict.
3. Rewrite `attempt_landscape.py`:
   - `_render_failed_attempt` emits nested XML.
   - Generator-failure case emits `<evaluator_judgment status="bypassed" reason="generator_failed">…</evaluator_judgment>`.
   - Evaluator-failure case emits `<evaluator_judgment status="ran" verdict="fail">…</evaluator_judgment>`.
4. Rewrite `evaluator.py`:
   - Remove `PARTIAL_PLAN_BOUNDARY` block emission (today at `evaluator.py:61-79`). The structural signal moves to `<next_iteration_handoff_goal>` nested inside `<attempt_plan>`, AND the behavioral guidance ("do not penalize for incomplete work that was explicitly deferred") is preserved by the surviving `evaluator_instruction(is_partial=True)` branch at `role_instruction.py:138-145`. The dropped block was duplicating prose that already lives in the role-instruction layer; nothing semantic is lost.
   - **Also remove the `PARTIAL_PLAN_BOUNDARY` enum entry from `ContextBlockKind`** (`packet.py:38`). No emitter, no consumer, no plan to revive it — keeping a dead enum value in a public enum invites confusion. Internal-only import surface so the removal is safe; if any external code imports it, that import is dead by construction and should also be removed.
   - `attempt_plan` block emission has nested children.
5. Rewrite `generator.py`:
   - `attempt_plan` block with nested children.
   - `<dependency_results>` / `<assigned_task>` tag names.
6. Snapshot regen: every recipe test under `backend/tests/unit_test/test_task_center/test_context_engine/` regenerates (contains `test_renderer.py`, `test_attempt_landscape.py`, `test_recipes_planner.py`, etc.).

Acceptance: `tests/` pass; manual transcript inspection of the 11 files in `docs/reports/initial_messages_cases/` shows expected XML shape.

### Phase 4 — Role instructions: `<tag>` mention pattern

1. Rewrite text in `role_instruction.py` per §7.2. **Preserve semantic content, not just headings.** The rewrite is a heading-to-tag *mention* swap; it must NOT lose substantive sentences. Specifically, the `evaluator_instruction(is_partial=True)` text at `role_instruction.py:138-145` becomes the *single source of truth* for partial-plan semantics after Phase 3 step 4 drops the `PARTIAL_PLAN_BOUNDARY` block. The sentence *"This attempt is not expected to solve the full iteration goal — it is expected to make progress and hand off remaining work via deferred_goal"* must survive (with `deferred_goal` renamed to `next_iteration_handoff_goal`); the sentence *"do not penalize for incomplete work that was explicitly deferred"* must survive verbatim. Add a regression test asserting both substrings appear in the partial branch's rendered text.
2. Add `test_role_instruction_tag_consistency.py` (parameterized per §7.2):
   - For each role × each branch B, build a packet that triggers branch B's context conditions, render it, parse tag names from both the role-instruction text and the rendered context, and assert every tag mentioned in B's text appears in the render under B's conditions.
3. Update `planner_instruction` kwarg from `iteration_sequence_no` → `iteration_no` for consistency with the rendered attribute name (and matching field rename, optional but recommended).

Acceptance: new tag-consistency test passes; existing planner / evaluator e2e scenarios pass.

### Phase 5 — Documentation + cleanup

1. Update `backend/src/task_center/context_engine/__init__.py` docstring.
2. Update any references in `docs/` that mention markdown heading format.
3. Delete dead `_DEFAULT_HEADINGS` references if any survive.
4. Run `ruff check` and `make lint`.

Acceptance: `make test` and `make lint` clean.

---

## 9. Test strategy (deliberate-mode expansion)

### Unit
- `test_renderer_xml.py`: every block kind, with/without group, attribute escaping, empty body.
- `test_goal_iteration_frame_xml.py`: iter1 vs iterN; verify tag names + attributes.
- `test_attempt_landscape_xml.py`: failed attempt with bypassed evaluator vs evaluator-ran-failed; multiple attempts ordering.
- `test_planner_submission_rename.py`: schema accepts new field names; backward-incompatible rejection of old names with clear error.
- `test_renderer_tag_escape.py`: cover each `_DEFAULT_TAGS` closer substring as a planted hostile body — `</goal>`, `</attempt_plan>`, `</iteration>`, `</plan_spec>`, `</next_iteration_handoff_goal>`, `</evaluation_criteria>`, `</completed_tasks>`, `</dependency_results>`, `</assigned_task>`. Highest-blast-radius case is `</attempt_plan>` planted inside a failed-attempt's `plan_spec` body (planner Case D), because that closer would prematurely close the wrapper *and* leak the failed-attempt's content into the iteration-level scope. Assert raise + verify the error message contains all three required parts (closer, source_id, remediation hint).

### Integration
- `test_planner_recipe_iter1.py`, `test_planner_recipe_iter1_failed.py`, `test_planner_recipe_iterN.py`, `test_planner_recipe_iterN_failed.py`: all four planner cases produce the §7.1 shape exactly.
- `test_generator_recipe_with_deps.py`, `test_generator_recipe_no_deps.py`: both generator cases.
- `test_evaluator_recipe_closes.py`, `test_evaluator_recipe_continues.py`: both evaluator cases.

### E2E (existing scenarios)
- `task_center_runner/scenarios/pipeline/initial_goal.py` (iter1 closes-goal happy path)
- `task_center_runner/scenarios/pipeline/iterative_continuation.py` (multi-iteration handoff)
- `task_center_runner/scenarios/pipeline/attempt_retry_planner_failure.py` (failed attempts visible to next planner)
- `task_center_runner/scenarios/pipeline/attempt_retry_generator_failure.py` (bypassed evaluator)

### Observability
- Capture before/after `message.jsonl` for `initial_messages_capture.py` scenario; diff stored under `docs/reports/initial_messages_cases_xml/` for future reference.
- One assertion in the renderer test: total token estimate for typical iter-3-with-failures packet must not exceed pre-migration estimate by >15%.

---

## 10. ADR

**Decision.** Adopt XML-tagged context rendering across all recipe outputs; rename `task_specification` → `plan_spec` and `deferred_goal` → `next_iteration_handoff_goal` end-to-end (DTO, persistence DTO, recipe code, terminal tools); keep DB column names unchanged to avoid migration risk.

**Decision drivers.**
1. LLM legibility under long context (closure problems with markdown today).
2. Closed loop between planner's terminal-tool params and the rendered tag names every agent reads.
3. Semantic clarity in the field name itself: `next_iteration_handoff_goal` communicates "this iteration was too risky for one shot" without requiring a separate explanation.

**Alternatives considered.**
- *Option B (renderer-only XML, no rename)* — rejected: under-delivers on user request; keeps the existing submission/persistence divergence.
- *Option C (dual-format with feature flag)* — rejected: violates one-renderer principle; flag becomes permanent in practice; doubles maintenance.

**Why chosen.** Option A delivers all three requirements in one coherent migration boundary. The pre-mortem identifies the persistence rename as the only real risk; the mitigation (rename in Python, not in DB) eliminates it. Test snapshot churn is mechanical and bounded.

**Consequences.**
- All recipe tests regenerate snapshots once.
- Existing recorded `message.jsonl` artifacts (e.g. `docs/reports/initial_messages_cases/`) become historical; new captures supersede.
- Planner system prompts and tool descriptions must use the new field names — agents in flight when this lands will see new params; no graceful degrade.
- Any downstream consumer of `.planning/` artifacts that reads Python dataclasses (not raw DB rows) needs a one-line rename in their code.
- **Rollback is a clean `git revert` of the merge commit.** The persistence layer is column-stable (keeps `task_specification` / `deferred_goal` DB column names), so reverting code does not corrupt any on-disk state. Audit / message.jsonl artifacts written *during* the new code's deployment carry the new key names; reverters of post-merge data can re-translate via the `_to_dto` mapper or accept the schema break point as a forensics boundary.

**Follow-ups (tracked, not in this plan).**

| ID | Item | Trigger condition | Owner |
|---|---|---|---|
| FU-1 | Audit `docs/` for stale references to markdown headings; update `docs/reports/initial_messages_cases/` snapshots to reflect XML format | This PR merges to `main` | TBD |
| FU-2 | Rename persistence DB columns `task_specification` → `plan_spec` and `deferred_goal` → `next_iteration_handoff_goal` via Alembic migration; remove the row-mapper translation seam in `_to_dto` | One release of stable XML rendering on `main` with no rollback signal (≥2 weeks production-equivalent runtime) | TBD |
| FU-3 | Rename `iteration.sequence_no` Python field → `iteration_no` for symmetry with the rendered tag attribute name and `attempt_no` rename | Cosmetic; bundle with the next iteration-related refactor | TBD |

---

## 11. RALPLAN-DR summary

**Principles (5).**
1. One renderer, no per-agent format drift.
2. LLM-facing names match terminal-tool names.
3. Body content stays verbatim.
4. Asymmetric structure by lifecycle status.
5. Migration is one-shot.

**Decision drivers (3).**
1. LLM legibility under long context.
2. Field-name coherence submission ↔ rendering.
3. Recipe simplicity.

**Viable options (3).**
- A: full XML + full rename (chosen).
- B: renderer-only XML, no rename (rejected — under-delivers).
- C: dual format via feature flag (rejected — violates Principle 5).

**Pre-mortem (3 scenarios + mitigations).** See §5.

**Expanded test plan.** See §9 (unit/integration/e2e/observability).
