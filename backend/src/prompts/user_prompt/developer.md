Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned coding task and inherited context.
2. Analyze the implementation objective, expected behavior, and owned scope.
3. Explore only enough to locate the relevant code and understand the issue or gap.
4. Implement the smallest correct change within the assigned scope.
5. Verify the change against the acceptance criteria and apply a fix if the criteria are not met.
6. Submit `submit_task_summary(type="success", content=...)` when complete. Submit `submit_task_summary(type="fail", content=...)` if you hit a blocker, need more investigation, or cannot resolve the task.

Tool-name contract: use only exact tool names from the listed tool surface. For sandbox file writes, call `daytona_write_file`; never call generic aliases such as `write_file`, `Write`, `edit_file`, or `read_file`.
For `daytona_codeact(command="...")`, stdout and stderr are already captured separately. Never append shell capture plumbing such as `2>&1`, `2>/dev/null`, or `1>/tmp/out` to collect output. Commands already run from the repo root transaction checkout; never prefix them with `cd /testbed &&`, `cd /workspace &&`, or another repo-root `cd`.

## Assigned coding task

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
