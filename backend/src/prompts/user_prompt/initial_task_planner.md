Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the user request and benchmark targets.
2. Analyze the task objective, expected outcome, and likely owner surfaces.
3. Explore only enough to justify concrete task ownership and scope boundaries.
4. Draft the plan and verify dependencies, scope paths, and structured specs.
5. Submit the final plan with `submit_plan(new_tasks=[...])`. Do not include `task_note`, `background`, or any other top-level fields besides `new_tasks` and optional `output`.

## User request

```markdown
{{user_request}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}

{{#if benchmark_targets}}
## Benchmark targets

```markdown
{{benchmark_targets}}
```
{{/if}}

{{#if parent_context}}
## Parent context
{{parent_context}}
{{/if}}
