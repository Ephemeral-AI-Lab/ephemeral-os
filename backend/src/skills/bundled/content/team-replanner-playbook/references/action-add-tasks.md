# Action Reference: submit_replan (add corrective tasks)

Use `submit_replan(new_tasks=[...], cancel_ids=[])` when the plan structure is sound but more work is needed. Existing siblings continue running while downstream work remains blocked on this replanner until its new child tasks complete.

## Task/Goal

- An isolated task failed and needs corrective follow-up.
- A transient error needs a new corrective task with the same or narrower scope.
- Follow-up validation is needed after a fix lands.

## Avoid

- Never submit corrective tasks without reading sibling notes first.
- Do not add tasks that duplicate work already covered by existing siblings.

## Workflow

- Must confirm owner paths live with CI before submitting.
- Must read sibling notes before deciding corrective scope.
- Each new task must have: `id`, `name` (agent), `spec`, `deps`, `scope_paths`. Do not set `parent_id`; every new task is inserted as a direct child of this replanner.
- Put all corrective work in `new_tasks`.
- Do not include the original failed `request_replan` task in `cancel_ids`; this action normally uses `cancel_ids=[]`.
- Parallel concrete tasks must not share any `scope_paths` file. If two corrective clusters touch the same file, either add a `deps` edge from the later task to the earlier task, or submit one focused repair task for that shared owner file.
- `deps` on existing tasks must target tasks whose status is `done`, `ready`, or `pending` and that do not already depend on this replanner or the original failed task. Do NOT depend on downstream tasks blocked on this replanner, or on tasks in `request_replan`, `running`, `expanded`, `failed`, or `cancelled` — these are either cyclic, transitioning, transient, or detached and the scheduler will never promote a dependent past them.
- Each `spec` must use numbered colon labels in this exact order: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Do not use Markdown headings such as `## Goal`.
- Do not include `task_note`, `output`, `background`, `parent_id`, or any top-level field besides `new_tasks` and `cancel_ids`.
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
      "name": "developer",
      "deps": [],
      "scope_paths": ["pkg/config.py"],
      "spec": "1. Goal: Repair the config regression named in the failure packet.\n2. Environment: Work in the current repository checkout and use the available team runtime tools.\n3. Scope: Start in pkg/config.py and keep verification on the named failing tests.\n4. Context: The failed sibling gathered exact evidence for pkg/config.py but did not complete the repair.\n5. Acceptance Criteria: Run diagnostics on pkg/config.py, run the named focused tests, and submit a success or fail summary with evidence."
    }
  ],
  "cancel_ids": []
}
```
