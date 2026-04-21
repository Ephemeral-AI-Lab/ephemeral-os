# Action Reference: submit_replan (add corrective tasks)

Use this reference for `submit_replan(new_tasks=[...], cancel_ids=[])` when existing siblings stay valid and only need corrective follow-up.
If your final payload needs any `cancel_ids`, stop and load `action-cancel-and-redraft` instead.

## Task/Goal

- Use this only when the failure shows scope expansion, wrong owner/role assignment, or a blocker that needs a different investigation path. Do not replan merely because a developer stopped before making a small in-scope edit; that belongs in the developer's terminal summary as incomplete work unless ownership changed.

## Avoid

- Do not add tasks that duplicate work already covered by existing siblings.
- Do not add a developer task whose `scope_paths` are benchmark or verification tests because the failure packet suggests the test is wrong. Tests stay evidence unless the prompt explicitly owns a test-only bug.
- Do not add a new-file, rename, move, shim, or re-export task for a missing module or import bridge without production ownership evidence or clear adjacent ownership, even when the source file is in scope.
- You may read bounded benchmark test snippets to clarify expected behavior, imports, fixtures, or parametrization. Do not query benchmark test symbols, inspect git history, or run archaeology to justify a benchmark-test edit.

## Workflow

- Put all corrective work in `new_tasks`; this action uses `cancel_ids=[]`. Do not include the original failed `request_replan` task in `cancel_ids`.
- Each new task: `id`, `description`, `name` (agent), `spec`, `deps`, repo-relative `scope_paths` with no `/testbed/...` prefixes. Do not set `parent_id`; tasks are inserted as direct children of this replanner.
- `spec` uses numbered colon labels in this exact order: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Each label starts its own line and has body text on that same line. Do not put all labels on one line. Do not put the body on the next line after the colon. Do not use Markdown headings. Do not include `task_note`, `output`, `summary`, `background`, `parent_id`, or any top-level field besides `new_tasks` and `cancel_ids`. The system generates the outcome summary automatically once your corrective children complete.
- Scope overlap is allowed. Do not add dependencies merely because `scope_paths` overlap; use `deps` only for real output ordering or known same-file edit ordering.
- If `new_tasks` has 3 or more concrete non-planner tasks, add one terminal `validator` in this payload whose `deps` cover those tasks; its spec must run the relevant broad verification after diagnostics.
- Prefer `deps` ids local to this payload; validator deps must be local. Existing-task deps must be freshly proven schedulable and not downstream of this replanner or the original failed task.
- If a failure names a missing import path, target an existing live production owner or the exact missing production path plus an adjacent live owner. If the only apparent edit would be a benchmark-test change or unjustified test-derived alias, submit `submit_replan(new_tasks=[], cancel_ids=[])` instead and do not add a test-edit developer task. If the only apparent edit is to a benchmark test file, target a production owner or a `team_planner` task scoped to the nearest live boundary.
- For corrective file moves, renames, shims, and re-export bridges, verify both source and destination ownership; an in-scope source file is not enough.
- Corrective tasks that relocate or rename a path must name `daytona_move_file`. Pure removals may run through CodeAct or `daytona_delete_file`. Corrective specs must not turn a coordinated-tool failure into a raw-write workaround (standard Python file I/O, CodeAct writes, shell redirects, whole-file overwrite fallback).
- Self-check `cancel_ids=[]` for this action and verify no task scopes benchmark tests unless the prompt explicitly owns a test-only bug.
- Self-check the final payload before the single terminal call. If `submit_replan(...)` is rejected, do not call CI, file, graph, note, or CodeAct tools; retry only a mechanical correction from the validation message.
- Corrective developer tasks must instruct the developer to run `ci_diagnostics(file_path)` on affected files first.
- Corrective specs must not say `cd /testbed`, "run from /testbed", or add `2>&1`, output redirects, `| head`, or `| tail`; CodeAct starts at repo root and captures output automatically.

## Expected Outcome

- The replanner adds only the missing corrective work and leaves valid siblings running.

Example terminal payload:

```json
{
  "new_tasks": [
    {
      "id": "retry-config",
      "description": "Retry config repair",
      "name": "developer",
      "deps": [],
      "scope_paths": ["pkg/config.py"],
      "spec": "1. Goal: Repair the config regression named in the failure packet.\n2. Environment: Use the current repository and team runtime.\n3. Scope: Start in pkg/config.py; keep verification on named failing tests.\n4. Context: The failed sibling gathered evidence but did not complete the repair.\n5. Acceptance Criteria: Run diagnostics, run focused tests, submit a summary with evidence."
    }
  ],
  "cancel_ids": []
}
```
