Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned validation task and inherited context.
2. Analyze what outcome must be verified and which prior task outputs matter.
3. Inspect only enough context to understand the expected behavior and risk surface.
4. Run the relevant verification command or check.
5. Evaluate the evidence truthfully as pass or fail.
6. Submit the final validation summary with `submit_task_summary(type="success", summary=...)` or report failure with evidence.

## Assigned validation task

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
