Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned planner task and inherited context.
2. Reuse current Task Center notes with `read_task_note(paths=[...])` before launching scouts or probing likely owners, then use CI tools to refine ownership.
3. Analyze the subtask objective, expected outcome, and remaining uncertainty.
4. Explore only enough to justify concrete child task ownership and scope boundaries.
5. Draft the child plan and verify dependencies, short descriptions, scope paths, and structured specs.

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
