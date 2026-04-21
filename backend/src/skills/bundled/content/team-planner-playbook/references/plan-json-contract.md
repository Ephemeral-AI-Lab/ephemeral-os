# Plan JSON Contract
Use this reference as an optional final helper immediately before calling `submit_plan(...)`. It is not a planning guide; do not load it until exploration, DAG shaping, terminal background scouts, and required note reads are complete.

Use this only as the final schema checklist. After this reference loads, stop exploration and make the next assistant action the `submit_plan(...)` tool call. Do not emit recap prose, visible planning text, or another non-terminal tool call. If any background scout/subagent is still running, do not load this reference yet.

## Task/Goal

- You already have the owner ledger, deps, and task prose. Your only remaining work is putting the decided tasks into the tool input.

## Avoid

- Avoid summarizing what you will submit or saying "the plan is ready" / "let me submit".
- Do not make another tool call except `submit_plan(...)`.
- Do not call `wait_for_background_task(...)`, `check_background_progress(...)`, `cancel_background_task(...)`, CI, notes, or scout tools after this reference loads.
- Do not include `task_note`, `background`, `parent_id`, `rationale`, `output`, or `summary`.
- Do not use a failed `submit_plan(...)` result as your schema checker.

## Workflow

Build the schema-valid payload inside the `submit_plan(new_tasks=[...])` tool input. The response after this reference should contain the terminal tool call only.

Tool input checklist:

- Top-level key: `new_tasks` only. Do not include `output` or `summary` — the system generates the outcome summary automatically once your children complete.
- `new_tasks` is a JSON array.
- Each task has `id`, `description`, `name`, `spec`, `deps`, and non-empty `scope_paths`. This includes validators.
- `name` is an exact registered agent name such as `developer`, `validator`, or `team_planner`.
- `deps` is a top-level task field and every `id` is unique. Keep independent benchmark families parallel; do not add deps unless a task needs another task's concrete output or same-file edit ordering.
- `spec` uses numbered colon labels in this exact order, each at the start of its own line with content on the same line after the colon:
  `1. Goal:` <text>
  `2. Environment:` <text>
  `3. Scope:` <text>
  `4. Context:` <text>
  `5. Acceptance Criteria:` <text>
  Do not put all labels on one line. Do not put the section body on the next line after the colon. Do not use Markdown headings.
- `scope_paths` uses repo-relative live-confirmed production owner paths, adjacent supporting owners for the same likely fix, or a broader production boundary on `team_planner` when exact ownership is still uncertain. Do not submit `/testbed/...` prefixes. Validator `scope_paths` are the production files/directories being verified. Keep verification-only test targets in `spec` context or acceptance criteria unless the task explicitly owns a test-only bug.
- Missing modules, compatibility shims, re-export modules, and import bridges named by tests need production ownership evidence before entering `scope_paths`.
- An exact file with no indexed symbols is not a live-confirmed owner when workspace structure shows a directory or nested files for that owner family; use that directory or the confirmed nested files instead.
- Scope overlap is allowed. Do not add dependencies merely because `scope_paths` overlap; use `deps` only for real output ordering, known same-file edit ordering, or unresolved ownership that belongs in one child `team_planner`.
- Exactly one terminal `validator` end-of-chain guard when the layer has non-validator tasks. Child planners still need a child-layer validator when they later submit their own plan. Never submit a validator with `deps: []` when the plan has non-validator siblings. The terminal validator's `deps` must cover every same-layer non-validator sibling, including child planners like `plan-parquet` or `plan-groupby`. Mentioning dependencies inside `spec` does not set task deps.
- Do not include a child `team_planner` and that planner's would-be developer or validator children in the same `new_tasks` payload.
- Validator `spec` must include: (a) the full-suite test command covering all targets from the original benchmark/request, (b) the scoped re-check list of failing test ids from developer lanes, and (c) a `ci_diagnostics(file_path)` pre-check instruction for every `scope_paths` file.
- Child specs must not say `cd /testbed`, "run from /testbed", or add `2>&1`, output redirects, `| head`, or `| tail`; CodeAct commands start at repo root and capture output automatically.

## Expected Outcome

- The next tool call is the terminal `submit_plan(...)` call.
