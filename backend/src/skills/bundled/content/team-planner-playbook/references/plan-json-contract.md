# Plan JSON Contract
Use this reference as an optional final helper immediately before calling `submit_plan(...)`. It is not a planning guide.

STOP READING AFTER THE CHECKLIST AND CALL THE TOOL. After this reference loads, your next assistant turn must contain one `submit_plan(...)` tool call and no text block. Do not write a recap, checklist, JSON preview, or "let me call submit_plan now" sentence.

## Task/Goal

- You already have the owner ledger, deps, and task prose. Your only remaining work is putting the decided tasks into the tool input.

## Avoid

- Do not summarize what you will submit or say "the plan is ready" / "let me submit".
- Do not make another tool call except `submit_plan(...)`.
- Do not include `task_note`, `background`, `parent_id`, `rationale`, or `output: null`.
- Do not use a failed `submit_plan(...)` result as your schema checker.

## Workflow

Call `submit_plan(new_tasks=[...])` now.

Tool input checklist:

- Top-level keys: `new_tasks` and optional string `output` only.
- Each task keys: `id`, `description`, `name`, `spec`, `deps`, `scope_paths`. `name` is an exact registered agent name such as `developer`, `validator`, or `team_planner`. `deps` is a top-level task field and every `id` is unique.
- `spec` uses numbered colon labels in this exact order: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Do not use Markdown headings.
- `scope_paths` uses live-confirmed production owner paths, adjacent supporting owners for the same likely fix, or a broader production boundary on `team_planner` when exact ownership is still uncertain. Keep verification-only test targets in `spec` context or acceptance criteria unless the task explicitly owns a test-only bug.
- Missing modules, compatibility shims, re-export modules, and import bridges named by tests need production ownership evidence before entering `scope_paths`.
- An exact file with no indexed symbols is not a live-confirmed owner when workspace structure shows a directory or nested files for that owner family; use that directory or the confirmed nested files instead.
- Pairwise overlap check: no two parallel concrete non-planner tasks may share an exact `scope_paths` file. If they do, merge them, add a `deps` edge, or make the shared file one child `team_planner` surface before this terminal call.
- Exactly one terminal `validator`. Never submit it with `deps: []` when the plan has non-validator siblings. The validator's `deps` must contain every same-layer non-validator sibling id, including child planners like `plan-parquet` or `plan-groupby`. Mentioning dependencies inside `spec` does not set task deps.
- Validator `spec` must include: (a) the full-suite test command covering all targets from the original benchmark/request, (b) the scoped re-check list of failing test ids from developer lanes, and (c) a `ci_diagnostics(file_path)` pre-check instruction for every `scope_paths` file.

## Expected Outcome

- The next assistant response is the terminal `submit_plan(...)` tool call, not prose.
