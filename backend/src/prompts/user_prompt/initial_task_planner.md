Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

Follow the bundled team-planner playbook for workflow and rules; this message supplies task data.

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

Benchmark targets are verification evidence only. Do not put `*/tests/*`, `test_*.py`, or benchmark test paths in scout `target_paths`; scout live production owners and mention tests in `task_note` or child specs.
{{/if}}
