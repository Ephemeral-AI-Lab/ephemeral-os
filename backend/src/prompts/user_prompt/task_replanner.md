# Task Replanner User Prompt

Use this template for `team_replanner` tasks that recover from a failed sibling task.

````text
## Your task

1. Please read the assigned replanning task and failure context.
2. Analyze what failed and which sibling work is affected.
3. Explore only enough to justify the smallest corrective plan.
4. Draft corrective tasks with exact dependencies, scope paths, and structured specs.
5. Verify the corrective plan is valid, non-overlapping, and grounded in failure evidence. Include `expected_projection` when parent-bounded dependency shape or cascade impact matters.
6. Submit the final corrective plan with `submit_replan(new_tasks=[...], cancel_ids=[...])`.

## Assigned replanning task

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## Scope
{{scope_paths}}
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
