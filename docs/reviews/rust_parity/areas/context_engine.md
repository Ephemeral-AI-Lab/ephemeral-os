# Rust Parity Audit — Context Engine (role packets from store state, workflow-only)

Domain: agent-core. Audited Rust: `agent-core/crates/eos-workflow/src/context/` (engine.rs, composer.rs, scope.rs, section.rs, xml.rs, mod.rs). Ground truth: `backend/src/workflow/context_engine/` plus `docs/architecture/workflow/context-engine.html`.

Verdict in one line: the role-scoped `AgentContext` projection (planner/generator/reducer), XML rendering, scope identity, recipe validation, and workflow-only routing are a faithful port. The substantive gap is **outside the typed projection**: the Rust `<terminal_tool_selection>` block in `composer.rs` is rendered with a hand-rolled format that diverges from Python's `render_terminal_catalog` (different bullet syntax, separator, and an extra header line). That block is appended to row-3/row-4 launch messages, so it is on the workflow launch path even though it is not part of the `context/` typed object proper.

---

## Ground truth

Docs:
- `docs/architecture/workflow/context-engine.html` — Scope/Verdict (§scope-verdict), role shapes table (§role-shapes), guidance wire shape (§guidance-wire-shape). Key claims: `ContextEngine.build(...)` dispatches by `scope.role` and returns `AgentContext`; lifecycle stays in orchestrators; planner sees `<workflow>` + prior-iteration + previous-attempt evidence; generator/reducer see `<dependencies>` + `<assigned_task>`; reducer no longer gets `<assigned_prompt>`.

Python (behavioral ground truth):
- `backend/src/workflow/context_engine/engine.py` — `ContextEngine`, `ContextEngineDeps`, `validate_context_recipe` (78-87), `build_agent_context` dispatch (90-98), `build_planner_context` (100-153), `_build_execution_context` (164-199), `_prior_iteration_sections` (202-221, filter `>=` at 209), `_previous_attempt_sections` (224-247, filter `>=` at 232), `_dependency_sections` (250-281), `_execution_role` (284-287).
- `backend/src/workflow/context_engine/context.py` — `ContextSection` (9-14), `AgentContext` (17-22).
- `backend/src/workflow/context_engine/scope.py` — `ContextScope`, `require_field` (36-41), `for_planner/for_generator/for_reducer` (50-100).
- `backend/src/workflow/context_engine/xml.py` — `render_context_xml` (11-17), `render_task_outcome` (20-29), `render_section` (32-40), `_render_attrs` (43-51).
- `backend/src/workflow/context_engine/task_guidance.py` — `render_task_guidance` (8-17), `_render_context_contents` (20-36), `_render_context_limits` (39-42), `_render_what_to_do` (45-46).
- `backend/src/workflow/context_engine/skill_message.py` — `_render_terminal_tool_selection_block` (21-33), `wrap_task_guidance` (36-50), `build_skill_message` (53-97).
- `backend/src/workflow/context_engine/exceptions.py` — `ContextEngineError`/`RecipeScopeError`/`MissingContextRecipeError`/`AgentDefinitionValidationError`.
- `backend/src/workflow/agent_launch/composer.py` — `AgentEntryComposer.compose` (36-56) is the single workflow launch entry that drives the engine.
- `backend/src/workflow/_core/outcomes.py` — `parse_outcomes_record` (156-177), `execution_outcomes_from_row`/`task_outcomes_from_row` (43-73), `attempt_execution_outcomes` (93-99), `present_status` (39-40), `_normalize_status` (187-191).
- `backend/src/config/markdown.py` — `parse_markdown_frontmatter` (10-25).
- `backend/src/tools/_terminals/registry.py` — `render_terminal_catalog` (99-121), `TERMINAL_DESCRIPTORS` (36+).
- Launch call site (workflow-only): `backend/src/workflow/attempt/launch.py:413,437,456` build scopes, `:481` calls `compose`.

---

## Rust mapping

- `agent-core/crates/eos-workflow/src/context/engine.rs` — `ContextEngineDeps` (14-30), `ContextEngine` (33-69 incl. `build` dispatch 56-69), `build_planner_context` (71-129), `build_execution_context` (131-166), `prior_iteration_sections` (168-203, filter `>=` at 183), `previous_attempt_sections` (205-240, filter `>=` at 216), `dependency_sections` (242-276), `validate_context_recipe` (279-292), local `parse_outcomes_record` (294-299), `execution_role` (301-307).
- `agent-core/crates/eos-workflow/src/context/section.rs` — `ContextRole` (5-26), `ContextSection` (29-76), `AgentContext` (79-90).
- `agent-core/crates/eos-workflow/src/context/scope.rs` — `ContextScope` (8-20), `for_planner/for_generator/for_reducer` (24-71), private id accessors (73-95).
- `agent-core/crates/eos-workflow/src/context/xml.rs` — `render_context_xml` (7-12), `render_task_outcome` (14-28), `render_section` (30-48), `escape` (50-58).
- `agent-core/crates/eos-workflow/src/context/composer.rs` — `AgentEntryComposer.compose` (50-83), `render_task_guidance` (87-115), `wrap_task_guidance` (117-124), `build_skill_message` (126-150), `strip_frontmatter` (152-160), `terminal_selection_block` (162-186).
- Supporting projection (other crate, traced across): `agent-core/crates/eos-state/src/outcomes.rs` — `attempt_execution_outcomes` (126-134), `project_attempt_outcomes` (101-119); enums `TaskOutcomeStatus` (19-26), `ExecutionRole` (30-37), `ExecutionTaskOutcome` (41-51).
- Workflow-only launch call site: `agent-core/crates/eos-workflow/src/attempt/launch.rs:301` calls `composer.compose(...)`; `agent-core/crates/eos-runtime/src/entry.rs:120-126` only *constructs* the engine/composer.

---

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | ContextEngine builds role packets from STORE STATE for WORKFLOW agents only (not root, not subagents) | match | n/a | `agent_launch/composer.py:36-56`; only caller `attempt/launch.py:481` | `context/composer.rs:50-83`; only caller `attempt/launch.rs:301`; `runtime/entry.rs:120-126` constructs only | Both: the composer/engine are invoked exclusively from the attempt (workflow) launch path. Root request path (`runtime/entry.py`) mints `Task(role=root)` and runs the root agent directly — it never routes through `ContextEngine.build`. Recipe validation restricts roles to `{planner, generator, reducer}` (`engine.py:54` / `engine.rs:281`), structurally excluding root/subagent roles. |
| 2 | Lifecycle policy lives in workflow handlers/managers, NOT in context construction | match | n/a | `engine.py` (no retry/terminal/close logic; pure store reads); docs §scope-verdict "engine does not own lifecycle policy" | `engine.rs` (pure store reads, returns `AgentContext`); no close/retry/terminal-submission logic | Both engines only read `workflow_store`/`iteration_store`/`attempt_store`/`task_store` and project. No mutation, no status transitions, no terminal routing. `ContextEngineDeps` is a frozen/`Clone` store bundle on both sides. |
| 3 | Packet composition varies by role/scope (planner vs generator vs reducer differ) | match | n/a | `build_agent_context` match `engine.py:90-98`; planner `100-153`, exec `164-199` | `build` match `engine.rs:56-69`; planner `71-129`, exec `131-166` | Planner → single `<workflow>` with `goal` + optional `prior_iterations` + `current_iteration`(`goal` + optional `previous_attempts`); 2 `context_limits`; directive "Plan generator and reducer tasks for `<current_iteration><goal>`.". Generator/reducer → optional `<dependencies>` + `<assigned_task task_id>`; empty `context_limits`; directive "Complete `<assigned_task>` using `<dependencies>`.". Reducer uses `assigned_task` (not `assigned_prompt`) — confirmed by Rust test `build_reducer_context_uses_assigned_task` asserting `!xml.contains("<assigned_prompt>")`. |
| 4 | XML rendering of context sections is preserved | match | n/a | `xml.py:11-51` | `xml.rs:7-58` | Same root `<context role="...">`, same recursive `render_section` (open tag, escaped text, children joined by `\n`, close tag), trailing `\n` appended once at root, insertion-ordered attributes. `escape` ordering (`&`,`<`,`>`,`"`→`&quot;`,`'`→`&#x27;`) is byte-identical to Python `html.escape(s, quote=True)` (verified empirically incl. pre-escaped `&amp;`→`&amp;amp;`). Rust golden test `render_context_xml_golden` pins the exact bytes. |

Operator/constant comparison (explicitly requested):
- Prior-iteration skip filter: Python `iteration.sequence_no >= current_sequence` (engine.py:209) == Rust `iteration.sequence_no >= current_sequence` (engine.rs:183). **`>=` both sides.**
- Previous-attempt skip filter: Python `attempt.attempt_sequence_no >= current_attempt.attempt_sequence_no` (engine.py:232) == Rust `attempt.attempt_sequence_no >= current.attempt_sequence_no` (engine.rs:216). **`>=` both sides.**
- Sort keys: Python `sorted(..., key=sequence_no)` / `key=attempt_sequence_no` == Rust `sort_by_key(sequence_no)` / `sort_by_key(attempt_sequence_no)` (engine.rs:180,213). Stable sort both sides.
- Valid recipe set: Python `frozenset(("planner","generator","reducer"))` (engine.py:54) == Rust `matches!(recipe_id, "planner" | "generator" | "reducer")` (engine.rs:281).
- Dependency missing-outcome fallback: Python requires `task.get("status") != "done"` to raise, else synthesizes `("(no outcome recorded)", status="success", role=...)` (engine.py:262-273) == Rust requires `task.status != TaskStatus::Done` to raise, else synthesizes identical fallback (engine.rs:256-267). Literal string `"(no outcome recorded)"` matches.
- Directives, `context_limits` strings, and `render_task_guidance` prose are string-literal identical (engine.rs:123-127,163; composer.rs:89-99).

---

## Disparities

### D1 — Terminal-tool-selection block format diverges from Python (MEDIUM)
**Evidence.** Python builds the block from the shared registry:
- `skill_message.py:30-33`: `catalog = render_terminal_catalog(list(agent_def.terminals), focus="selection_guidance")` then wraps `f"<terminal_tool_selection>\n{catalog}\n</terminal_tool_selection>"`.
- `registry.py:120`: each row is `` f"- `{descriptor.name}` — {text}" `` (backtick-wrapped name, em-dash `—` separator), and rows are joined with `"\n\n"` (registry.py:121, blank line between rows).
- There is **no** "Pick exactly one based on outcome:" header anywhere in the Python source (`grep "Pick exactly one"` returns zero hits in `backend/src`).

Rust hand-rolls a different block in `composer.rs:172-184`:
- Row: `format!("- {}: {}", desc.name.as_str(), desc.selection_guidance)` — **no backticks**, **colon `:`** separator instead of `` ` `` + ` — `.
- Rows joined with `"\n"` (composer.rs:183) — **no blank line** between rows.
- Prepends a header: `"<terminal_tool_selection>\nPick exactly one based on outcome:\n\n{rows}\n</terminal_tool_selection>"` (composer.rs:182) — an **extra line that does not exist in Python**.

So for a profile with terminals, the row-3 `<Task Guidance>` and row-4 skill block bytes differ between Python and Rust. (The per-terminal `selection_guidance` *text* itself does match between `registry.py:39+` and `terminal.rs:90-117`; only the surrounding bullet/header formatting diverges.)

**Why it matters.** This block is appended to the launch messages on every workflow agent launch (composer.rs:75-79 / `agent_launch/composer.py:49-50`), so it is live prompt content the model sees. It changes the exact bytes of the planner/generator/reducer/explorer system-adjacent guidance. It is also covered by the Python AC#15 byte-equality contract between row-3 and row-4. Rust preserves AC#15 *internally* (both rows call the same `terminal_selection_block`, composer.rs:119,145-147), but the cross-language byte shape is different.

**Suggested fix.** Make `terminal_selection_block` mirror `render_terminal_catalog`: drop the `"Pick exactly one based on outcome:"` header line, render each row as `` - `{name}` — {selection_guidance} ``, and join rows with `"\n\n"`. Confirm against the Python `test_initial_messages_capture` / `test_descriptor_registry` fixtures.

### D2 — Unknown/unparseable terminal: Python emits a fallback bullet, Rust silently drops it (LOW)
**Evidence.** Python `render_terminal_catalog` (registry.py:115-118): when a terminal name has no descriptor, it appends `` f"- `{terminal}` — (no descriptor registered for this terminal)" `` (a visible fallback row). Rust `terminal_selection_block` (composer.rs:165-169): on a name that fails `parse::<ToolName>()` *or* `TerminalTool::from_tool_name(...)` returns `None`, it `continue`s — the terminal vanishes from the block with no trace.

**Why it matters.** If a profile lists a terminal the Rust enum does not know, Python surfaces the drift in the prompt (and a static test catches it); Rust silently omits it, so the model is never told that terminal exists and the divergence is invisible at runtime. Combined with D1's "Pick exactly one" framing, a dropped terminal could change which terminal the agent picks.

**Suggested fix.** Match Python: emit a fallback row for unresolved terminal names rather than `continue`, or assert/log on the unknown name. Lower priority than D1.

### D3 — `parse_outcomes_record` is strict in Rust, lenient in Python (LOW)
**Evidence.** Python `parse_outcomes_record` (outcomes.py:156-177): on a non-list value returns `()`, and skips any non-dict record (`if not isinstance(record, dict): continue`); `_normalize_status` coerces any unknown status to `"failed"`. The planner path therefore *skips a malformed iteration's outcomes silently* and still builds. Rust `engine.rs:294-299` does `serde_json::from_str::<Vec<ExecutionTaskOutcome>>(raw)` and propagates the `serde_json` error (`engine.rs:189` `?`), so malformed/legacy `iteration.outcomes` (non-array, missing fields, unknown status string) **errors the whole planner build** instead of skipping.

**Why it matters.** Only triggers on corrupt or legacy-shaped persisted `iteration.outcomes`. In the migrated/typed world the column is already normalized, so this is unlikely in practice — but it is a behavior change (skip-and-continue vs hard-fail) in an audited file.

**Suggested fix.** If legacy/corrupt iteration outcomes are possible post-migration, make `parse_outcomes_record` tolerant (return empty on parse failure) to match Python's skip semantics; otherwise document the stricter contract as intentional.

### D4 — Cross-boundary: dependency/attempt outcome status normalization (MEDIUM, verify in eos-db / eos-state audit)
**Evidence.** Python `_dependency_sections` (engine.py:260) and `project_attempt_outcomes` (outcomes.py:88-90) both go through `execution_outcomes_from_row` → `task_outcomes_from_row` (outcomes.py:43-66), which on a record with a **missing** `status` fills it via `present_status(task.status)` (outcomes.py:58): `"done" → "success"`, else `"failed"`. Rust reads pre-normalized `task.outcomes` directly (engine.rs:254 `task.outcomes.clone()`; outcomes.rs:114-115). The Rust `outcomes.rs` module docstring (lines 53-58) **explicitly flags two distinct normalizers**: `present_status` (done→success) used for the *missing-status* fill, vs `_normalize_status` (done→failed) at the eos-db parse boundary for an *already-present* status.

**Why it matters.** In the normal persisted case (record already carries `status`+`role`), Python's `setdefault` is a no-op and output is identical to Rust — so this is **intentional migration, not a context-engine bug**. The single discriminating question is: *when eos-db fills a missing per-record status on a `done` task, does it use `present_status` (→success, matching Python's row path) or `_normalize_status` (→failed)?* If eos-db applies `_normalize_status`, the dependency `<dependency>` outcome status and the attempt-recompute path silently flip success↔failed for status-less records. This is **systemic** (same path feeds `dependency_sections` and `previous_attempt_sections` via `attempt_execution_outcomes`).

**Suggested fix.** Confirm in the eos-db / eos-state parse-boundary audit that missing-status fill on a row outcome uses the `present_status` (done→success) semantics. No change in `context/` itself; this is a note to verify the upstream boundary the context engine now depends on.

---

## Extra findings

- **E1 (info, latent).** `xml.rs:18-25` (`render_task_outcome` role/status) and `engine.rs:233` (attempt `status`) render enum values via `format!("{:?}", x).to_lowercase()` rather than the serde `snake_case` name or `.as_str()`. Correct **today** because every relevant variant is a single token (`Generator`→`generator`, `Reducer`→`reducer`, `Success`→`success`, `Failed`→`failed`, `Passed`→`passed`, `Running`→`running`) — verified against Python `AttemptStatus`/`ExecutionRole`/`TaskOutcomeStatus` string values. A future multi-word variant (e.g. `InProgress`) would render `inprogress`, silently diverging from Python's `.value`/serde. Prefer `serde_json`/`as_str` to be future-proof.
- **E2 (info).** `strip_frontmatter` in `composer.rs:152-160` is a hand-rolled approximation of Python `parse_markdown_frontmatter` (markdown.py:10-25). Differences: Python tolerates leading whitespace on the delimiter line (`line.strip() == "---"`) and finds the closing `---` on its own stripped line; Rust requires the file to start *exactly* with `---` (no BOM/leading whitespace) and matches the closing delimiter via `split_once("\n---")`, which would also match a non-standalone `---more`. Both produce the body text only (frontmatter is discarded for the skill body), and for well-formed skill files (`---\n...\n---\n`) they agree. Edge-case divergence only; skill files in this repo are well-formed. Low/no practical impact, but noted since it is in an audited file.
- **E3 (info, positive).** Rust correctly threads the "recompute from task rows when attempt outcomes not yet persisted" path: `previous_attempt_sections` (engine.rs:219-220) calls `attempt_execution_outcomes(&attempt, Some(task_store))`, and `eos-state` `attempt_execution_outcomes` (outcomes.rs:126-134) returns persisted outcomes when present else `project_attempt_outcomes` — a faithful port of Python `attempt_execution_outcomes` (outcomes.py:93-99). Empty-string `iteration.outcomes` is handled: Rust `parse_outcomes_record` short-circuits on `raw.trim().is_empty()` (engine.rs:295) matching Python `if not value: return ()`.
- **E4 (info, positive).** Recipe/role mismatch error path matches: Python raises `RecipeScopeError`/`MissingContextRecipeError` (engine.py:80-87); Rust raises `WorkflowError::Recipe` with messages containing "unknown context recipe" and "cannot build role" (engine.rs:281-290), pinned by Rust test `build_rejects_recipe_role_mismatch`. (Error *type* names differ but both are workflow-level recipe errors; behavior is equivalent.)

---

## Open questions

1. **D4 resolution** lives in the eos-db / eos-state parse-boundary audit: does missing-status fill on a row outcome use `present_status` (done→success) or `_normalize_status` (done→failed)? This determines whether dependency/attempt outcome statuses can silently flip for status-less records.
2. **D1/D2** depend on the eos-tools terminal-catalog audit for the canonical block format. Is the Rust `<terminal_tool_selection>` format (header + colon rows) an intentional new wire shape, or an unintended drift from Python `render_terminal_catalog`? The Python AC#15 byte-equality contract is preserved within Rust but the cross-language shape differs; confirm which side is the intended target for the migrated prompt.
3. Whether any post-migration code path can still hand the context engine a non-array/legacy `iteration.outcomes` string (governs whether D3's strictness is a real risk or dead defensive concern).
