# Action Reference: submit_replan (add corrective tasks)

Use `submit_replan(new_tasks=[...], cancel_ids=[])` when the plan structure is sound but more work is needed. Existing siblings continue running while downstream work remains blocked on this replanner until its new child tasks complete.

## Task/Goal

- An isolated task failed and needs corrective follow-up.
- A transient error needs a new corrective task with the same or narrower scope.
- Follow-up validation is needed after a fix lands.

## Avoid

- Never submit corrective tasks without reading sibling notes first.
- Do not add tasks that duplicate work already covered by existing siblings.
- Do not add a new-file task for a missing module, shim, re-export module, or import bridge whose only evidence is a test import or collection error. A target count, collection blocker, standard re-export pattern, or similar in-scope filename is not an exception.

## Workflow

- Must confirm owner paths live with CI before submitting.
- Must read sibling notes before deciding corrective scope.
- If a failure names a missing import path, first target an existing live production owner or broader live boundary. Only create a new-file corrective task when non-test production evidence proves the absent file is the intended repository surface.
- Each new task must have: `id`, `description`, `name` (agent), `spec`, `deps`, `scope_paths`. Do not set `parent_id`; every new task is inserted as a direct child of this replanner.
- Keep each `description` as a planner-authored short label under about 10 words; put full instructions in `spec`.
- Put all corrective work in `new_tasks`.
- If a corrective task removes a repo file, its spec must name `daytona_delete_file`; if it relocates or renames a path, its spec must name `daytona_move_file`. Never tell a child to use CodeAct `rm`, `mv`, `unlink`, `shutil.rmtree`, or `shutil.move`.
- Do not include the original failed `request_replan` task in `cancel_ids`; this action normally uses `cancel_ids=[]`.
- Parallel concrete tasks must not share any `scope_paths` file. If two corrective clusters touch the same file, either add a `deps` edge from the later task to the earlier task, or submit one focused repair task for that shared owner file.
- If `new_tasks` contains 3 or more concrete non-planner tasks, add one terminal `validator` task in this same `submit_replan(...)` payload. Its `deps` must cover those concrete tasks, and its spec must run the relevant broad verification after diagnostics.
- Prefer `deps` ids that are local to this same `new_tasks` payload. Validator deps must be local to this payload. `deps` on existing tasks must target tasks whose exact ids are freshly proven accepted by the current graph, whose status is `done`, `ready`, or `pending`, and that do not already depend on this replanner or the original failed task. Do NOT depend on downstream tasks blocked on this replanner, or on tasks in `request_replan`, `running`, `expanded`, `failed`, or `cancelled` — these are either cyclic, transitioning, transient, or detached and the scheduler will never promote a dependent past them. If you cannot prove an existing task id is schedulable, omit that existing dep.
- Each `spec` must use numbered colon labels in this exact order: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Do not use Markdown headings such as `## Goal`.
- Do not include `task_note`, `output`, `background`, `parent_id`, or any top-level field besides `new_tasks` and `cancel_ids`.
- Self-check the final payload before the single terminal call; do not use a failed `submit_replan(...)` attempt as a validation pass.
- Self-check `cancel_ids=[]` for this action and verify that no task creates a test-derived missing path without non-test production evidence.
- Must call `context_changed_since()` before submitting if freshness moved.
- For layered failures, emit a two-phase corrective plan (see Hard Rule 10 in main playbook).
- Corrective developer tasks must instruct the developer to run `ci_diagnostics(file_path)` on affected files first.

## Expected Outcome

- The replanner adds only the missing corrective work and leaves still-valid siblings running.
- Example terminal payload:

```json
{
  "new_tasks": [
    {
      "id": "retry-config",
      "description": "Retry config repair",
      "name": "developer",
      "deps": [],
      "scope_paths": ["pkg/config.py"],
      "spec": "1. Goal: Repair the config regression named in the failure packet.\n2. Environment: Work in the current repository checkout and use the available team runtime tools.\n3. Scope: Start in pkg/config.py and keep verification on the named failing tests.\n4. Context: The failed sibling gathered exact evidence for pkg/config.py but did not complete the repair.\n5. Acceptance Criteria: Run diagnostics on pkg/config.py, run the named focused tests, and submit a success or fail summary with evidence."
    }
  ],
  "cancel_ids": []
}
```
