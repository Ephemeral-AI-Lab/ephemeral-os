# Initial Task Planner User Prompt

Use this template for the root `team_planner` task that receives the original user request.

````text
## Your task

1. Please read the user request and benchmark targets.
2. Analyze the task objective, expected outcome, and likely owner surfaces.
3. Explore only enough to justify concrete task ownership and scope boundaries.
4. Draft the plan and verify dependencies, scope paths, and structured specs.
5. Submit the final plan with `submit_task_plan(new_tasks=[...])`.

## User request

```markdown
{{user_request}}
```

{{#if scope_paths}}
## Scope
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
````
