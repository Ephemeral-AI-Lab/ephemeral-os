# Skill as the Fourth Message — Design Doc

**Status:** Proposed
**Owner:** task-center maintainers
**Last updated:** 2026-05-18
**Related:** `docs/reports/first_three_messages_report.md`, `task_center/attempt/launch.py`, `agents/profile/main/`

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
