# Tool Docstring Upgrade Plan

Goal: lift `backend/src/tools/**` tool descriptions to the sophistication
level of Claude Code's `c c/src/tools/**/prompt.ts` files.

## Reference Inventory — Claude Code `prompt.ts` files

Located under `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/`:

- AgentTool/prompt.ts
- AskUserQuestionTool/prompt.ts
- BashTool/prompt.ts
- BriefTool/prompt.ts
- ConfigTool/prompt.ts
- EnterPlanModeTool/prompt.ts
- EnterWorktreeTool/prompt.ts
- ExitPlanModeTool/prompt.ts
- ExitWorktreeTool/prompt.ts
- FileEditTool/prompt.ts
- FileReadTool/prompt.ts
- FileWriteTool/prompt.ts
- GlobTool/prompt.ts
- GrepTool/prompt.ts
- ListMcpResourcesTool/prompt.ts
- LSPTool/prompt.ts
- MCPTool/prompt.ts
- NotebookEditTool/prompt.ts
- PowerShellTool/prompt.ts
- ReadMcpResourceTool/prompt.ts
- RemoteTriggerTool/prompt.ts
- ScheduleCronTool/prompt.ts
- SendMessageTool/prompt.ts
- SkillTool/prompt.ts
- SleepTool/prompt.ts
- TaskCreateTool/prompt.ts
- TaskGetTool/prompt.ts
- TaskListTool/prompt.ts
- TaskStopTool/prompt.ts
- TaskUpdateTool/prompt.ts
- TeamCreateTool/prompt.ts
- TeamDeleteTool/prompt.ts
- TodoWriteTool/prompt.ts
- ToolSearchTool/prompt.ts
- WebFetchTool/prompt.ts
- WebSearchTool/prompt.ts

## Patterns observed in Claude Code

1. **Separate `prompt.ts` module** per tool: description content lives next to
   the tool, isolated from implementation logic.
2. **Cross-tool name constants** (`FILE_READ_TOOL_NAME`, `BASH_TOOL_NAME`, etc.)
   imported by other tools so references stay in sync.
3. **Dynamic composition** via `getDescription()` / `renderPromptTemplate()` /
   `getPrompt()` functions assembling the description from feature flags, env
   vars, sandbox config, and subscription type.
4. **Stable IA**: header → Usage → Instructions → Constraints → (Sandbox) →
   (Git) → Examples.
5. **`<example>` + `<commentary>`** blocks for non-obvious behaviors.
6. **Behavioral discipline** (e.g., AgentTool: "Don't peek", "Don't race",
   "Never delegate understanding", "Writing the prompt").
7. **Constants extracted** (`MAX_LINES_TO_READ`) and interpolated into prose so
   docstring claims cannot drift from code.

## Current EphemeralOS state

In `backend/src/tools/`:

- Descriptions are inline string literals on the `@tool(...)` decorator.
- `shell.py` is the gold standard: Use / Prefer / Do NOT / Capabilities /
  Output / Pitfalls sections.
- `edit_file`, `grep`, `read_file`, `run_subagent`, `write_file`, `glob` are
  4–7 lines: terse, missing pitfalls, no cross-tool guidance.
- Tool names are hardcoded inside other tools' prose (no `_names.py`).
- No shared fragments (audit-trail wording, line-number format note,
  sandbox-paths note duplicated where present).
- No `<example>` blocks.

## RALPLAN-DR Summary

### Principles
1. **Sophistication of structure, not invented constraints.** No drift from
   actual tool behavior.
2. **Composition over duplication.** Tool names + shared phrases live in one
   place.
3. **One module per concern.** Logic in `<tool>.py`, prompt in
   `<tool>/_prompt.py` (or sibling), parameter docs stay on Pydantic `Field`.
4. **Cache-stable text.** No runtime variants unless behavior demands.
5. **Verifiable accuracy.** Numeric/named claims interpolated from constants.

### Decision Drivers
1. Highest leverage on cheap-tool descriptions (`edit_file`, `grep`,
   `read_file`, `run_subagent`).
2. Reuse Claude Code's IA verbatim where applicable — proven, no need to
   invent.
3. Skip feature-flag sprawl. Anthropic has many `isEnvTruthy` gates;
   EphemeralOS has fewer runtime variants — don't import speculatively.

### Viable Options

**Option A — Inline literal upgrade (minimal change).**
Keep `description=(...)` in the `@tool(...)` decorator; expand each to match
`shell.py`'s depth, add cross-tool references.
- Pros: no module restructuring; one PR per tool; no import-cycle risk.
- Cons: tool-name strings duplicated; sharing fragments is awkward; 100+ line
  string literals make `*.py` files hard to scan.

**Option B (Recommended) — Sibling `_prompt.py` per tool.**
Factor description out to `tools/<group>/_prompts/<tool>.py` (or
`tools/<group>/<tool>_prompt.py`) exporting a `get_description()` function.
Constants in `tools/_names.py`.
- Pros: mirrors Claude Code's structure; central naming; description stops
  competing with implementation; composable fragments trivial.
- Cons: more files; circular-import risk (mitigated: keep `_names.py`
  literals-only); touches every tool's decorator call.

**Option C — Single shared registry module.**
One `tools/_descriptions.py` holding every description as a function.
- Pros: one file to edit; trivial cross-references.
- Cons: ~2000-line bus-factor file; breaks locality; doesn't match the proven
  Claude Code template.

**Recommendation: Option B.** It is the structural lift that *makes* the
content sophistication sustainable. Option A produces 8 sophisticated
docstrings today but rots; the next agent inline-duplicates fragments and
wording drifts.

### Steelman antithesis (Architect-style pushback)
- *"You don't need module separation — `shell.py` is already excellent
  in-decorator."* True for `shell.py`. The asymmetry argues *for* Option B:
  when a description is 4 lines (`edit_file`) inline is fine; the day someone
  adds 30 lines, the seam should already exist.
- *"This is bikeshedding."* These descriptions are tool-use system prompts —
  the highest-leverage prompt-engineering surface in the codebase aside from
  agent system prompts.

### Tradeoff tension
Module separation helps future maintainers but adds one PR of churn now
(touching every `@tool(...)` decorator). Mitigation: ship Option B as a
mechanical move-only refactor PR (Phase 1), then content upgrades per tool
group (Phase 2). Two PRs > one PR with mixed concerns.

## Plan

### Scope
- **In:** `backend/src/tools/sandbox/{shell,edit_file,grep,read_file,write_file,glob}.py`,
  `tools/subagent/run_subagent.py`, `tools/background/*.py`,
  `tools/ask_helper/*.py`, `tools/submission/**.py`.
- **Out:** `_framework/` plumbing, `_terminals/`, `skills/` (wiring, not
  user-facing tools).

### Phases

**Phase 1 — Structural seam (mechanical, zero content change).**
1. Add `backend/src/tools/_names.py` with tool-name string constants:
   `READ_FILE_TOOL_NAME`, `EDIT_FILE_TOOL_NAME`, `WRITE_FILE_TOOL_NAME`,
   `SHELL_TOOL_NAME`, `GREP_TOOL_NAME`, `GLOB_TOOL_NAME`,
   `RUN_SUBAGENT_TOOL_NAME`, `CHECK_BACKGROUND_TASK_RESULT_TOOL_NAME`,
   `WAIT_BACKGROUND_TASKS_TOOL_NAME`, etc.
2. For each in-scope tool, create a sibling `_prompt.py` exporting
   `get_<tool>_description() -> str` returning the **current** description
   verbatim. Decorator becomes `description=get_shell_description()`.
3. Verify: `pytest -q backend/tests/unit_test/test_tools` passes unchanged.
   Schema snapshots (if any) unchanged byte-for-byte.

**Phase 2 — Content upgrade per tool, using Claude Code's IA.**
Restructure each description into these sections (omit empty ones):

```
<one-line summary>

Use this when:
  - <concrete trigger>
  ...

Prefer dedicated tools / alternatives:
  - <X> -> <Y> when ...

Do NOT use for:
  - <antipattern with reason>

Capabilities and constraints:
  - <limits, defaults, caps — pulled from constants, not prose>

Output shape:
  - <field>: <semantics>

Common pitfalls:
  - <concrete failure mode + fix>

Examples (only when behavior is non-obvious):
  <example>...</example>
```

Per-tool deltas:
- **`read_file`** — spell out line-numbered output format; `MAX_READ_FILE_LINES`
  cap referenced from constant; "don't re-read after `edit_file`" guidance;
  behavior on non-UTF-8 / binary; directory-listing antipattern.
- **`edit_file`** — "must use `read_file` first" preamble (mirrors
  `getPreReadInstruction()` in Claude Code); exact-match whitespace caveat;
  `aborted_version` semantics; when to widen `old_text` vs. split into multiple
  edits.
- **`grep`** — already strong; add "When NOT to use" (don't grep to read whole
  files; don't use for cross-file structural search); add a `multiline` example.
- **`shell`** — already excellent; minor edits to reference other tool names via
  `_names.py` instead of hardcoded `"read_file"` / `"write_file"` strings.
- **`run_subagent`** — adopt Claude Code's "Writing the prompt" section nearly
  verbatim ("Brief the agent like a smart colleague…"); add "Don't peek / Don't
  race" guidance; concrete `<example>` blocks (good vs. bad spawn prompt).
- **`write_file`** — emphasize "prefer `edit_file` for existing files" (analog
  of Claude Code's `Write` description warning); empty-file behavior.
- **`glob`** — output ordering; glob syntax variant (fnmatch vs. shell);
  filename-only scope.

**Phase 3 — Shared fragments.**
Extract into `backend/src/tools/_prompt_fragments.py`:
- `AUDIT_TRAIL_NOTE` — "Writes are tracked; commands that exit 0 but write
  outside the audited boundary return is_error=True with..."
- `SANDBOX_PATHS_NOTE` — "Paths are repo-relative or sandbox-absolute."
- `LINE_NUMBER_FORMAT_NOTE` — used by `read_file` and `edit_file`.

Composed via f-strings in each `_prompt.py`. No runtime flags.

**Phase 4 — Verification.**
- Schema regen / introspection catalog round-trip: assert
  `Catalog.get("shell").description` matches the new output (snapshot test,
  not equality to old).
- Smoke a real agent against one upgraded tool (`shell`) to confirm no
  behavior regression. (Optional — description-only changes.)
- `ruff check` / `make test` green.

### Acceptance Criteria
1. Every in-scope tool's `description` is produced by a
   `get_<tool>_description()` function in a sibling `_prompt.py`.
2. Tool names referenced across descriptions come from `tools/_names.py` — no
   string literals like `"read_file"` inside another tool's description.
3. Each upgraded description contains, at minimum: one-line summary, "Use
   this when", "Do NOT use for", and "Output shape" (or rationale why a
   section is omitted).
4. `pytest`, `ruff check` green.
5. Diff is reviewable: Phase 1 PR is mechanical (zero content change,
   verified by `git diff` showing only function-extraction); Phase 2 PRs are
   one-per-tool-group with content changes.

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Circular imports between `_names.py` and tools | Keep `_names.py` literal-constants-only, no imports. |
| Description drift from actual behavior | In Phase 2, every numeric/named constant in prose is referenced via f-string (`f"... {MAX_READ_FILE_LINES} ..."`). |
| Over-engineering with feature flags | Explicitly out of scope — no `isEnvTruthy` analogs unless a real runtime variant exists. |
| Prompt-cache invalidation churn during rollout | Phase 1 + Phase 2 are content-changing — accept one-time miss; cache-stable afterward. |

## ADR

- **Decision:** Adopt Option B (sibling `_prompt.py` per tool +
  `tools/_names.py`).
- **Drivers:** Highest leverage on `run_subagent` / `edit_file` / `read_file`;
  sustainability over a single sweep.
- **Alternatives considered:**
  - Option A (inline upgrade) — rejected: bit-rots.
  - Option C (central registry) — rejected: kills locality.
- **Why chosen:** Mirrors Claude Code's proven IA without importing its
  feature-flag complexity.
- **Consequences:** +1 file per tool; future tool template includes
  `_prompt.py`.
- **Follow-ups:** Once stable, consider whether
  `task_guidance/builders.py` strings should adopt the same fragment pattern
  (out of scope here).
