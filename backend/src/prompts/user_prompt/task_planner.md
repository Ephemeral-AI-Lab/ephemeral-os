Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

Follow the bundled team-planner playbook for workflow and rules; this message supplies task data.
Your first assistant action must contain exactly one tool call: `load_skill(skill_name="team-planner-playbook")`.
Do not batch that first playbook load with any other tool call.

## Assigned planner task

Your task id: `{{your_task_id}}`
{{#if your_parent_task_id}}
Your parent task id: `{{your_parent_task_id}}`
{{/if}}
{{#if your_deps_ids}}
Your dependency task ids: {{your_deps_ids}}
{{/if}}

Context-read pre-step: this applies to child planners only. After loading the team-planner playbook, use the UUIDs above exactly with `read_task_details(...)` for your task, parent, and each dependency, then call `read_task_graph()` to enumerate siblings before scouts, CI, notes, or `submit_plan(...)`. Each `read_task_details` input must contain only `task_id`; do not pass `skill_name`, planner slugs, short prefixes, or fabricated ids. Do not batch those required context reads with scout, CI, note, file, edit, diagnostics, reference, or submission tools.

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}
