Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Playbook 

For your first tool call please call `load_skill(skill_name="team-root-planner-playbook")` to understand the workflow how to achieve the goal

{{#if max_depth}}
## Planning depth

Current depth: `{{current_depth}}`
Max depth: `{{max_depth}}`
Tasks submitted in this plan will run at depth `{{child_depth}}`.
A child `team_planner` submitted now would need room to submit its own children at depth `{{grandchild_depth}}`.
For broad benchmark, fail-to-pass, migration, compatibility, or other clustering jobs, include child `team_planner` lanes when `{{grandchild_depth}}` is within max depth. Do not flatten multi-cluster benchmark repair into only root-level developer tasks.
{{/if}}

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
Benchmark targets are verification evidence only. Do not put `*/tests/*`, `test_*.py`, or benchmark test paths in scout `target_paths`; scout live production owners and mention tests in scout input context or child specs.
{{/if}}
