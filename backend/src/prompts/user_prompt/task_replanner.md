# Task Replanner User Prompt

Use this template for `team_replanner` tasks that recover from a failed sibling task.

````text
## Your task

1. Please read the assigned replanning task and failure context.
2. Analyze what failed, which sibling work is affected, and whether a shared blocker exists.
3. Explore only enough to justify the smallest corrective plan.
4. Draft corrective tasks with exact dependencies, scope paths, and structured specs.
5. Verify the corrective plan is valid, non-overlapping, and grounded in failure evidence. Include `expected_graph={"task_id": ["dep_id", ...]}` when dependency shape matters.
6. Submit the final corrective plan with `submit_task_plan(new_tasks=[...], remove_tasks=[...])` or declare a shared blocker when appropriate.

## Assigned replanning task

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## Scope
{{scope_paths}}
{{/if}}

{{#if active_blockers}}
## Active blockers

The following blockers are currently active for sibling tasks. If an active blocker already covers the same root-cause paths, do not declare another blocker. Use `submit_task_plan(new_tasks=[...])` instead, and depend on that blocker's `fix_task_id` so the retry runs after the shared fix.

{{active_blockers}}

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
````
