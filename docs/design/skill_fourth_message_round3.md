# Skill-as-Row-4 — Round 3 Simplification (single channel, planner-only)

**Status:** Approved by user directive 2026-05-18 (no consensus loop). Supersedes Round 2 scope decisions and Round 1 FAQ convention artifacts. Parent doc's core launch-shape framing is retained, with one structural change to row 3 / row 4 split (see §"Launch shape under Round 3").
**Date:** 2026-05-18
**Parent doc:** `docs/design/skill_fourth_message.md`
**Prior addenda:**
- `docs/design/skill_fourth_message_resolutions.md` — Option D (`skill:` as `AgentDefinition` frontmatter field, resolved through `RuleBasedAgentResolver`). **Retained.**
- `docs/design/skill_fourth_message_round2.md` — Channel A vs Channel B per role, bundled executor reference, helper-Channel-B deferral. **Superseded.**
- Parent doc §"Design FAQ — follow-up clarifications" (Round 1) — STYLE.md + _template.md as Phase 5 prerequisites. **Superseded.**

---

## Headline — single-channel design, planner only, `selection_guidance` duplicated in row 4 (same registry render) for emphasis

Rounds 1 and 2 introduced (a) a two-channel split (boot-time row 4 vs mid-run `load_skill`), (b) bilateral lint at both registration paths, (c) a positive-reference convention (`STYLE.md` + `_template.md`), and (d) a bundled executor reference skill. Round 3 collapses all four.

Round 3 keeps exactly one mechanism: when an agent has a `skill:` declared on its `AgentDefinition` and the resolver returns a skill path, the launcher renders row 4 in the **proposed format** below. Row 3 retains the terminal-catalog auto-append exactly as today (unchanged). Row 4 includes the same `selection_guidance` content rendered from `TERMINAL_DESCRIPTORS` via `render_terminal_catalog(focus="selection_guidance", ...)` at `backend/src/tools/_terminals/registry.py` — the same call site that produces the row 3 catalog. The duplication is intentional emphasis at the binding decision point, not paraphrase: one source, two render targets, no drift. When no skill is declared, the launch stays at three rows exactly as today. v1 declares skills only for planner variants. No other role gets a skill in v1.

---

## The four directives that defined Round 3

1. **One skill mechanism, not two.** At most one skill per launch. When a skill is resolved, build row 4 in the proposed format (skill body + terminal selection block in the same row). Drop Channel A / Channel B framing entirely.
2. **`load_skill_reference` for skill-equipped agents.** A skill-equipped agent gets the reference tool to pull from its own skill's `references/` directory. Do **not** equip the `load_skill` tool — the skill is already loaded in row 4; arbitrary on-demand skill loading is out of scope.
3. **No `STYLE.md`, no `_template.md`.** Drop the Round 1 convention artifacts. Skill authors work from the design doc plus existing examples. The startup substring lint is retained but narrowed (single scan path, not bilateral).
4. **Skill only for planner.** Each planner variant gets its own skill folder (`skill.md` + `references/`, references initially empty). No skill for executor, evaluator, advisor, resolver, or explorer in v1.

---

## Launch shape under Round 3

| case | row 1 | row 2 | row 3 | row 4 |
|---|---|---|---|---|
| Planner with skill (v1: `planner.md` + `planner_full_only.md`) | system identity | context | task instruction + terminal catalog (status quo — `selection_guidance` from `registry.py`) | `Load skill: <name>`\n`<skill>...</skill>`\n`<terminal_selection>...</terminal_selection>` — same `selection_guidance` content as row 3, repeated for emphasis |
| Any other main agent (executor / evaluator) | system identity | context | task instruction + terminal catalog (status quo) | absent |
| Helper (advisor / resolver) | system | parent context + transcript | helper-task user_msg_2 | absent |
| Subagent (explorer) | system | spawn prompt | absent | absent |

The conditional rule for the launcher: when the resolver returns a non-None `skill_path`, the composer builds row 4 as the composite (skill body + `<terminal_selection>` block) **in addition to** row 3 (whose terminal-catalog auto-append is unchanged). When `skill_path` is None, status quo (catalog on row 3, no row 4). The `<terminal_selection>` block in row 4 sources from `render_terminal_catalog(focus="selection_guidance", ...)` at `backend/src/tools/_terminals/registry.py` — the same call site that produces the row 3 catalog. The duplication is intentional emphasis at the binding decision, not paraphrase; one source, two render targets.

### Row 4 composite format

```
Load skill: <skill-name>

<skill>
<frontmatter-stripped skill.md body>
</skill>

<terminal_selection>
Pick exactly one based on outcome:
- <tool_1_name>: <tool_1.selection_guidance>
- <tool_2_name>: <tool_2.selection_guidance>
</terminal_selection>
```

The `<terminal_selection>` block is rendered from `render_terminal_catalog(focus="selection_guidance", ...)` at `backend/src/tools/_terminals/registry.py:171` — **the same call site that produces the row 3 catalog**. Both row 3 and row 4 render from this single source when a skill is present (the row 3 render is unchanged from status quo; row 4 adds a duplicate render of the same `selection_guidance` content for emphasis at the binding decision). When no skill is present, only row 3 renders. The duplication is intentional emphasis, not paraphrase — `TERMINAL_DESCRIPTORS` at `registry.py:35` remains the single source of truth.

Tag casing for the XML-style cues is one of the three open ambiguities (see §"Open ambiguities" below) — the format above uses lowercase snake `<terminal_selection>` as a placeholder; the user originally wrote `<Terminal Tool Call>` and the final case is to be confirmed.

---

## Tool surface

| role | `skill:` declared | `load_skill` in `allowed_tools` | `load_skill_reference` in `allowed_tools` |
|---|---|---|---|
| `planner.md` | YES (its own skill folder) | NO | YES |
| `planner_full_only.md` | YES (its own skill folder) | NO | YES |
| `executor.md` (router stub — no `allowed_tools` by design) | N/A | N/A | N/A |
| `executor_success_handoff.md` | NO | NO | NO |
| `executor_success_failure.md` | NO | NO | NO |
| `entry_executor.md` | NO | NO | NO |
| `evaluator.md` | NO | NO | NO |
| `generator_verifier.md` | NO | NO | NO |
| `advisor` (helper) | NO | NO | NO |
| `resolver` (helper) | NO | NO | NO |
| `explorer` (subagent) | NO | NO | NO |

Nobody gets `load_skill`. The factory at `backend/src/tools/skills/_factory.py:60-66` returns the pair `(load_skill, load_skill_reference)`; the profile-level `allowed_tools` filter downstream (`engine/agent/factory.py:237-239`) gates per-tool. The Round 2 enforcement concern (per-tool gating must be load-bearing) still applies — but only to suppress `load_skill` from the planner variants, since they're the only roles that get any skill-related tool in v1.

`load_skill_reference` scopes to the skill loaded in row 4. The reference tool can read any file under the loaded skill's `references/` directory; it cannot resolve references for skills that are not the launch-loaded one. (This may require a factory-time `allowed_slugs` set to the skill's own name; implementation detail noted but not pinned here.)

---

## Skill folder layout (per planner variant)

```
<skill-root>/<planner-variant-name>/
  skill.md                # frontmatter-prefixed workflow content; loaded into row 4
  references/             # optional per-reference docs; empty in v1
    <ref-name>.md         # added when needed; reachable via load_skill_reference
```

v1 ships two skill folders (both with `references/` initially empty):
- `<skill-root>/planner/` (for `planner.md`)
- `<skill-root>/planner_full_only/` (for `planner_full_only.md`)

Both planner variants point at distinct skill folders via `skill:` on their respective `AgentDefinition` frontmatter. Skill body MUST be terminal-silent at the contract level (no `submit_*` substrings, no `TERMINAL_DESCRIPTORS` keys) — same lint rule as the original parent doc §"Skill body". Bridging language ("the decision point", "the submission step") is permitted; selection-rule paraphrase is not.

The exact `<skill-root>` and the case of `skill.md` vs `SKILL.md` are two of the three open ambiguities (§"Open ambiguities").

---

## What's dropped from Rounds 1 and 2

| dropped item | source | replaced by |
|---|---|---|
| Channel A / Channel B distinction | resolutions doc §Q2 + round2 doc | single channel: row 4 at launch only |
| `load_skill` tool plumbing for any role | round2 doc Phase 5 + prerequisites | `load_skill_reference` only, for skill-equipped roles only |
| `SkillRegistry.register()` bilateral lint | resolutions doc §Q2 + round2 prerequisite #1 | startup substring scan on declared `skill:` paths only (single scan path) |
| Bundled `executor_handoff_default/SKILL.md` reference | round2 doc Phase 5 | not shipped; executor has no skill in v1 |
| `STYLE.md` (allowed/disallowed phrase enumeration) | parent doc Round 1 FAQ AC#2 | dropped; skill authors work from existing examples + lint |
| `_template.md` (positive exemplar with grep-checkable headers) | parent doc Round 1 FAQ AC#3 | dropped; skill authors freehand within lint rule |
| Per-role Channel A/B matrix complexity | round2 doc §"Per-role v1 decision matrix" | one bit per role: `skill:` declared or not |
| Round 2 prerequisite #3 (load_skill_reference allowlist load-bearing) | round2 doc | retained but narrowed: needed only to suppress `load_skill` from planner variants |

The resolutions doc's Option D (skill as frontmatter field, resolver-driven) is **retained**. The plumbing investment (`AgentDefinition.skill` field, `AgentSelection.skill_path`, composer `skill_message`, launcher inversion) is the same.

---

## Implementation phases

### Phase 1 — Plumbing (unchanged from resolutions doc)

| file | change |
|---|---|
| `backend/src/agents/definition/model.py` | add `skill: Path \| None = None` to `AgentDefinition`; **drop** `skill_variants` from the v1 surface (Round 3 simplification — defer to v3 if axis divergence emerges) |
| `backend/src/agents/definition/loader.py` | resolve `skill:` to absolute Path; raise on missing referenced file |
| `backend/src/task_center/_core/agent_routing.py` | add `skill_path: Path \| None = None` to `AgentSelection`; populate from base / variant target |
| `backend/src/task_center/context_engine/core.py` | add `skill_message: ConversationMessage \| None = None` to `LaunchBundle`; composer builds skill+terminal-block composite when `skill_path` is set |
| `backend/src/task_center/attempt/runtime.py` | add `skill_message` field to `AgentLaunch` |

### Phase 2 — Launcher conditional

| file | change |
|---|---|
| `backend/src/task_center/attempt/launch.py` | when `skill_message` is present, it becomes row 4 **in addition to** row 3 (which retains its existing terminal-catalog auto-append unchanged); `runner_initial_messages = [context_message, role_instruction_message]`. When `skill_message` is None, status quo (catalog on row 3, no row 4). Row 3 is unchanged in both cases. |
| `backend/src/task_center/context_engine/core.py::build_skill_message` (new helper) | when building `skill_message`, invoke `render_terminal_catalog(focus="selection_guidance", terminals=<resolved terminals>)` at `backend/src/tools/_terminals/registry.py:171` and append the rendered text inside the `<terminal_selection>` block of the row-4 composite. Same call site that produces the row 3 catalog — single source, two render targets. |

### Phase 3 — Startup lint (narrowed)

| file | change |
|---|---|
| `backend/src/agents/skills/loader.py` (NEW) | at process start, scan every `AgentDefinition.skill` path for `submit_*` substring + any `TERMINAL_DESCRIPTORS` key as substring; raise on violation. Single scan path (no Channel B counterpart needed). |

### Phase 4 — Planner skill content

| file | change |
|---|---|
| `backend/src/agents/profile/main/planner.md` | declare `skill: <relative-path>/planner/skill.md`; add `load_skill_reference` to `allowed_tools` |
| `backend/src/agents/profile/main/planner_full_only.md` | declare `skill: <relative-path>/planner_full_only/skill.md`; add `load_skill_reference` to `allowed_tools` |
| `<skill-root>/planner/skill.md` (NEW) | first planner workflow skill — scope-bounding, criterion-per-deliverable, dependency reasoning. Terminal-silent at contract level. |
| `<skill-root>/planner/references/` (NEW empty directory) | placeholder for future references |
| `<skill-root>/planner_full_only/skill.md` (NEW) | planner-full-only workflow variant (full-plan-only at deep depth) |
| `<skill-root>/planner_full_only/references/` (NEW empty directory) | placeholder |

### Phase 5 — Tests + audit

| file | change |
|---|---|
| `backend/src/agents/tests/test_skill_resolver.py` (NEW) | base path, variant-target path, no-skill returns None |
| `backend/src/agents/tests/test_skill_message.py` (NEW) | row-4 composite format — `Load skill:` header + `<skill>` block + `<terminal_selection>` block; frontmatter stripped from skill body |
| `backend/src/agents/tests/test_skill_lint.py` (NEW) | startup scan refuses `submit_*` substrings and `TERMINAL_DESCRIPTORS` keys in planner skill files |
| `backend/src/agents/tests/test_planner_tool_exposure.py` (NEW) | planner profiles expose `load_skill_reference` but NOT `load_skill` |
| `backend/src/task_center_runner/tests/sweevo/test_first_three_messages_capture.py` | edit — planner launches = 4 rows with row-4 composite; non-planner main-agent launches = 3 rows with catalog on row 3 |
| `scripts/build_first_three_messages_report.py` | edit — render row 4 composite when present; render row 3 catalog when row 4 absent |

---

## Acceptance criteria (testable)

1. **Planner variants declare `skill:`.** `grep -l '^skill:' backend/src/agents/profile/main/*.md` returns exactly `planner.md` and `planner_full_only.md`.
2. **Planner variants expose `load_skill_reference`.** Both `planner.md` and `planner_full_only.md` declare `load_skill_reference` in `allowed_tools`. Neither declares `load_skill`. Grep-checkable.
3. **No other role declares `load_skill` or `load_skill_reference`.** Grep-checkable across `backend/src/agents/profile/main/*.md` and helper / subagent definitions.
4. **Skill folders exist** with required structure: `<skill-root>/planner/skill.md` + `<skill-root>/planner/references/` directory; same for `planner_full_only`. `references/` directories are empty in v1.
5. **Row-4 composite format renders** with `Load skill: <name>`, `<skill>…</skill>`, and `<terminal_selection>…</terminal_selection>` blocks in that order. Terminal block content rendered from `render_terminal_catalog(...)` against the same registry that drives row 3 in the no-skill path.
6. **Launch shape conditional:** `pipeline.first_three_messages_capture` asserts planner launches = 4 rows (row 3 retains terminal catalog as today; row 4 holds the skill + `<terminal_selection>` composite; the row-4 `<terminal_selection>` content matches the row-3 catalog content character-for-character because both are rendered from `render_terminal_catalog(focus="selection_guidance", ...)` at `registry.py:171`). Executor / evaluator / entry_executor launches = 3 rows (catalog on row 3, no row 4) — unchanged from today.
7. **Startup lint rejects forbidden substrings.** A fixture skill containing `submit_foo` causes process startup to raise. Same for any `TERMINAL_DESCRIPTORS` key as substring.
8. **`load_skill_reference` scopes correctly.** When planner calls `load_skill_reference("some-ref")`, the tool resolves against the planner's own skill `references/` directory only; references in another skill's directory are not reachable. Test artifact: `test_planner_tool_exposure.py::test_load_skill_reference_scoped_to_own_skill`.
9. **`load_skill` is not callable** from any agent in v1. Test asserts the tool surface for every shipped profile lacks `load_skill`.
10. **No new convention artifacts ship.** Repository contains no `STYLE.md` or `_template.md` under any skills directory.

---

## ADR

**Decision:** Round 3 simplification. Single skill mechanism resolved via existing Option D plumbing. Skill declared only on planner variants in v1. When a skill is resolved, row 4 absorbs the terminal catalog from row 3 in the composite format above. Tool surface for skill-equipped agents includes `load_skill_reference` only; `load_skill` is not shipped to any role. Convention artifacts (`STYLE.md`, `_template.md`) and Channel B framing are dropped.

**Drivers:**
1. **Simplicity over symmetry.** A single skill mechanism eliminates the two-channel cognitive overhead and the per-role decision matrix. Future channels can be added if community pressure emerges.
2. **Skill leverage is asymmetric in practice.** Planner's terminal args carry the most semantic load; executor's args are trivial. Shipping a skill only where the leverage exists matches actual workflow value.
3. **Reference-only tool surface bounds the design.** `load_skill_reference` lets a skill-equipped agent drill into its own skill's references without exposing arbitrary skill loading — keeps the "at most one skill per launch" invariant load-bearing.

**Alternatives considered:**
- **Round 2** (Channel A + Channel B + bundled executor reference): rejected — two-channel complexity, executor calibration debt, convention-artifact burden.
- **Round 1** (Channel A symmetric, planner + executor; STYLE.md + _template.md): rejected — symmetric coverage without proportional value; convention artifacts add review surface without changing the lint floor.
- **Original parent doc** (skill loader with `applies_when` frontmatter, no executor): closest to Round 3 in spirit but predates Option D (frontmatter field) and lacks the row-4 terminal-catalog relocation. Superseded by Option D plumbing + Round 3 conditional render.

**Why Round 3 wins:**
- Three changes to current launcher (resolver populates `skill_path`; composer builds row-4 composite; row-3 catalog append becomes conditional) plus one tool-config bit (`load_skill_reference` on planner variants). No new registries, no new lint paths, no new convention docs.
- Future flexibility preserved: Channel B can be added back if community pressure emerges; STYLE.md can be authored if multiple skill authors join; multi-skill chains can extend the launcher if v2 demands.
- Round 3 has the smallest blast radius of any considered design while still delivering the planner workflow scaffolding that motivated the whole effort.

**Consequences:**
- **Row 3 content is unchanged in all cases.** Row 3 retains its existing terminal-catalog auto-append whether a skill is resolved or not. The only conditional in the launcher is the presence or absence of row 4. The row-4 `<terminal_selection>` block duplicates the row-3 catalog content for emphasis, with both rendered from the same `TERMINAL_DESCRIPTORS` source — no drift risk, since neither row paraphrases the other.
- **Executor / evaluator / helpers see no change.** Tool surfaces unchanged; launch shape unchanged.
- **Community-skill compatibility deferred to a hypothetical v2.** No `load_skill` tool in v1; no `SkillRegistry` registration channel exposed.
- **`skill_variants:` schema is NOT reserved in v1 frontmatter.** Round 3 drops the v2-reservation introduced by the resolutions doc. If workflow-only axis divergence emerges later, the schema is added then (one-line `AgentDefinition` change plus loader update).
- **Three ambiguities remain** (folder root, file case, tag casing) — see §"Open ambiguities" below. None are blocking the design decisions; all need pinning before code lands.

**Follow-ups:**
- Pin the three open ambiguities below.
- Author `planner/skill.md` and `planner_full_only/skill.md` workflow content (Phase 4).
- Reassess: Channel B / community skills, STYLE.md, multi-skill chains, executor skill, helper skills — all deferred until concrete pressure emerges.

---

## Open ambiguities (must resolve before code lands)

1. **Skill folder root.** Adjacent to agent profiles (`backend/src/agents/profile/main/skills/<planner-variant>/`) — co-locates skill with the agent definition that owns it — or shared bundled root (`backend/config/skills/<planner-variant>/`) — reuses the existing discovery convention used by `load_skill_reference` at `backend/src/skills/bundled/__init__.py:22-44`? The first is more cohesive; the second avoids touching the discovery code path. Tradeoff: code-path reuse vs. profile-folder cohesion.

2. **File case: `skill.md` vs `SKILL.md`.** User's directive says `skill.md` (lowercase). Existing bundled discovery convention at `backend/src/skills/bundled/__init__.py:22-44` expects `SKILL.md` (uppercase). If the skill folder root is the bundled root, the case must match the existing convention (uppercase). If it's a new root under `agents/profile/main/skills/`, lowercase is free to pick. Decision deferred to ambiguity #1's resolution.

3. **Row 4 tag casing.** User's original wording in the proposed format: `<skill>`, `<Terminal Tool Call>`. Conventional XML-cue style in this codebase is lowercase snake (e.g., `<terminal_selection>`). The composite-format example in §"Row 4 composite format" uses the lowercase form as a placeholder; the final convention should be confirmed (consistency with other row content vs. literal preservation of user wording).

---

## Process record

Round 3 was produced by **user directive**, not by a ralplan consensus loop. Four directives issued in one conversational turn:

1. Drop Channel A / Channel B framing; single skill per launch; when present, build row 4 in the proposed format.
2. Skill-equipped agents get `load_skill_reference`; nobody gets `load_skill`.
3. Drop `STYLE.md` and `_template.md`.
4. Skill only for planner variants; per-variant `skill.md` + `references/` folder structure.

The directives map cleanly onto the existing Option D plumbing (resolutions doc) with surgical edits to the launcher conditional and a narrowing of the lint scan to a single path. No consensus loop was run because the changes are scope reductions atop already-approved infrastructure — the architectural decisions (Option D, frontmatter-field resolution, row-4 placement) are preserved; only the channel matrix, convention artifacts, and per-role assignment are modified.

If implementation surfaces a substantive ambiguity beyond the three listed in §"Open ambiguities", revisit with a consensus loop scoped to that ambiguity.
