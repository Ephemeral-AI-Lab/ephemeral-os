Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned planner task and inherited context.
2. Reuse current Task Center notes with `read_task_note(paths=[...])` before launching scouts or probing likely owners, then use CI tools to refine ownership. Before `run_subagent`, scrub scout `target_paths` to live production owner files/directories; keep benchmark tests and missing test-derived paths in task prose or `task_note`. After `run_subagent` scouts, read their notes with default scope; do not set `scope="sibling"` for those same-task scout notes.
3. Analyze the subtask objective, expected outcome, and remaining uncertainty.
4. Explore only enough to justify concrete child task ownership and scope boundaries.
5. Draft the child plan and verify dependencies, short descriptions, scope paths, and structured specs.
6. Keep benchmark or verification test targets in task prose and acceptance criteria, not developer, validator, or child-planner `scope_paths`, unless tests are explicitly the owned bug surface. If the only concrete paths are test files, broaden to the nearest live production owner boundary or leave the tests as evidence in `spec`; do not submit test paths as implementation scope.
7. Before the terminal `submit_plan(...)` call, self-check the payload once. Validation errors on the terminal call count as a bad post call; include required descriptions, structured specs, and validator dependencies before calling.

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
