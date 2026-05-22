# Implementation Plan: codify the optimized initial-messages design

Companion to [`OPTIMIZED_USER_MSG_1.md`](OPTIMIZED_USER_MSG_1.md). That document is the
**spec**; this document is the **migration plan** to make the renderer emit it.

## Principles
1. **Code-resident truth** — `TAG_DICTIONARY` and `ROLE_DIRECTIVES` are Python registries with completeness tests; the markdown spec is the golden, the registries enforce it.
2. **Walk the packet, not parsed XML** — `render_what_in_context` consumes `ContextPacket` blocks directly; no re-parsing of rendered output.
3. **Byte-equal to `OPTIMIZED_USER_MSG_1.md`** — the spec is the contract; the test suite enforces it case-by-case.
4. **Incremental migration** — one concern per PR; reversible at each step.
5. **Goldens flip intentionally** — `test_initial_messages_capture.py` snapshots are *expected* to change at the PR-3 and PR-4 boundaries; CI accepts those diffs when `OPTIMIZED_USER_MSG_1.md` was updated in the same PR.

## Decision drivers
1. Test stability — never break two test suites in one PR.
2. Reviewability — keep each PR's diff narrowly scoped.
3. Tag-drift correctness — current `_DEFAULT_TAGS` does NOT match `OPTIMIZED_USER_MSG_1.md` in several places (`iteration_statement → current_iteration` should be `iteration`+`status="current"`; `task_specification → attempt_plan` should drop the wrapper); the migration must fix these.

## Options considered
- **A — Single PR**: rename tags + replace builders + add `render_what_in_context` + regen. Rejected — diff too large to review safely.
- **B — Incremental, 5 PRs**: chosen.
- **C — Feature-flag both paths**: rejected — flag debt for a one-way migration.

## Current state vs target (concrete drift)

| Concern | Current (`renderer.py:_DEFAULT_TAGS` / `builders.py`) | Target (`OPTIMIZED_USER_MSG_1.md`) |
|---|---|---|
| `iteration_statement` tag | `current_iteration` | `iteration` + `attrs='status="current"'` |
| `failed_attempt` tag | `attempt` (no enforced attrs) | `attempt` + `attrs='status="prior" verdict="fail"'` |
| `task_specification` tag | `attempt_plan` (wrapper) | `plan_spec` (no wrapper) |
| Task Guidance body | hand-authored prose, 4 planner branches + 2 evaluator branches + 2 generator branches | `render_what_in_context(packet) + ROLE_DIRECTIVES[role] + render_terminal_catalog(...)` |
| Skill files | planner only | planner + executor + evaluator |

## The Plan — 5 PRs

### PR-1: Registries (no behavior change)

**Adds, doesn't wire.**

Files:
- `backend/src/task_center/context_engine/tag_dictionary.py`
  - `class TagDescriptor(BaseModel): tag: str; attr_filter: dict[str,str] | None; label: str`
  - `TAG_DICTIONARY: list[TagDescriptor]` — full table from `OPTIMIZED_USER_MSG_1.md`
  - `RECURSE_THROUGH: frozenset[str] = frozenset({"iteration"})`
  - `def match(tag: str, attrs: dict[str,str]) -> TagDescriptor | None`
- `backend/src/task_center/context_engine/role_directives.py`
  - `ROLE_DIRECTIVES: dict[str, str]` — agent name → one-line directive

Tests:
- `test_tag_dictionary_matches_optimized_md.py` — parse the markdown table; assert byte-equal to `TAG_DICTIONARY` labels.
- `test_role_directives_completeness.py` — every role with a task-guidance builder has a directive.

**Diff size**: ~150 LoC + ~80 LoC tests. Zero call sites changed.

### PR-2: `render_what_in_context` (no behavior change)

**Adds the renderer, still not wired.**

Files:
- `backend/src/task_center/context_engine/what_in_context.py`
  - `def render_what_in_context(packet: ContextPacket, max_depth: int = 2) -> str`
  - Algorithm: walk blocks in packet order; group by `metadata['group_id']`; for each top-level block/group emit `- <tag attrs> — label` from `TAG_DICTIONARY.match`; if `tag in RECURSE_THROUGH`, indent children one level.
  - Collapses consecutive same-descriptor siblings to one bullet.

Tests:
- `test_what_in_context_outlines.py` — one test per case in `OPTIMIZED_USER_MSG_1.md` (~11 cases); build a fixture `ContextPacket` matching each case's user_msg_1; assert output matches the spec's outline byte-for-byte.

**Diff size**: ~120 LoC + ~250 LoC fixtures and tests. Zero call sites changed.

### PR-3: Update tag emissions in recipes (intentional snapshot flip for user_msg_1)

**Modifies recipes to emit the target tag shape.**

Files:
- `recipes/iterations.py`: emit `<iteration status="current">` (not `<current_iteration>`); always emit even for iter1 attempt1 with `<iteration_goal>(identical to <goal>)</iteration_goal>` body.
- `recipes/attempts.py`: emit `<attempt status="prior" verdict="fail">` (not bare `<attempt>`); drop `<generator_outcomes>` and `<evaluator_judgment>` wrappers; promote children.
- `recipes/evaluator.py`: nest `<attempt status="current">` under `<iteration status="current">`; drop `<attempt_plan>` and `<completed_tasks>` wrappers; promote children.
- `recipes/generator.py`: drop `<dependency_results>` wrapper; emit `<dependency>` siblings directly; drop `<deferred_goal_for_next_iteration>` from executor packets.
- `renderer.py:_DEFAULT_TAGS`: update mapping table to match the spec.

Verification:
- Regenerate goldens via `scripts/regen_initial_messages_cases.py`.
- Focused-reference scenarios (`test_focused_scenarios.py::test_focused_reference_scenario_runs`) pass rate baseline-or-better.

**Diff size**: ~200 LoC recipe edits + ~10 golden files refreshed. user_msg_1 changes; user_msg_2 still hand-authored prose.

### PR-4: Replace `builders.py` (intentional snapshot flip for user_msg_2)

**Wires the registries.**

Changes:
- Delete the 6 branched prose builders in `task_center/task_guidance/builders.py`.
- New single builder: `build_task_guidance(agent_name: str, packet: ContextPacket, terminals: list[str]) -> str` that composes:

  ```
  What's in context:
  {render_what_in_context(packet)}

  What to do:
  - {ROLE_DIRECTIVES[agent_name]}

  <terminal_tool_selection>
  {render_terminal_catalog(terminals, focus="selection_guidance")}
  </terminal_tool_selection>
  ```

- `task_guidance_dispatch.py`: every role routes through the single builder; remove per-role dispatch.
- Drop the `Role:` line entirely.

Verification:
- Regenerate goldens.
- Focused-reference scenarios pass rate.

**Diff size**: −220 LoC (deleted branched builders) + ~80 LoC (new builder) + ~10 golden files refreshed.

### PR-5: Executor + evaluator skills (move heuristics to skills)

**Migrate operational heuristics out of (now-deleted) prose builders into skill MDs.**

Files:
- New skill MDs for executor and evaluator with the strategic heuristics previously in `builders.py`:
  - executor: "treat `<dependency>` outputs as fixed inputs", "verify deliverable exists at claimed location"
  - evaluator: "use `<evaluation_criteria>` as authority, not your preferences", "do not penalize for deferred work"
- Planner skill: already exists; sanity-check it carries the heuristics (one-criterion-per-item, etc.) that were duplicated in PR-4-deleted prose.

Wiring:
- `build_skill_message` to load executor/evaluator skills (already supported per `composer.py:81`).

Verification:
- Token budget check: each skill MD ≤ 2k tokens (assert via existing skill-message test pattern).

**Diff size**: 3 new skill MDs + minimal dispatch wiring.

## Acceptance criteria

- **AC1**: For each captured case in `test_initial_messages_capture.py`, generated `user_msg_1` matches `OPTIMIZED_USER_MSG_1.md` byte-for-byte.
- **AC2**: Same for `user_msg_2`.
- **AC3**: `test_focused_reference_scenario_runs` pass rate after PR-5 ≥ pre-PR-1 baseline.
- **AC4**: Adding a new launch position requires only a new scenario fixture; no recipe code, no template edits.
- **AC5**: Renaming a canonical label is one registry edit (verifiable by changing one row in `TAG_DICTIONARY` and observing all relevant cases update via regen).
- **AC6** *(per-PR gate)*: PRs 1–2 introduce zero golden churn. PRs 3–4 produce only the intended golden diffs (reviewed via snapshot review).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Tag-rename in PR-3 breaks `_validate_no_structural_closers` if previously-OK block bodies contain `</current_iteration>` etc. | Grep test fixtures for the old closers before merge; rewrite any offender. |
| `render_what_in_context` walker disagrees with the rendered XML on group membership | Use the *same* group-detection logic (`metadata['group_id']`) that `XmlPromptRenderer._render_blocks` uses; share a helper. |
| `_DEFAULT_TAGS` in renderer.py and `TAG_DICTIONARY` drift | Make `_DEFAULT_TAGS` derive from `TAG_DICTIONARY` at module load (single source). |
| Skill MDs bloat user_msg_3 | Per-skill token budget assertion in PR-5 (≤2k tokens each). |
| Architect's flag: codifying might freeze prompts mid-design | Registries are one-line-per-row Python dicts; editing a label requires only a regen, no code review for engineering correctness. |

## ADR

- **Decision**: Codify the optimized design as Python registries (`TAG_DICTIONARY`, `ROLE_DIRECTIVES`, `RECURSE_THROUGH`) + a `render_what_in_context` walker that consumes `ContextPacket`. Replace prose-branched `builders.py` with registry-driven Task Guidance composition. Migrate operational heuristics to skill MDs.
- **Drivers**: avoid drift between `OPTIMIZED_USER_MSG_1.md` and the renderer; reduce per-variant authoring cost; make extensions (new agent, new tag) a one-row change.
- **Alternatives considered**:
  - Single-PR atomic migration — rejected (review burden).
  - Feature-flag old vs new builder — rejected (flag debt for one-way migration).
  - Keep prose builders, lint `OPTIMIZED_USER_MSG_1.md` vs `builders.py` — rejected (drift still possible; lint is a check, not a guarantee).
  - YAML data file for labels (architect synthesis) — rejected (Python dict already cheap; no need for second format).
- **Consequences**:
  - 4 of the 5 PRs touch tests; PRs 3 & 4 intentionally flip goldens.
  - `builders.py` shrinks ~75% (only the registry dispatch remains).
  - 3 new skill files (executor, evaluator + planner sanity-check).
  - `Role:` line disappears from all user_msg_2 outputs.
- **Follow-ups**:
  1. After PR-3, audit the planner recipe for the same tag shapes.
  2. After PR-5, evaluate whether to merge `TAG_DICTIONARY` and the `TerminalToolDescriptor` registry into a single `prompt_artifacts/` module.
  3. Consider auto-generating `OPTIMIZED_USER_MSG_1.md` from the registries + a fixture set, making it derived rather than canonical.
