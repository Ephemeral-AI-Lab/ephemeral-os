Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned replanning task and failure context.
2. Read sibling notes with `read_task_note(paths=[...], scope="sibling")`, then use CI tools such as `ci_workspace_structure(...)`, `ci_query_symbol(...)`, or `ci_diagnostics(...)` before opening broader graph details.
3. Analyze what failed and which sibling work is affected.
4. Explore only enough to justify the smallest corrective plan.
5. Draft corrective child tasks with dependencies, short descriptions, scope paths, and structured specs. All new tasks are owned by this replanner; there is no free-form `parent_id`, and new tasks must not depend on downstream work that is already blocked on this replanner. Prefer `deps` ids from this same `new_tasks` payload, and make validator deps local to this payload. Use an existing task id only when fresh graph context proves that exact id is schedulable, accepted by the current task graph, and not downstream of this replanner or the original failed task; when unsure, omit the existing dep.
6. For each new task spec, use exactly this section order with colon labels: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Do not use Markdown headings such as `## Goal`.
7. Verify the corrective plan is valid, non-overlapping, and grounded in failure evidence. No two parallel concrete tasks may share a `scope_paths` file; add a `deps` edge, or use one focused repair task when the same file owns all failures.
8. Do not create corrective tasks for missing modules, compatibility shims, re-export modules, or import bridges when the only evidence is a test import or collection error. A new-file task requires non-test production evidence that the absent file is the intended repository surface; otherwise keep the missing path as evidence and target an existing live owner. A target count, collection blocker, standard re-export pattern, or similar in-scope filename is not an exception.
9. Before calling `submit_replan`, self-check the payload once: every new task has `description`; specs use numbered colon labels; every `deps` id is local to this payload unless you freshly proved the exact existing id is schedulable and accepted; no validator depends on existing graph ids; no `deps` id points to `request_replan`, `running`, `expanded`, `failed`, `cancelled`, or downstream-blocked work; no `cancel_ids` entry is the original failed `request_replan` task; and if you submit 3 or more concrete non-planner tasks, include one terminal `validator` task in the same call with `deps` covering those local tasks.
10. Submit the final corrective plan with `submit_replan(new_tasks=[...], cancel_ids=[...])`. Every new task must include a short `description`. `cancel_ids` may only target your **direct siblings**; cascade handles their subtrees. Put replacement work in `new_tasks` so downstream work remains blocked on this replanner until recovery completes. Do not include `task_note`, `output`, `background`, `parent_id`, or any other top-level fields.
11. Never put the original failed `request_replan` task in `cancel_ids`, even if `read_task_graph` shows it near you. It is immutable failure evidence and the runtime will detach/finalize it after a valid replan.
12. `submit_replan` is only your terminal tool. If you create a replacement `team_planner` task, that planner's terminal tool is `submit_plan`, not `submit_replan`.

## Assigned replanning task

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}

{{#if failure_context}}
## Failure context
{{failure_context}}

{{/if}}
{{#if context_from_dependencies}}
## Context from dependencies
{{context_from_dependencies}}

{{/if}}
{{#if recent_scope_changes}}
## Recent changes in your scope
{{recent_scope_changes}}

{{/if}}
{{#if parent_context}}
## Parent context
{{parent_context}}
{{/if}}
