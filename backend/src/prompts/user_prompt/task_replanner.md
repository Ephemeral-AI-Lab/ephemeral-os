Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

Follow the bundled team-replanner playbook for workflow and rules; this message supplies task data.

## Assigned replanning task

Your task id: `{{your_task_id}}`
{{#if your_parent_task_id}}
Your parent task id: `{{your_parent_task_id}}`
{{/if}}
{{#if your_failed_task_id}}
Failed task id: `{{your_failed_task_id}}`
{{/if}}
{{#if your_deps_ids}}
Your dependency task ids: {{your_deps_ids}}
{{/if}}

Context-read pre-step: after loading the replanner playbook, use the UUIDs above exactly with `read_task_details(...)` for your task, parent, failed task, and each dependency, then call `read_task_graph()` to enumerate siblings before CI, notes, diagnosis, corrective planning, or `submit_replan(...)`.

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}
