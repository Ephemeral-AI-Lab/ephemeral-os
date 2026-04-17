Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned planner task and inherited context.
2. Analyze the subtask objective, expected outcome, and remaining uncertainty.
3. Explore only enough to justify concrete child task ownership and scope boundaries.
4. Draft the child plan and verify dependencies, scope paths, and structured specs.
5. Submit the final child plan with `submit_plan(new_tasks=[...])`. Do not include `task_note`, `background`, or any other top-level fields besides `new_tasks` and optional `output`.

## Assigned planner task

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
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
