# Skill-as-Row-4 — Resolutions for Q1/Q2/Q3

**Status:** Approved (ralplan consensus — 2 iterations, Architect + Critic APPROVE)
**Date:** 2026-05-18
**Parent doc:** `docs/design/skill_fourth_message.md`
**Scope:** Resolves the three open architectural questions left by the parent doc.

---

## Headline — three artifacts, three concerns, no overlap

Every other question falls out of this map. Q3 (binary-terminal-choice ownership) is the lever that answers Q1 and Q2.

| row | artifact | concern | source of truth |
|---|---|---|---|
| 1 | `<role>.md` system prompt | identity | `agents/profile/main/<role>.md` |
| 3 | terminal catalog | binary terminal-selection rule | `tools/_terminals/registry.py` → `render_terminal_catalog(focus="selection_guidance")` (and future `focus="decision_tree"` for Layer 2) |
| 4 | skill | workflow process (HOW to reach the binary moment) | file named in resolved `AgentDefinition.skill` field |

Skills describe trigger conditions ("you've verified the deliverable exists at the claimed location — you're ready to evaluate the terminal options above"). Skills **never name terminals**. The catalog (row 3) is the only source of terminal-selection prose, rendered from the registry that defines them.

---

## Q1 — Skills resolve through the agent resolver (Option D)

`AgentDefinition` frontmatter gains an optional `skill:` field. `RuleBasedAgentResolver.resolve()` returns `AgentSelection.skill_path` populated from `selection.agent_def.skill`. The composer builds a `ConversationMessage`; the launcher applies the row-4 inversion. **Zero new predicate machinery.** Variant routing and skill resolution share one resolver pass.

### Variant supersession rule (explicit)

When a variant matches, the variant target's `AgentDefinition` supersedes the base completely — same rule as `terminals`, `allowed_tools`, `context_recipe`. The variant target's `skill` value (including `None`) wins.

**Foot-gun note for skill authors:** adding a variant for a scope drops the base's `skill` for that scope unless you also declare `skill:` on the variant target. This is intentional consistency with how `terminals` and `allowed_tools` already behave — variant targets are full agent definitions, not partial overlays. If you want the base's skill to apply to a variant scope, declare it explicitly on the variant target too.

### Schema reserved at v1 for future fan-out

```yaml
# v1 supported form
skill: skills/planner_default.md

# v2 reserved form (raises NotImplementedError at agent-definition load in v1)
skill_variants:
  - when: <predicate_name>
    use: skills/planner_continuation.md
  - when: always
    use: skills/planner_default.md
```

`skill_variants:` is a typed field on `AgentDefinition` (so Pydantic's `extra="forbid"` does not reject it as unknown), with a load-time validator that raises `NotImplementedError("skill_variants: is reserved for v2; v1 only supports skill: <path>")`. Reserving the schema now means a future workflow-only axis (e.g., "retry-aware planner" at the same depth as a fresh planner) does not force a synthetic agent variant or churn every existing frontmatter at v2.

### Axis-coincidence assumption (explicit caveat)

Option D's "variant routing and skill resolution are the same decision" claim holds **only because today's variant axis (nested-goal depth) coincides with the workflow axis** (different depths → different terminal sets → different workflow framings). The first workflow-only axis (a workflow distinction that does not change `terminals`, `allowed_tools`, or `context_recipe`) breaks this. When it surfaces, promote that role to `skill_variants:` list form rather than introducing a synthetic agent variant whose only purpose is differing skill text.

---

## Q2 — Two channels, both lint-gated; community skills via Channel B only

| channel | shape | trust | lint |
|---|---|---|---|
| **A — row 4 auto-injection** | `skill:` / `skill_variants:` frontmatter on `AgentDefinition`; loaded once at process start; injected as row 4 of every main-agent launch | harness-team-owned | startup scan: reject any file with `submit_*` substring OR any `TERMINAL_DESCRIPTORS` key as substring; raise on missing file |
| **B — `load_skill` on-demand tool** | `skills/bundled/` registry; agent calls `load_skill(skill_name)` tool mid-run; SKILL.md returned as `ToolResult` | community / 3rd-party | `SkillRegistry.register()` runs the same `submit_*` + `TERMINAL_DESCRIPTORS` key scan at load time; raise on violation |

**Terminal-contract enforcement** is in code paths, not skill prose:
- Tool dispatch rejects any unknown terminal name (community skill mentioning `submit_final_answer` fails at the runner because the tool doesn't exist).
- Row 3 catalog stays in the static launch prefix as the authoritative submission instruction.
- Advisor pre-submission discipline (row 3 prescribes `ask_advisor` before any submission) is the backstop.

**Residual risk (acknowledged):** community skills may paraphrase terminal semantics in natural language ("the close-iteration submission") undetected by the substring/key lint. A free-form paraphrase denylist was considered and **withdrawn** — it produces false positives on legitimate trigger-teaching prose and false negatives on reworded phrases. Advisor pre-submission discipline is the named backstop; an explicit example-bank-based detector is **out of scope for v1** and tracked as a follow-up if real incidents surface.

---

## Q3 — Binary terminal choice (answered in headline)

The binary terminal choice lives in row 3 (catalog), not row 4 (skill), not row 1 (system prompt). Putting it in row 4 would duplicate `TERMINAL_DESCRIPTORS.selection_guidance` (guaranteed drift). Putting it in row 1 mixes identity with selection rules (the design doc's stated "drift risk" problem).

The variant system handles cases where binary becomes unary (`planner_full_only`, `executor_success_failure` at deep depth) by narrowing the catalog itself — the skill doesn't need to know.

Layer 2 (`render_terminal_catalog(focus="decision_tree")`) is the follow-up that expands selection rules into an explicit "A vs B" decision tree, still inside row 3 with the rest of the terminal contract.

---

## Principles
1. **One source of truth per concern.** Identity (row 1) / terminal contract (row 3) / workflow (row 4) live in three disjoint artifacts.
2. **Single resolution path for main agents.** Skills resolve through `RuleBasedAgentResolver`; zero new predicate machinery. **Scope:** helpers (advisor, resolver) and subagents (explorer) bypass this resolver and need separate plumbing if they ever ship skills; helper-skill story explicitly deferred to v2.
3. **Terminal authority non-negotiable in code paths.** Both channels are lint-gated against `submit_*` substrings and `TERMINAL_DESCRIPTORS` keys. Residual natural-language paraphrase risk accepted; advisor backstops.
4. **Two channels, two trust models.** Channel A is harness-owned, lint-checked, pre-injected. Channel B is community-owned, lint-checked at load, agent-driven.
5. **Backwards compatibility via no-op default.** Agents without `skill:` keep the 3-row launch shape.

## Decision drivers
1. **Avoid parallel predicate machinery.** The doc's Option B introduces a separate skill loader; today's variant axis and workflow axis coincide, so D collapses them onto one resolver — fewer moving parts.
2. **Match the user's mental model.** "planner_v1 vs planner_v2" IS the variant concept; treating skill as a variant field makes the routing decision and the workflow decision the same decision.
3. **Community-skill compatibility is hard-required.** Channel separation + bilateral lint is the only design that preserves the terminal contract while letting community skills augment knowledge.

## Viable options

| option | shape | pros | cons | verdict |
|---|---|---|---|---|
| A — File-per-variant | `planner/skill.md`, `planner_full_only/skill.md`, `executor_success_handoff/skill.md`, … | trivial mental model; no machinery | ~80% duplication between variants of one role; drift on shared workflow text | rejected |
| B — Doc's current: skill-per-role + parallel `applies_when` loader | one file per role; separate loader walks them | one file per role; predicate-driven | parallel resolution path; doc's `applies_when` example keys don't exist on `ContextScope`; even fixed to `ResolverContext → bool`, it becomes a redundant pass of identical machinery | rejected |
| **D — Skill as agent-frontmatter field, single resolver path (recommended)** | `skill:` string for v1; `skill_variants:` list reserved for v2; resolver returns it alongside `agent_def` | zero new predicate machinery; variant routing and skill resolution unified for v1; frontmatter is the unit of authority; collocated by role; schema reserved for axis divergence | skill cannot be shared across variants without duplication (mitigation: `include:` transclude deferred); skill text edits touch agent frontmatter (small) | recommended |

---

## Pre-mortem (3 scenarios — deliberate mode)

1. **Terminal-name leak via copy-paste (both channels).** Author pastes a paragraph mentioning `submit_plan_closes_goal` into a new skill. Mitigation: bilateral lint (`submit_*` substring + `TERMINAL_DESCRIPTORS` keys); CI contract test scanning both channels; CODEOWNERS rule on `agents/profile/main/skills/*.md` and `skills/bundled/*/SKILL.md`.
2. **Workflow-only axis emerges and pollutes routing taxonomy.** Engineer needs a "retry-aware" planner at the same depth as a fresh planner. Option D as scoped would force a synthetic agent variant. Mitigation: `skill_variants:` schema reserved at v1; predicate uses existing `PredicateRegistry` so adding the new path is a one-line frontmatter change + new skill file + lifting the `NotImplementedError`, no resolver changes.
3. **Community skill paraphrases a terminal undetected.** Skill says "the close-iteration submission" without typing `submit_*`. Mitigation: tool dispatch rejects unknown tool names (community skill names cannot subvert runner); row 3 stays loaded as authoritative; advisor pre-submission discipline catches non-registered terminal names. Residual risk acknowledged; explicit example-bank detector tracked as follow-up.

---

## Files touched

| file | change |
|---|---|
| `backend/src/agents/definition/model.py:91-184` | add `skill: Path \| None = None` and `skill_variants: list[AgentSkillVariant] \| None = None` fields; add new `AgentSkillVariant` dataclass mirroring `AgentVariant`; add validator that raises `NotImplementedError` when `skill_variants` is non-None |
| `backend/src/agents/definition/loader.py:18-44` | resolve `skill:` relative to each source file's parent directory; store absolute `Path` on `AgentDefinition.skill`; raise on missing referenced file at load |
| `backend/src/task_center/_core/agent_routing.py:144-152` | add `skill_path: Path \| None = None` to `AgentSelection` (frozen + slots; additive default is safe) |
| `backend/src/task_center/_core/agent_routing.py:165-170` | base fast-path: populate `AgentSelection.skill_path` from `base.skill` |
| `backend/src/task_center/_core/agent_routing.py:194-208` | `_select_variant_target`: populate `AgentSelection.skill_path` from `target.skill` (target's `None` supersedes base's value) |
| `backend/src/task_center/_core/agent_routing.py:178-181` | "variants exist but none match" fallback: populate `AgentSelection.skill_path` from `base.skill` |
| `backend/src/task_center/context_engine/core.py:87-104` | add `skill_message: ConversationMessage \| None = None` to `LaunchBundle` |
| `backend/src/task_center/context_engine/core.py:125-155` | composer builds `skill_message` when `selection.skill_path` is set via new `build_skill_message(skill_path)` helper: reads file, strips frontmatter, returns `ConversationMessage(role="user", content=[TextBlock(text="Base directory for this skill:\n<abs_path>\n\n<body>")])` |
| `backend/src/task_center/attempt/runtime.py` (`AgentLaunch`) | add `skill_message: ConversationMessage \| None = None` field |
| `backend/src/task_center/attempt/launch.py:141-145` | apply skill inversion inside the `if role_instruction:` branch: when `skill_message` is present, it becomes `runner_prompt`; `runner_initial_messages = [context_message, role_instruction_message]`. Entry-executor `else` branch (lines 146-148) untouched. |
| `backend/src/agents/skills/loader.py` | **NEW** — Channel A startup lint: scans `AgentDefinition.skill` paths; `submit_*` substring + `TERMINAL_DESCRIPTORS` key scan |
| `backend/src/skills/core/loader.py` (or `SkillRegistry.register()` at `skills/core/registry.py`) | extend with `submit_*` substring + `TERMINAL_DESCRIPTORS` key scan at load time (Channel B lint) |
| `backend/src/agents/profile/main/planner.md` | declare `skill: skills/planner_default.md` |
| `backend/src/agents/profile/main/executor_success_handoff.md` | declare `skill: skills/executor_handoff_default.md` |
| `backend/src/agents/profile/main/skills/planner_default.md` | **NEW** Phase 5 — first planner workflow skill (no `submit_*` substrings) |
| `backend/src/agents/profile/main/skills/executor_handoff_default.md` | **NEW** Phase 5 — first executor workflow skill |
| `backend/src/agents/tests/test_skill_resolver.py` | **NEW** — base fast-path, variant-target path, variants-exist-none-match fallback, variant-target's `None` supersedes base, no-skill returns None, `skill_variants:` raises `NotImplementedError` at load |
| `backend/src/agents/tests/test_skill_message.py` | **NEW** — prefix format, frontmatter stripped, `ConversationMessage` shape |
| `backend/src/agents/tests/test_skill_lint.py` | **NEW** — Channel A startup lint; Channel B `SkillRegistry.register` lint |
| `backend/src/task_center_runner/tests/sweevo/test_first_three_messages_capture.py` | edit — assert 4 rows when `skill:` is registered; assert 3-row baseline when absent |
| `scripts/build_first_three_messages_report.py` | edit — render row 4 |
| `backend/src/message/agent_message_recorder.py` | **no change required** — `record_initial_messages(seeded_initial_messages)` at lines 116-148 is generic over message count; row 4 emerges from the launcher's seeded-initial-messages contract |
| `backend/src/tools/ask_helper/ask_advisor.py`, `ask_resolver.py`, `tools/subagent/run_subagent.py` | **no change required** — helper / subagent skill plumbing deferred to v2 |
| `docs/design/skill_fourth_message.md` | edit — supersede AC#6 (helper-shape) per this resolution; cite this addendum |

---

## Expanded test plan

- **Unit — frontmatter & loader:** `AgentDefinition` parses `skill:` string into `Path` (absolutized at load); `skill_variants:` list raises `NotImplementedError` at load; missing skill file raises at load.
- **Unit — resolver:** five tests covering all three return paths + variant-target supersession + no-skill case (enumerated in `test_skill_resolver.py`).
- **Unit — composer:** `build_skill_message` produces exact prefix; frontmatter stripped; returns `ConversationMessage` with `role="user"` and one `TextBlock`.
- **Unit — Channel A lint:** loader at startup raises on `submit_*` substring; raises on any `TERMINAL_DESCRIPTORS` key as substring; passes clean files.
- **Unit — Channel B lint:** `SkillRegistry.register()` raises on same patterns; tested with fixture skills containing each violation type.
- **Integration:** `test_attempt_launcher_skill.py` — 3-row launch when no skill, 4-row launch when skill; helper / subagent paths assert unchanged shape (negative test).
- **E2E:** `pipeline.first_three_messages_capture` confirms `message.jsonl` records 4 rows for planner/executor when skill files registered; row 4 identifiable by prefix; no regressions when absent.
- **Observability:** log `skill_resolved` event with `skill_path` and `variant_used` at attempt launch via `audit/recorder.py`; add `skill_id` to `AgentLaunch` dataclass for forensic queries.
- **Contract test:** snapshot scans every shipped skill (both channels) for `TERMINAL_DESCRIPTORS` keys; fails CI on any leak.

---

## Acceptance criteria (testable)

1. With no `skill:` field on any agent frontmatter, every existing test passes — fully backwards compatible.
2. With `planner.md` and `executor_success_handoff.md` declaring `skill:`, `pipeline.first_three_messages_capture` runs green; `message.jsonl` for planner + executor agents contains exactly 4 initial rows.
3. Row 4 starts with `Base directory for this skill:\n<abs_path>\n\n` followed by frontmatter-stripped body.
4. Process startup raises if any Channel A skill file contains `submit_*` substring OR any `TERMINAL_DESCRIPTORS` key as substring. `SkillRegistry.register()` raises identically on Channel B.
5. `RuleBasedAgentResolver.resolve()` returns `AgentSelection.skill_path = None` when the resolved definition's `skill` is None (whether base, variant target, or no-match fallback); returns the absolute `Path` otherwise. Variant target's `None` supersedes base's declaration.
6. v1 parses `skill: <path>` only; declaring `skill_variants:` raises `NotImplementedError` at agent-definition load.
7. Audit recorder logs `skill_resolved` event with non-empty `skill_path` whenever row 4 is emitted (via existing `record_initial_messages` instrumentation; no recorder change required).
8. Helpers (`ask_advisor`, `ask_resolver`) and subagent (`run_subagent`) launch shapes are unchanged by this PR; no edits to those files.

---

## ADR

**Decision:** Adopt Option D — skill as variant-frontmatter field, resolved through the existing `RuleBasedAgentResolver`. Ship v1 with `skill:` string form; reserve `skill_variants:` list form in schema (raises `NotImplementedError` at load until v2). Apply bilateral lint (Channel A startup + Channel B load) using deterministic substring/key scans. Defer helper / subagent skill plumbing.

**Drivers:**
1. Avoid parallel predicate machinery — variants and skills resolve through one pass.
2. Match the user's mental model — "planner_v1 vs planner_v2" maps directly to variants.
3. Community-skill compatibility is required — channel separation preserves the terminal contract.

**Alternatives considered:**
- **Option A (file-per-variant):** rejected — ~80% duplication between variants of the same role; drift is the design doc's named failure mode.
- **Option B (doc's current; parallel `applies_when` loader):** rejected — runs as a redundant pass of identical predicate machinery once you make the predicates `ResolverContext → bool`; doc's flat `applies_when` keys (`iteration_sequence_no`, `has_failed_attempts`) don't exist on `ContextScope`.

**Why D wins:**
- Reuses `PredicateRegistry` and the `RuleBasedAgentResolver.resolve()` machinery (zero new resolution paths).
- Collocates skill with the variant target that already owns the full agent definition (terminals, allowed_tools, context_recipe).
- Schema reservation for `skill_variants:` handles the future workflow-only axis without resolver churn or every-frontmatter migration.
- Backwards-compatible: agents without `skill:` keep the 3-row launch.

**Consequences:**
- **Variant supersession foot-gun:** adding a variant for a scope drops the base's skill for that scope unless the variant target also declares `skill:`. Documented; consistent with existing `terminals`/`allowed_tools` semantics.
- **Axis-coincidence assumption:** D's "one decision" claim holds only while variant axis (depth) coincides with workflow axis. First workflow-only axis triggers the v2 promotion to `skill_variants:` list form.
- **Helper plumbing deferred:** `ask_advisor`, `ask_resolver`, `run_subagent` keep their current launch shapes. Original design-doc AC#6 (helper four-row shape) is **superseded** by this addendum.
- **Paraphrase residual risk:** community skill paraphrasing terminal semantics in prose evades the deterministic lint. Advisor pre-submission discipline is the named backstop.

**Follow-ups:**
- Layer 2: `render_terminal_catalog(focus="decision_tree")` rendering explicit "A vs B" decision text in row 3 alongside `selection_guidance`.
- Helper-skill plumbing for `ask_advisor` and `ask_resolver` (when a real helper-skill use case surfaces).
- Subagent (`run_subagent`) skill plumbing.
- `include:` transclude syntax for skills shared across variants (only when duplication pressure surfaces).
- Explicit example-bank paraphrase detector for Channel B (only if real incidents surface).
- Skill-quality eval suite (terminal-selection accuracy on held-out scope set).
- CODEOWNERS rule on `agents/profile/main/skills/*.md` and `skills/bundled/*/SKILL.md` requiring terminal-registry-owner review.

**Open questions deferred:**
- Skill version stamps (parent doc Q3) — defer to first cache-behavior pressure in production.
- Multi-skill chains per launch — Option D supports "one skill per resolved variant"; chained skills is an orthogonal extension.
- Whether `skill_variants:` predicate dispatch reuses the exact same `PredicateRegistry` or introduces a scoped sub-registry — defer to v2 implementation.

---

## Process record

- **Consensus loop:** ralplan (Architect + Critic, deliberate mode auto-engaged).
- **Iteration 1:** Architect found axis-coincidence assumption, Channel B lint gap, helper-plumbing partial violation. Critic returned ITERATE with 4 blocking asks: (1) resolver ownership rule, (2) v1 schema commitment, (3) restored files-touched table, (4) helper scope decision; plus Finding 5 (paraphrase denylist circularity).
- **Iteration 2:** Architect returned NEEDS-MORE with 5 specific asks (variant-target-without-skill edge case, three resolver paths enumerated, path resolution specified, launcher branch identified, recorder confirmed out of scope). All addressed in the revised plan. Critic returned **APPROVE**.
- **Carry-forward refinement:** add one-sentence skill-author foot-gun note about variant-target supersession (incorporated above in Q1 section).
