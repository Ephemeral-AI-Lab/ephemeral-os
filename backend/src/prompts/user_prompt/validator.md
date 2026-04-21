Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

Follow the bundled validator playbook for workflow and rules; this message supplies task data.

## Assigned validation task

Task id: `{{your_task_id}}`
{{#if your_parent_task_id}}
Parent task id: `{{your_parent_task_id}}`
{{/if}}
{{#if your_deps_ids}}
Dependency task ids: {{your_deps_ids}}
{{/if}}

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}
