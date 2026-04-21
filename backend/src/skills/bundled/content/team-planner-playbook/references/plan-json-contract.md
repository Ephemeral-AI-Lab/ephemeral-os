# Plan JSON Contract
Use this reference only when the plan is fully decided and your next action is the terminal `submit_plan(...)` call.

After this reference loads, emit no assistant prose, recap, "let me submit", or visible task list. The next assistant message must be exactly one `submit_plan(new_tasks=[...])` tool call. If any background scout/subagent is still running, or if you still need notes, CI, file reads, or schema thinking outside the tool input, do not load this reference yet.

## Task/Goal

- You already have the owner ledger, deps, and task prose. Your only remaining work is putting the decided tasks into the tool input.
- This is an optional final helper for schema-valid terminal submission, not a planning or discovery reference.
- Do not load it until exploration, DAG shaping, terminal background scouts, scout synthesis, and dependency checks are complete.

## Avoid

- Avoid summarizing what you will submit or saying "the plan is ready" / "let me submit".
- Do not make another tool call except `submit_plan(...)`.
- Do not call `wait_for_background_task(...)`, `check_background_progress(...)`, `cancel_background_task(...)`, CI, notes, or scout tools after this reference loads.
- Do not include `task_note`, `background`, `parent_id`, `rationale`, `output`, or `summary`.
- Do not use a failed `submit_plan(...)` result as your schema checker.

## Workflow

Build the schema-valid payload inside the `submit_plan(new_tasks=[...])` tool input. The response after this reference should contain the terminal tool call only.

Tool input checklist:

1. **Payload shape:** Top-level key is `new_tasks` only. `new_tasks` is a JSON array; every task has unique `id`, `description`, exact registered `name`, `spec`, top-level `deps`, and non-empty `scope_paths`, including validators. Do not include `output`, `summary`, `task_note`, `background`, `parent_id`, or rationale fields.
2. **Spec format:** `spec` uses numbered colon labels in exact order, each at the start of its own line with body text on that same line: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Do not combine labels on one line, put body text on the next line, or use Markdown headings.
3. **Scope paths:** Use repo-relative live-confirmed production owner paths, adjacent supporting owners for the same likely fix, or a broader production boundary on `team_planner` when exact ownership is still uncertain. Validator scopes are production files/directories being verified. Keep benchmark and verification tests in `spec` unless tests are explicitly owned.
4. **Dependencies and validators:** `deps` values must name ids in this same payload or existing Task Center ids explicitly read in this agent run; entry/root planners have no existing deps. Use deps only for real output ordering, known same-file edit ordering, or unresolved ownership delegated to one child `team_planner`. When the layer has non-validator tasks, include exactly one terminal validator whose deps cover every same-layer non-validator sibling, including child planners.
5. **Final safeguards:** Do not include a child `team_planner` and its would-be children in the same payload. Validator specs include the full-suite command, scoped failing-id rechecks, and `ci_diagnostics(file_path)` pre-checks for scope files. Child specs must not include repo-root `cd` wrappers, shell pipes, redirects, or stderr capture.

## Expected Outcome

- The next tool call is the terminal `submit_plan(...)` call.
