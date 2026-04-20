Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned validation task and inherited context.
2. Before any sandbox file read, call `read_file_note(file_path="...")`, then use `ci_workspace_structure(...)`, `ci_query_symbol(...)`, or `ci_diagnostics(...)` to locate the verification boundary.
3. Treat `daytona_read_file(...)` as a fallback for narrow line ranges after notes and CI evidence, not as the opening move.
4. Analyze what outcome must be verified and which prior task outputs matter.
5. Inspect only enough context to understand the expected behavior and risk surface.
6. Run the relevant verification command or check.
7. Evaluate the evidence truthfully as pass or fail.
8. Submit the final validation summary with `submit_task_summary(type="success", content=...)` or report failure with `submit_task_summary(type="request_replan", content=...)`.

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
