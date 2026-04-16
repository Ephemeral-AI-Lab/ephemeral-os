# Task Planner User Prompt

Use this template for non-root `team_planner` tasks created by a parent planner.

````text
## Your task

1. Please read the assigned planner task and inherited context.
2. Analyze the subtask objective, expected outcome, and remaining uncertainty.
3. Explore only enough to justify concrete child task ownership and scope boundaries.
4. Draft the child plan and verify dependencies, scope paths, and structured specs.
5. Submit the final child plan with `submit_plan(new_tasks=[...])`.

## Assigned planner task

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
