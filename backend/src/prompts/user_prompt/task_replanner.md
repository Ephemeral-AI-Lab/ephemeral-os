Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned replanning task and failure context.
2. Analyze what failed and which sibling work is affected.
3. Explore only enough to justify the smallest corrective plan.
4. Draft corrective child tasks with dependencies, scope paths, and structured specs. All new tasks are owned by this replanner; there is no free-form `parent_id`, and new tasks must not depend on downstream work that is already blocked on this replanner.
5. For each new task spec, use exactly this section order with colon labels: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Do not use Markdown headings such as `## Goal`.
6. Verify the corrective plan is valid, non-overlapping, and grounded in failure evidence. No two parallel concrete tasks may share a `scope_paths` file; add a `deps` edge, or use one focused repair task when the same file owns all failures.
7. Submit the final corrective plan with `submit_replan(new_tasks=[...], cancel_ids=[...])`. `cancel_ids` may only target your **direct siblings**; cascade handles their subtrees. Put replacement work in `new_tasks` so downstream work remains blocked on this replanner until recovery completes. Do not include `task_note`, `output`, `background`, `parent_id`, or any other top-level fields.
8. Never put the original failed `request_replan` task in `cancel_ids`, even if `read_task_graph` shows it near you. It is immutable failure evidence and the runtime will detach/finalize it after a valid replan.
9. `submit_replan` is only your terminal tool. If you create a replacement `team_planner` task, that planner's terminal tool is `submit_plan`, not `submit_replan`.

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
