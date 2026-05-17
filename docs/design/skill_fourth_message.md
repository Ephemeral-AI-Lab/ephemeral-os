# Skill as the Fourth Message — Design Doc

**Status:** Proposed (core design); Addendum APPROVED 2026-05-18.
**Owner:** task-center maintainers
**Last updated:** 2026-05-18
**Related:** `docs/reports/first_three_messages_report.md`, `task_center/attempt/launch.py`, `agents/profile/main/`
**Addendum:** See §"Design FAQ — follow-up clarifications" below for Q1–Q4 + meta-question resolution (Architect APPROVE + Critic APPROVE).

## Summary

Inject a per-role *workflow skill* as a fourth message at agent launch — `system` + `context_message` + `role_instruction` + `skill`. The skill teaches HOW to do the work; the existing four-message-prefix already carries WHAT (system identity, current state, current task). The split keeps terminal-tool authority intact, enables scope-driven skill reuse, and matches Claude Code's user-invocable-skill injection pattern.

This is **Layer 1** of a two-layer skill design. Layer 2 (per-terminal decision-tree text rendered inside the existing terminal-tool catalog) is a separate follow-up and is intentionally out of scope here.

## Problem

Main agents (planner, executor, evaluator) need rich workflow knowledge — how to scope a goal, how to decompose into atomic tasks, when to mark complete vs handoff. Today this lives implicitly in the `agents/profile/main/<role>.md` system prompt, which mixes role identity with workflow guidance and selection criteria. The result is:

- **Drift risk.** When terminal semantics change (registry side), system prompts get out of sync because they re-state the same selection rules in prose.
- **No scope awareness.** A planner facing iteration 1 attempt 1 needs different workflow guidance than a continuation planner; today they receive identical system prompts.
- **No composability.** Two roles that share a workflow concept (e.g. "decompose into criterion-per-deliverable") have to repeat the text in each `<role>.md`.
- **Hard to test.** Workflow guidance buried in a system prompt can't be unit-tested in isolation; we can only test the end-to-end agent behavior.

The terminal tools themselves are *binary* in spirit — planner picks between `submit_plan_closes_goal` and `submit_plan_continues_goal`; executor between `submit_execution_success` and `submit_execution_handoff` (or `submit_execution_failure`). That binary commitment point is exactly the moment workflow knowledge matters most. Skills must improve that decision without redefining what the terminals mean.

## Goals

1. Workflow knowledge becomes a first-class, reviewable artifact separate from role identity and from terminal contracts.
2. Skills load by scope so an agent only ever sees the skill that applies to its position (iteration / attempt / dep state / partial-vs-complete).
3. Terminal-tool registry remains the single source of truth for selection semantics — skills *cite* terminals but never redefine them.
4. Captured `message.jsonl` preserves the full launch shape so replays, evals, and forensics see exactly what the agent saw.
5. Zero impact on roles that don't ship a skill — every existing scenario keeps passing without change.

## Non-goals

- Layer 2 terminal decision-tree rendering (separate follow-up).
- Skills for subagents (explorer) — explorer has no binary terminal.
- Skills for evaluator and verifier — defer until the planner/executor pattern stabilizes.
- A multi-skill chain per agent (today: at most one skill per launch).
- Runtime skill mutation / hot-reload — skills load at process start.

## Design

### Final launch shape

| row | role | content | producer |
|---|---|---|---|
| 1 | `system` | `agents/profile/main/<role>.md` body (identity) | agent loader (unchanged) |
| 2 | `user` | composer's `context_message` (goal, iteration, deps, attempt plan, criteria) | `ContextComposer.compose` (unchanged) |
| 3 | `user` | composer's `role_instruction_message` (role-instruction body + terminal-tool catalog + `# Your task`) | `_append_terminal_catalog` (unchanged) |
| 4 | `user` | `Base directory for this skill:\n<abs_path>\n\n` + SKILL.md body | **new** — launcher prepends to `initial_messages`, skill becomes the *spawn prompt* |

When no skill matches, the launch shape stays at 3 rows (or 2 for entry executor). This is the only path; the launcher decision flow:

```
if role_instruction:
    if skill is not None:
        runner_prompt = skill_text                       # row 4
        initial_messages = [context_message,
                            role_instruction_message]     # rows 2 + 3
    else:
        runner_prompt = role_instruction                 # row 3
        initial_messages = [context_message]             # row 2
else:                                # entry_executor branch
    runner_prompt = context_message                       # row 2
    initial_messages = None
```

The inversion (skill becomes the spawn prompt rather than a seeded initial) is what lands it as row 4. Our recorder already writes seeded initials between system and spawn prompt, so no recorder change is needed.

### Why row 4, not row 2

Context (row 2) and task (row 3) are paired — they're both authored by the composer in a single `LaunchBundle`. Splitting them would break the existing pairing semantics and confuse anyone reading the launch trace. Putting the skill *after* the pair frames it as "applied knowledge for the task you just read", and benefits from the cache property that row 4 changes don't invalidate rows 1–3.

Claude Code itself injects skills as row 2 (immediately after system) because it has a human-facing transcript UI where skill-as-late-row looks weird. We don't. Row 4 is the right choice for this codebase.

### File convention

```
agents/profile/main/planner/skill.md        # planner workflow skill
agents/profile/main/executor/skill.md       # executor workflow skill
```

Skills sit in a directory named after the role; `<role>.md` (existing system prompt) is a sibling. Frontmatter is optional and supports a single `applies_when` predicate keyed off scope fields the resolver already exposes:

```yaml
---
applies_when:
  iteration_sequence_no: ">=2"     # optional; matches iter 2+
  has_failed_attempts: true        # optional; matches retry attempts
---
```

A role MAY have multiple skill files; the loader picks the most-specific predicate match for the launch's scope. If multiple match equally, the loader fails at startup (no silent precedence).

### Skill body

Plain markdown. No restrictions on length, structure, or content **except**:

- **Static lint (enforced at process start):** skill files MUST NOT contain any `submit_*` substring. Terminals are taught in the catalog (row 3), not in skills (row 4). Mentioning a terminal name in a skill is a hard error — the loader refuses to start until it's removed.

This rule is what keeps terminal-tool authority single-sourced.

### Identification at read time

Skill rows are identifiable by their leading content prefix:

```
Base directory for this skill:
/Users/.../agents/profile/main/planner

<frontmatter-stripped body>
```

No new `is_meta` field on `ConversationMessage`. The prefix is the marker; one-line helper:

```python
def is_skill_row(row: dict) -> bool:
    return row["role"] == "user" and any(
        b.get("text", "").startswith("Base directory for this skill:")
        for b in row.get("content", [])
    )
```

If/when we build a transcript viewer that needs to bulk-filter scaffolding rows, derive it then. Don't add the field now.

### Helpers (advisor, resolver)

Symmetric treatment: when a helper skill exists at `agents/profile/helper/<name>/skill.md`, the helper tool prepends it to the spawn-prompt seat just like the main launcher does:

| row | role | content |
|---|---|---|
| 1 | system | `agents/profile/helper/<name>.md` |
| 2 | user | `assemble_user_msg_1(messages)` (parent context + parent task + parent transcript) |
| 3 | user | helper-task user_msg_2 (advisor: catalog + pending submission + calibration + how-to-submit; resolver: issues + task) |
| 4 | user | skill body |

Subagent (explorer) is single-layer — defer skill-row addition.

## Implementation phases

### Phase 1 — Skill file convention + loader (no behavioral wiring)

New module `backend/src/agents/skills/loader.py`:

```python
@dataclass(frozen=True, slots=True)
class SkillFile:
    path: Path
    body: str  # frontmatter stripped

def load_skill_for(role: str, scope: ContextScope) -> SkillFile | None: ...
```

Behavior:

- Scans `agents/profile/{main,helper}/<role>/skill.md` once at process start.
- Parses optional `applies_when` frontmatter into a predicate function.
- Returns the most-specific match for the given `scope`; returns `None` when no skill applies.
- Raises at startup (not runtime) when any skill file contains a `submit_*` substring (the lint rule).
- Raises at startup when two skills tie on specificity for a given role.

Test surface: `backend/src/agents/tests/test_skill_loader.py`. Cover the happy path, lint failure, tie failure, no-skill returns None, and frontmatter parsing edge cases (no frontmatter, empty body, predicate variants).

### Phase 2 — Message builder

New module `backend/src/agents/skills/message.py`:

```python
def build_skill_message(skill: SkillFile) -> ConversationMessage:
    text = f"Base directory for this skill:\n{skill.path.parent}\n\n{skill.body}"
    return ConversationMessage(role="user", content=[TextBlock(text=text)])
```

No `ConversationMessage` schema change. No `is_meta` flag.

### Phase 3 — Launcher wiring

Edit `backend/src/task_center/attempt/launch.py:140-148` per the decision flow above. The change is ~15 lines.

Symmetrically edit:

- `backend/src/tools/ask_helper/ask_advisor.py`
- `backend/src/tools/ask_helper/ask_resolver.py`

`tools/subagent/run_subagent.py` — leave alone.

### Phase 4 — Audit & verification

- Re-run `pipeline.first_three_messages_capture` test with no skills present → 3-row main-agent launches still recorded, no regressions.
- Add a placeholder `agents/profile/main/planner/skill.md` containing `# Workflow\n\n(placeholder)`. Re-run; verify planner `message.jsonl` files now contain 4 rows and row 4 starts with the `Base directory for this skill:` prefix.
- Extend `scripts/build_first_three_messages_report.py` to detect row 4 and render it in per-case files when present.
- Extend `backend/src/task_center_runner/tests/sweevo/test_first_three_messages_capture.py` to assert row 4 presence + content prefix when a skill file is registered.

### Phase 5 — First real skills

Author:

- `agents/profile/main/planner/skill.md` — scope-bounding, criterion-per-deliverable, dependency reasoning, partial-vs-full decision *triggers* (without naming terminals).
- `agents/profile/main/executor/skill.md` — assigned-task discipline, "produce → verify → submit" flow, when to handoff *triggers* (without naming terminals).

Iterate via the live scenario test until skill-equipped agents pass the same evaluation criteria as before with at least one observable improvement (e.g., handoff-vs-success accuracy on a held-out set; tracked in a separate eval suite, not this plan).

## Files touched

| file | change |
|---|---|
| `backend/src/agents/skills/__init__.py` | new — public surface |
| `backend/src/agents/skills/loader.py` | new — file scan, predicate match, startup lint |
| `backend/src/agents/skills/message.py` | new — `build_skill_message` helper |
| `backend/src/agents/profile/main/planner/skill.md` | new — phase 5 |
| `backend/src/agents/profile/main/executor/skill.md` | new — phase 5 |
| `backend/src/task_center/attempt/launch.py` | edit — skill prepend + spawn-prompt inversion (~15 LOC) |
| `backend/src/tools/ask_helper/ask_advisor.py` | edit — symmetric skill prepend |
| `backend/src/tools/ask_helper/ask_resolver.py` | edit — symmetric skill prepend |
| `backend/src/agents/tests/test_skill_loader.py` | new — predicate + lint coverage |
| `backend/src/task_center_runner/tests/sweevo/test_first_three_messages_capture.py` | edit — assert 4 rows when skill present |
| `scripts/build_first_three_messages_report.py` | edit — render row 4 |
| `docs/reports/first_three_messages_cases/*.md` | regenerate after phases 4 + 5 |

## Acceptance criteria

1. With no skill files present, every existing test passes unchanged — fully backward compatible.
2. With `planner/skill.md` and `executor/skill.md` present, the live `pipeline.first_three_messages_capture` test runs green and the captured `message.jsonl` for planner + executor agents contains exactly 4 initial rows.
3. Row 4 starts with the literal string `Base directory for this skill:\n` followed by the absolute directory path of the skill file.
4. Process startup raises if any skill file under `agents/profile/{main,helper}/*/skill.md` contains a `submit_*` substring (lint rule for terminal authority).
5. `load_skill_for(role, scope)` returns deterministically per scope (snapshot tests cover the predicate matrix).
6. Helpers (advisor, resolver) inherit the four-row shape when a helper skill is registered (code path plumbed even if no helper skills ship in this PR).
7. Regenerated report shows the 4-row structure with row 4 populated only where a skill applies.

## Risks + mitigations

| risk | mitigation |
|---|---|
| Skill text drifts from terminal contract | Startup lint blocks `submit_*` mentions in skill files |
| Cache invalidation on skill bumps | Skill is row 4 (most-variable last); rows 1–3 stay cache-stable on skill edits |
| Loader becomes slow at launch | Skills loaded once at process start; `load_skill_for` is a predicate match against a small in-memory dict, no I/O on hot path |
| Helper transcript filter regresses | Filter already drops the first user message and `role=="system"`; with skill as the spawn prompt, the helper's "drop spawn prompt" rule still does the right thing |
| Two skills tie on specificity | Loader fails fast at startup with the conflicting paths — no silent precedence |
| Predicate keys diverge from resolver | Skill predicate keys MUST match `ContextScope` field names; reuse the scope dataclass directly so renames break loader tests immediately |

## Out of scope (follow-ups)

- **Layer 2:** terminal decision-tree text in `render_terminal_catalog(focus="decision_tree")`. Lives in the terminal-tool registry, surfaces in row 3, teaches the binary choice without polluting workflow skills.
- Subagent / explorer skills.
- Evaluator + verifier skills.
- `--explain` flag on the composer that prints the loaded skill chain at launch (debugging UX).
- `CODEOWNERS` rule on `agents/profile/*/*/skill.md` requiring terminal-registry-owner review.
- A skill-quality eval suite (terminal-selection accuracy on held-out scope set).
- Multi-skill chains per launch.

## Open questions

1. Should helper skills land in `agents/profile/helper/<name>/skill.md` (mirroring main) or in a separate `agents/profile/helper/<name>_skill.md` flat file? Lean toward the directory layout for symmetry, but flat is one fewer mkdir.
2. Should the skill body be allowed to import / transclude shared sub-skills (e.g. `dependency-reasoning.md` shared between planner and evaluator)? Out of scope for v1; revisit if duplication shows up in practice.
3. Do we want a per-skill version stamp in frontmatter for cache-keying? Probably yes long-term; defer until we see real cache behavior in production.

## Estimated effort

| phase | LOC | time |
|---|---|---|
| 1 — loader + lint + tests | ~230 | 1 h |
| 2 — message builder | ~30 | 10 min |
| 3 — launcher + helper wiring | ~50 | 30 min |
| 4 — audit/report tooling | ~80 | 30 min |
| 5 — first two skill files (drafts) | ~200 (prose) | 1 h |

**Total: ~half a day** for plumbing + first real skills, single PR.

---

## Design FAQ — follow-up clarifications

**Date:** 2026-05-18
**Status:** ralplan consensus, single iteration. Architect APPROVE, Critic APPROVE (carry-forward refinement incorporated below).
**Sibling:** [`skill_fourth_message_resolutions.md`](./skill_fourth_message_resolutions.md) — approved Option D resolution of the original §"Open questions".

This appendix closes four follow-up design questions that surface when the design is read for the first time. **Q1–Q4 are pointer entries** — they index already-approved answers without restating them. **The meta-question is genuinely new analysis** and gets the full RALPLAN-DR treatment.

### Q1 — Do we need one skill per routing variant (planner_v1 vs planner_v2, executor_v1 vs executor_v2)?

**No.** Resolved in `skill_fourth_message_resolutions.md` §Q1 via **Option D**: `skill:` is a field on `AgentDefinition`, resolved through the existing `RuleBasedAgentResolver` alongside `terminals`, `allowed_tools`, and `context_recipe`. A variant target keeps, overrides, or drops the base's `skill` value the same way it does for any other agent-definition field. v1 ships with `skill: <path>` only; v2 reserves `skill_variants:` (raises `NotImplementedError` at load) for a future workflow-only axis.

**Convention:** most variants share their parent's skill. Declare `skill:` on a variant only when the workflow itself differs — not just the terminal set. The first workflow-only axis (a workflow distinction that does not change `terminals`, `allowed_tools`, or `context_recipe`) triggers v2 promotion to `skill_variants:`.

### Q2 — How does "each agent must end with exactly one terminal tool" interact with community skills, given each agent must submit a summary?

**Three layered enforcements; community skills cannot subvert the contract.** Resolved in `skill_fourth_message_resolutions.md` §Q2 (Channels A and B).

1. **Tool dispatch** rejects any unregistered terminal name. A community skill that says "call `submit_final_answer`" fails at the runner — the tool doesn't exist in the dispatch table.
2. **Row 3 catalog** stays loaded as the authoritative submission instruction on every launch; the skill cannot displace it.
3. **Bilateral lint** (Channel A startup scan over `AgentDefinition.skill` paths + Channel B `SkillRegistry.register()`) refuses any skill containing a `submit_*` substring or any `TERMINAL_DESCRIPTORS` key as substring.
4. **Advisor pre-submission discipline** (row 3 prescribes `ask_advisor` before any submission) is the runtime backstop against natural-language paraphrase that evades substring-level lint.

Community skills augment workflow knowledge; they cannot redefine the summary contract. Residual paraphrase risk is acknowledged explicitly; an example-bank paraphrase detector is a tracked follow-up (not v1).

### Q3 — Planner / executor / evaluator / verifier have binary terminal choices — how does skill fit?

**Binary choice lives in row 3, not row 4.** Resolved in `skill_fourth_message_resolutions.md` §Q3 and the resolutions doc's Headline mapping.

The skill teaches the workflow that drives to the decision point (trigger conditions like "you've verified the deliverable exists at the claimed location"); row 3's `TERMINAL_DESCRIPTORS.selection_guidance` (and future `focus="decision_tree"` for Layer 2) owns the A-vs-B selection rule. When binary collapses to unary (e.g., `planner_full_only`, `executor_success_failure` at deep handoff depth), the **variant system narrows the catalog itself** — the skill doesn't need to know.

### Q4 — Can we make the 4-message semantics (a) system, (b) context, (c) task instruction, (d) skill?

**Yes — that is the design.** Exact mapping (canonical reference: §"Final launch shape" earlier in this doc):

| row | semantics | source |
|---|---|---|
| 1 | system identity | `agents/profile/main/<role>.md` |
| 2 | context — current goal, iteration, deps, attempt plan, criteria | composer `context_message` |
| 3 | task instruction + terminal catalog | composer `role_instruction_message` (with auto-appended catalog) |
| 4 | skill — how to do the work, scoped to the launch | resolved from `AgentDefinition.skill` |

---

### Meta-question — How much terminal contract should the skill carry, given row 3 (prompt c) is clear?

**Short answer.** Almost none. Skills should be **terminal-silent at the contract level** (no terminal names, no submission semantics, no A-vs-B selection rules) and only **terminal-aware at the workflow boundary** (they may reference "the decision point" or "the submission step" as workflow milestones — bridging language that anticipates row 3 without restating it).

This is the posture the existing bilateral lint already enforces. The intuition that "we don't need to mention much" is the correct read. The rest of this section justifies the floor (lint) and the ceiling (convention) and adds two artifacts to close a bootstrapping gap that Architect surfaced.

#### Principles (meta-question)

1. **One source of truth per concern.** Identity → row 1, contract → row 3, workflow → row 4. (Resolutions Principle 1.)
2. **Lint sets the floor, convention sets the ceiling.** Deterministic lint forbids `submit_*` substrings and `TERMINAL_DESCRIPTORS` keys. Convention asks authors to also avoid paraphrasing selection rules even where substrings cannot catch it.
3. **Bridging language is permitted; ownership is not.** Skill ends pointing at the decision; row 3 makes it.

#### Decision drivers

1. **Drift cost.** Every terminal-contract sentence in a skill becomes a sync obligation against the registry. Drift is the parent doc's named failure mode (§"Problem").
2. **Cognitive load.** Agent reads row 3 then row 4; restated selection logic forces reconciliation of two near-identical descriptions.
3. **Discoverability for skill authors.** Without a positive reference, authors lack scaffolding to write "drive toward row 3 without naming terminals" prose. This is a bootstrapping problem, not a vigilance problem.

#### Viable options

| option | what skills carry about terminals | pros | cons | verdict |
|---|---|---|---|---|
| α — Terminal-silent at every level | Zero mention. Skill stops at "the work is done"; row 3 owns "what triggers submission". | Simplest mental model; lint could relax to substring-only; no paraphrase risk. | Awkward workflow gap; planner skill cannot reference "the decision point" as a milestone; bridging language disallowed. | rejected |
| **β — Terminal-silent at contract; aware at workflow boundary (recommended)** | No names, no selection rules, no semantics. May reference "the decision point" or "the submission step" as workflow milestones. | Matches existing bilateral lint; preserves bridging language; skill drives to the decision without owning it. | Judgment-call boundary between trigger condition and selection paraphrase; paraphrase residual risk for Channel B. | **recommended** |
| γ — Skills paraphrase terminal contract richly | Names terminals by purpose ("when picking between close-goal and continue-goal"); restates selection rules in author's voice. | Full context-locality during agent reasoning; closes prose distance between row 3 catalog and row 4 trigger; valuable under high context utilization. | Guaranteed drift; row 3 + row 4 redundancy; cache invalidation cascade on every catalog edit. | rejected |

**Steelman of γ (preserved for the record).** γ is not merely richer prose — it is *context-locality during agent reasoning*. The agent reads rows in order; when it reaches the decision point, the row 3 catalog is thousands of tokens upstream from the active workflow framing. γ closes that distance by letting the skill author write the bridge inference ("verified deliverable + criterion still open → continues") in the same paragraph as the trigger. This is valuable when (1) the decision is non-obvious, (2) the agent is under context-utilization pressure, (3) skill authors are subject-matter experts on triggers but not on the registry. **β rejects γ on drift cost**: every paraphrase becomes a sync obligation against `TERMINAL_DESCRIPTORS`. The drift cost dominates the locality benefit in a multi-author / multi-variant world.

#### Bootstrapping gap (Architect's finding)

The bilateral lint and a STYLE.md doc are both **negative-only references**: they enumerate what to avoid. A new skill author at a blank file has no positive reference showing what "drive toward row 3 without naming terminals" actually looks like in prose. The worst cases are technically lint-clean prose that is still too paraphrased (Channel B failure mode) or too sparse (the "skill ends pointing at the decision" guidance has no concrete instance). This is a bootstrapping problem, not a vigilance one — review discipline cannot fix it because there is nothing for review to compare against.

**Closing addition:** ship `agents/profile/main/skills/_template.md` (~50 LOC) as a Phase 5 prerequisite — **authored before the first production skill**. The template is the positive reference that the lint and STYLE.md cannot be.

#### Pre-mortem (v1-honest — only v1 mitigations claimed)

1. **Author writes "submit the close-iteration result" in a planning skill.** Lint does not fire (no `submit_*` substring match; no `TERMINAL_DESCRIPTORS` key match if the key is e.g. `submit_plan_closes_goal`).
   - **v1 mitigations:** advisor pre-submission discipline (catches selection mismatch at runtime); manual review against `STYLE.md` and `_template.md` before merge.
   - **Follow-ups (not v1):** CODEOWNERS rule on `agents/profile/main/skills/*.md`; example-bank paraphrase detector. (Both listed in resolutions doc §"Follow-ups".) **Not claimed as v1 mitigations.**
2. **Community skill paraphrases payload shape ("returns a JSON with fields X, Y").** Channel B `SkillRegistry.register()` substring lint does not catch.
   - **v1 mitigations:** tool dispatch rejects unknown terminal names (structural drift cannot subvert execution); advisor calibration on payload shape (semantic drift caught at audit); advisor pre-submission backstop. Residual risk acknowledged (matches resolutions doc §Q2).
3. **Convention erodes over time.** Bridging language ("you'll reach the decision point") gradually slides into paraphrase ("you'll choose between option A and option B").
   - **v1 mitigations:** `STYLE.md` (negative reference) + `_template.md` (positive reference) establish the convention concretely; manual review checklist for shipped skills against both artifacts.

#### Files touched (incremental over resolutions doc §"Files touched")

| file | change |
|---|---|
| `backend/src/agents/profile/main/skills/STYLE.md` | **NEW** Phase 5 prerequisite — enumerates allowed bridging phrases and disallowed paraphrase patterns. Authored **before** `planner_default.md` and `executor_handoff_default.md`. |
| `backend/src/agents/profile/main/skills/_template.md` | **NEW** Phase 5 prerequisite — ~50 LOC worked exemplar (positive reference). Authored **before** the first production skill. Three labeled sections (see AC#3 below for exact, grep-checkable headers). |

#### Acceptance criteria (testable)

1. Bilateral lint refuses `submit_*` substrings and `TERMINAL_DESCRIPTORS` keys at both Channel A startup and Channel B `SkillRegistry.register()`. *(No change from resolutions AC#4 — restated for completeness.)*
2. `STYLE.md` ships in `agents/profile/main/skills/` **before** any production skill (planner / executor) is merged, and enumerates: (a) allowed bridging phrases such as "the decision point", "the submission step", "when the deliverable is verified"; (b) disallowed paraphrase patterns such as "the close-iteration submission", "when picking between close-goal and continue-goal".
3. `_template.md` ships in `agents/profile/main/skills/` **before** the first production skill is merged, and contains three labeled sections — **grep-checkable**:
   - `## Trigger condition (example)` — a worked trigger-condition paragraph showing the verify-then-decide pattern.
   - `## Bridging language (example)` — a paragraph using only sanctioned bridging phrases from `STYLE.md`.
   - `## Anti-example: paraphrase that lints would catch` — a deliberate paraphrase plus the lint rationale that would flag it.
4. Every shipped skill file under `agents/profile/main/skills/` (other than `STYLE.md` and `_template.md`) is manually reviewed against both artifacts before merge. Reviewer records "STYLE-compliant" in the PR description.

#### ADR (meta-question)

**Decision:** Hold **option β** — skill is terminal-silent at contract level, terminal-aware at workflow boundary. Retain the bilateral lint as the floor. Ship two new convention artifacts as Phase 5 prerequisites: `STYLE.md` (negative reference, enumerates disallowed paraphrases) and `_template.md` (positive reference, worked exemplar). Do **not** relax the lint on the basis that "prompt (c) is clear."

**Drivers:**
1. Drift cost dominates context-locality benefit in a multi-author / multi-variant world — drift is the failure mode the parent doc was designed to prevent.
2. Community-skill compatibility is hard-required — paraphrase risk needs a runtime + review backstop, not lint alone.
3. New skill authors need scaffolding (positive reference), not just rules (negative reference).

**Alternatives considered:**
- **α** (terminal-silent everywhere): rejected — awkward workflow gap; bridging language disallowed; no path to mention "the decision point" as a milestone.
- **γ** (rich paraphrase): rejected — drift cost dominates context-locality benefit in a multi-author / multi-variant world; cache invalidation cascade on every catalog edit.

**Why β wins:**
- Matches existing lint design (no new floor); reuses the bilateral substring/key scan already specified in resolutions doc AC#4.
- Preserves bridging language so the skill drives to the decision moment without restating it.
- The new positive reference (`_template.md`) closes the bootstrapping gap that pure-negative artifacts left open.

**Consequences:**
- **Two new Phase 5 prerequisites** — `STYLE.md` and `_template.md` — that must land **before** the first production skill. This inverts what would otherwise be a natural "skills first, style guide later" cadence.
- **Manual review burden** — every shipped skill needs a "STYLE-compliant" sign-off in its PR description. Acceptable for v1 volume (two skills).
- **Paraphrase residual risk persists** — explicit example-bank detector remains a tracked follow-up; advisor pre-submission discipline is the named v1 backstop.
- **No change to v1 lint design or resolutions doc AC#4.**

**Follow-ups (deferred):**
- CODEOWNERS rule on `agents/profile/main/skills/*.md` requiring terminal-registry-owner review (resolutions doc §"Follow-ups").
- Example-bank paraphrase detector for Channel B (resolutions doc §"Follow-ups").
- Periodic snapshot review scanning shipped skills against a paraphrase example bank (linked to example-bank detector follow-up).
- Skill-quality eval suite measuring terminal-selection accuracy on a held-out scope set (resolutions doc §"Follow-ups").

---

### Process record (this addendum)

- **Consensus loop:** ralplan, single iteration, scoped to the meta-question only.
- **Architect:** APPROVE with one architectural addition — `_template.md` as positive reference closes the bootstrapping gap that STYLE.md alone could not.
- **Critic:** APPROVE with one carry-forward refinement — tighten AC#3 to enumerate grep-checkable section headers (incorporated above).
- **Q1–Q4:** no consensus loop required — pointer entries into already-approved resolutions; no new design content.
