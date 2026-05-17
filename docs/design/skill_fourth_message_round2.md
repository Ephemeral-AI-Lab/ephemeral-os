# Skill-as-Row-4 — Round 2 Scope Decision (Channel A vs Channel B per role)

**Status:** Approved (ralplan consensus — 3 iterations, Architect APPROVE + Critic APPROVE)
**Date:** 2026-05-18
**Parent doc:** `docs/design/skill_fourth_message.md`
**Prior addendum:** `docs/design/skill_fourth_message_resolutions.md` (Option D + bilateral lint + helper deferral)
**Scope:** Settles which roles get Channel A (boot-time row 4) vs Channel B (mid-run `load_skill` tool) in v1. Supersedes the resolutions doc Files-touched entry for `executor_handoff_default.md`.

---

## Headline — planner gets Channel A; executor gets Channel B with a bundled reference; everyone else gets neither in v1

The resolutions doc approved Channel A skills for both planner and executor as Phase 5 deliverables. Round 2 narrows that: ship Channel A only for planner, route executor through Channel B with a harness-owned bundled reference skill, and defer every other role.

The decision is driven by terminal-arg complexity. Planner's terminals (`submit_full_plan` / `submit_partial_plan`) carry a structured DAG with tasks, dependencies, and success criteria — workflow scaffolding earns its keep. Executor's terminals (`submit_execution_success` / `submit_execution_handoff` / `submit_execution_failure`) carry a summary string and optional handoff text — community-style skills via Channel B cover general work discipline. The bundled reference skill is required (not optional) because Channel B without a harness-owned reference would leave executor-family lint calibration ungrounded and `load_skill` plumbing untested before community skills land.

---

## Q1 — Does the design provide flexibility?

**YES, with no design change required.** Channel A and Channel B are structurally orthogonal:

- **Channel A** opt-in lives on `AgentDefinition.skill` (optional `Path | None` field per `agents/definition/model.py:91-184`, resolved through `RuleBasedAgentResolver` per resolutions doc §Q1).
- **Channel B** opt-in lives in `allowed_tools` (per-role tool gating downstream of `make_skills_tools` at `backend/src/tools/skills/_factory.py:60-66`).

The asymmetric pattern — complex-args roles get in-house Channel A skills, trivial-args roles get community Channel B skills, the rest get neither — is enabled by setting these two axes independently per role. No new machinery required.

## Q2 — v1 role scope

**Recommended option: S2 + bundled executor reference.** Channel A only for planner; Channel B for the three real executor profiles (with a harness-owned bundled reference at `backend/config/skills/executor_handoff_default/SKILL.md`); evaluator/resolver Channel B deferred; advisor/explorer Channel B architecturally N/A.

### Per-role v1 decision matrix

| role | Channel A (`skill:`) | Channel B (`allowed_tools` includes `load_skill`) | bundled SKILL.md |
|---|---|---|---|
| planner | YES (`agents/profile/main/skills/planner_default.md`) | optional (defer) | N/A — Channel A serves |
| three real executor profiles (`executor_success_handoff.md`, `executor_success_failure.md`, `entry_executor.md`) | NO | YES (`load_skill` only; not `load_skill_reference`) | YES — single shared at `backend/config/skills/executor_handoff_default/SKILL.md` |
| `executor.md` (router stub — no `allowed_tools` by design) | N/A | N/A — never runs as agent | N/A |
| evaluator | NO | NO (Round 2 decision — extends helper-Channel-A deferral to Channel B) | NO |
| advisor | NO | N/A (ephemeral pre-submission gate) | NO |
| resolver | NO | NO (Round 2 decision — extends helper-Channel-A deferral to Channel B) | NO |
| explorer | NO | N/A (read-only subagent) | NO |

**Why `executor.md` is excluded from Channel B:** it is a router stub with no `allowed_tools` field, no terminals, no body. The resolver picks one of its variants at depth-resolution time; the stub itself never runs as an agent. Adding `load_skill` to its `allowed_tools` would be mechanically vacuous.

**Why `entry_executor.md` is included:** it is the depth-0 executor profile (a real agent profile with `allowed_tools`, three terminals, `context_recipe: entry_executor`). The calibration-uniformity driver applies — entry executor can escalate to handoff or failure mid-run and needs the same STYLE.md anchoring surface as its sibling variants.

**Why evaluator/resolver Channel B is deferred (not N/A):** Round 2 extends the helper-plumbing deferral (resolutions doc line 138, which addressed Channel A for `ask_advisor`, `ask_resolver`, `run_subagent`) to Channel B for evaluator and resolver. This is a **new Round 2 decision**, not a restatement of resolutions line 138.

**Why advisor/explorer Channel B is N/A:** advisor is an ephemeral pre-submission gate; it has no scope to invoke `load_skill` mid-run. Explorer is a read-only search subagent; it has no workflow surface that a skill could scaffold.

---

## Principles

1. **Channel A and Channel B are independently per-role opt-in.** The two axes are orthogonal and decided per role.
2. **Skill leverage scales with terminal-arg complexity.** Roles whose terminals carry complex semantic load gain most from workflow scaffolding; roles with trivial args gain little.
3. **v1 scope should match real value, not symmetric coverage.** Ship Channel A where leverage is highest; defer the rest until evidence demands it.
4. **Channel B needs a harness-owned reference before community skills land.** Without one, executor-family lint calibration and Channel B plumbing (namespace, size, discovery UX) have no harness-canonical artifact.
5. **Backwards compatibility via no-op default.** Roles without `skill:` keep the 3-row launch shape; roles without `load_skill` in `allowed_tools` never invoke Channel B.

## Decision drivers

1. **Planner has the most complex terminal args.** `submit_full_plan` / `submit_partial_plan` carry a structured DAG (tasks, dependencies, success criteria). Authoring that decomposition correctly benefits from explicit workflow guidance.
2. **Executor terminal args use everyday English words** (`handoff`, `success`, `failure`) — a different paraphrase surface than planner terminals. Channel B with community skills covers general work discipline; the bundled reference skill grounds the lint calibration for this terminal family.
3. **Channel B needs production exercise before community skills land.** The first executor-family skill loaded via `load_skill` should be harness-owned content, so review judgment, registry namespace, `ToolResult` size limits, and discovery UX all have a known-good reference call site.

## Viable options

| option | Channel A v1 scope | Channel B v1 scope | pros | cons | verdict |
|---|---|---|---|---|---|
| S1 — Symmetric (resolutions doc Phase 5 as approved) | planner + executor | per-role allowed_tools | consistent model; lint stress-tested against both terminal families | 2× v1 authoring burden; executor skill body has marginal value above row 1 + row 3 | rejected |
| **S2 — Planner-only Channel A + bundled executor reference (recommended)** | planner only | three real executor profiles | ships where leverage is highest; halves Channel A surface; bundled reference closes Architect's calibration-vacuum finding; gives `load_skill` first production exercise | asymmetric (requires documentation); needs Channel B lint at `SkillRegistry.register()` to land before merge | **recommended** |
| S2-bare — Planner-only Channel A without bundled reference | planner only | three real executor profiles | simplest | calibration vacuum: executor's first real skill exposure is community-authored with only a planner-shaped `_template.md` as guidance | rejected |
| S3 — No Channel A in v1 | none | all roles | simplest v1 | loses planner workflow scaffolding where most needed | rejected |

**Steelman of S1 (preserved for the record).** The strongest case for shipping symmetric Channel A is *lint co-evolution*: the bilateral lint (`submit_*` substring + `TERMINAL_DESCRIPTORS` keys, resolutions doc AC#4) was designed against two terminal shapes — planner's compound DAG-vs-iteration decision and executor's success-vs-handoff binary. Shipping only planner means the lint pattern is only stress-tested against one terminal family before Channel B opens to community skills. Executor terminals have a different paraphrase surface (everyday English words like "handoff") than planner terminals (technical phrases like "close-iteration"); a planner-only v1 lets the lint look adequate when its weak edges (executor-family false negatives) haven't been probed yet. **S2 rejects S1 by adopting the bundled reference skill**: it gives the lint executor-family content to calibrate against without paying the symmetric Channel A authoring cost.

---

## Phase 5 net change vs resolutions doc Files-touched

| concrete change | path before (resolutions doc) | path after (Round 2) |
|---|---|---|
| Drop Channel A executor injection | `agents/profile/main/executor_success_handoff.md` declares `skill: skills/executor_handoff_default.md` (resolutions line 129) | declare `load_skill` in `allowed_tools` instead |
| Relocate executor skill | `backend/src/agents/profile/main/skills/executor_handoff_default.md` (resolutions line 131) | `backend/config/skills/executor_handoff_default/SKILL.md` (directory + SKILL.md per bundled convention at `backend/src/skills/bundled/__init__.py:22-44`) |
| Add `load_skill` to other executor profiles | (not addressed by resolutions doc) | `executor_success_failure.md` and `entry_executor.md` also declare `load_skill` in `allowed_tools` (calibration uniformity) |

The resolutions doc itself is **not edited** — it remains the historical record of the Option D approval. This Round 2 addendum supersedes the executor-skill Files-touched entry.

## Round 2 prerequisites (must land before deliverables merge)

1. **Channel B lint at `SkillRegistry.register()`.** Currently `backend/src/skills/core/registry.py:14-16` is a one-line dict insert. Must scan `submit_*` substring + `TERMINAL_DESCRIPTORS` keys and raise on violation. Test artifacts: `test_skill_lint.py::test_channel_b_rejects_submit_substring` and `::test_channel_b_rejects_terminal_descriptor_key`.
2. **Bundled-skill discovery end-to-end.** `get_bundled_skills()` returns the `executor_handoff_default` entry; `load_skill("executor_handoff_default")` returns a `ToolResult` containing the SKILL.md body.
3. **Profile-level `allowed_tools` filter applied downstream of `make_skills_tools`.** The factory at `backend/src/tools/skills/_factory.py:60-66` returns both `load_skill` and `load_skill_reference` unconditionally. The agent-level filter at `engine/agent/factory.py:237-239` (which already implements `allowed_tools ∪ terminals`) enforces per-tool gating; this prerequisite makes the property load-bearing rather than incidental. Test artifact: `test_executor_tool_exposure.py::test_load_skill_reference_not_exposed_to_executor`.

## Acceptance criteria (testable)

1. **Planner-only Channel A.** `grep -l '^skill:' backend/src/agents/profile/main/*.md` returns only `planner.md` and future planner variants.
2. **Three real executor profiles declare `load_skill`.** Grep each of `executor_success_handoff.md`, `executor_success_failure.md`, `entry_executor.md` for `load_skill` in `allowed_tools`. Router stub `executor.md` is NOT updated (has no `allowed_tools` field by design — explicit exclusion).
3. **Bundled skill exists** at `backend/config/skills/executor_handoff_default/SKILL.md` with explicit frontmatter `name: executor_handoff_default` (not relying on directory-name fallback at `backend/src/skills/bundled/__init__.py:51-52`). Test artifacts:
   - `test_bundled_skills.py::test_executor_handoff_default_discovered` — `get_bundled_skills()` returns the entry; frontmatter `name` matches.
   - `test_executor_skill_loadable.py::test_executor_can_load_executor_handoff_default` — end-to-end: executor profile's `allowed_tools` enables `load_skill("executor_handoff_default")`, which returns the SKILL.md body.
4. **Bundled skill passes Channel B lint — fails by design if prerequisite #1 not implemented.** Test artifact `test_skill_lint.py::test_executor_handoff_default_passes_channel_b_lint` imports the lint scanner module; if the scanner does not exist (prerequisite #1 missing), the import fails and this AC fails.
5. **Path correctness.**
   - Positive: `test -f backend/config/skills/executor_handoff_default/SKILL.md`.
   - Negative: `! test -f backend/src/agents/profile/main/skills/executor_handoff_default.md` (the resolutions doc's originally-planned path; never existed but guarded against future drift).
6. **Launch shape.** `pipeline.first_three_messages_capture` asserts planner launches = 4 rows; the three real executor profiles' launches = 3 rows at launch (Channel B loads append rows mid-run, not at launch).
7. **Bundled SKILL.md positive content (grep-checkable).**
   - Section header `## Trigger conditions` MUST be present — paragraph describes observable conditions (output produced, sanity checks complete).
   - Section header `## Decision framing` MUST be present — paragraph uses STYLE.md sanctioned bridging phrases (e.g., "the decision point", "your task instruction's tool catalog"); MUST NOT name terminals.
   - Section header `## Common pitfalls` MUST be present — lists role-specific gotchas without naming terminals.
   - Negative content (enforced by Channel B lint test fixture): MUST NOT contain `submit_*` substrings, `TERMINAL_DESCRIPTORS` keys, or executor-family paraphrases (`"the success submission"`, `"the handoff submission"`, `"when picking between success and handoff"`).
8. **`load_skill_reference` non-exposure.** `test_executor_tool_exposure.py::test_load_skill_reference_not_exposed_to_executor` asserts the executor tool surface includes `load_skill` but NOT `load_skill_reference`. Enforcement at downstream profile-level filter, not in `_factory.py`.
9. **Supersession of resolutions doc Files-touched entry** for `executor_handoff_default.md` (resolutions line 131). Resolutions doc itself is not edited (historical record preserved); this doc is the authoritative entry for that file in Round 2.

## ADR

**Decision:** Adopt S2 + bundled executor reference. Channel A v1 = planner only. Channel B v1 = three real executor profiles (`executor_success_handoff`, `executor_success_failure`, `entry_executor`) via `load_skill` (not `load_skill_reference`). Ship a single shared bundled reference at `backend/config/skills/executor_handoff_default/SKILL.md`. Evaluator/resolver Channel B = Round 2 decision to defer. Advisor/explorer Channel B = architecturally N/A. Channel B lint at `SkillRegistry.register()` is a Round 2 prerequisite.

**Drivers:**
1. Planner has the most complex terminal args (DAG + dependencies + success criteria); biggest workflow leverage from skill scaffolding.
2. Executor terminal args use everyday English words — a different paraphrase surface than planner terminals; community skills cover general work discipline.
3. Channel B needs harness-owned reference content before community skills land — for executor-family lint calibration, namespace + size + discovery-UX exercise, and review judgment grounding.

**Alternatives considered:**
- **S1** (symmetric Channel A planner + executor, resolutions doc as approved): rejected — 2× authoring burden with marginal executor benefit. Steelman (lint co-evolution) addressed by the bundled reference skill.
- **S2-bare** (planner-only without bundled reference): rejected on Architect's calibration-vacuum finding — executor's first real skill exposure would be community-authored with only a planner-shaped `_template.md` as guidance.
- **S3** (no Channel A in v1): rejected — planner's complexity needs scaffolding now, not later.

**Why S2 wins:**
- Halves v1 Channel A authoring surface (one skill instead of two).
- Validates Channel B with a real, harness-owned use case (executor pulls `using-superpowers`-style community skills, with the bundled default as the reference exemplar).
- Closes the executor-family lint calibration gap that S2-bare leaves open.
- Preserves all design machinery for adding Channel A back to other roles when evidence demands.

**Consequences:**
- **Channel B lint must land before bundled executor skill merge.** AC#4 fails by design if prerequisite #1 slips.
- **Executor launches stay 3-row at initial launch shape.** Rows grow via tool turns when `load_skill` is invoked mid-run.
- **Round 2 extends helper-plumbing deferral** (resolutions line 138, which addressed Channel A) to Channel B for evaluator/resolver. Explicitly a new decision, not a restatement.
- **Bundled `name: executor_handoff_default`** becomes the v1 `load_skill` API surface; future renames would be breaking changes.
- **Per-tool gating** of `load_skill_reference` becomes a load-bearing property of the profile-level `allowed_tools` filter (was previously incidental).

**Follow-ups:**
- Reassess executor Channel A in v2 if Channel B + bundled reference proves insufficient.
- Evaluator / resolver Channel B opt-in when workflow pressure emerges (lifts Round 2 deferral).
- `load_skill_reference` to executor `allowed_tools` when bundled skill grows `references/*.md` subdirectory.
- Bundled reference skill catalog under `backend/config/skills/` grows as new roles opt into Channel B.
- Semantic validation smoke test for bundled SKILL.md content (currently only grep-checkable section headers + forbidden-substring constraints).

---

## Process record

- **Consensus loop:** ralplan Round 2, three iterations (max 5 allowed).
- **Iteration 1.** Planner draft proposed S2. Architect APPROVED with one architectural addition — ship `executor_handoff_default.md` as a Channel B *bundled reference skill* (not Channel A injection), closing the calibration-vacuum tradeoff. Critic returned ITERATE on three critical mechanical findings:
  - F#1: wrong bundled-skill path (flat file vs directory-with-SKILL.md convention).
  - F#2: Channel B lint at `SkillRegistry.register()` does not yet exist (assumed by AC#3).
  - F#3: tool name ambiguity (`load_skill` vs `load_skill_reference`).
  - Open question: should depth-0 base `executor.md` get `load_skill`?
- **Iteration 2.** Planner revised: corrected path to `backend/config/skills/executor_handoff_default/SKILL.md`; listed Channel B lint as a prerequisite; selected `load_skill` (deferred `load_skill_reference`); resolved depth-0 question by shipping `load_skill` to all three executor variants. Architect APPROVED. Critic returned ITERATE on five findings:
  - F#1' (critical): "three variants" naming included `executor.md` router stub (no-op for Channel B); silently omitted `entry_executor.md`.
  - F#2' (critical): AC#5 unfalsifiable (find on non-existent dir).
  - F#3' (major): AC#4 dependency phrasing weak (vacuous pass).
  - F#4' (missing): no positive content requirement for bundled SKILL.md.
  - F#5' (major): `load_skill_reference` allowlist semantics unclear.
- **Iteration 3.** Planner revised: dropped router stub `executor.md` with rationale; named the three real executor profiles (`executor_success_handoff`, `executor_success_failure`, `entry_executor`); replaced AC#5 with positive existence + sanity-check negative; tightened AC#4 to "fails by design if prerequisite #1 missing"; added AC#7 positive content with three required section headers; named prerequisite #3 enforcement location (downstream profile filter, not `_factory.py` itself). Architect APPROVED all five mechanical fixes. **Critic APPROVED.** All iteration-2 findings verified closed against the codebase.

**Carry-forward refinements incorporated** during iteration 3:
- Wording clarification: `load_skill_reference` non-exposure lives at the profile-level `allowed_tools` filter downstream of `make_skills_tools`, not inside `_factory.py` itself (Architect iteration-3 sharpening).
