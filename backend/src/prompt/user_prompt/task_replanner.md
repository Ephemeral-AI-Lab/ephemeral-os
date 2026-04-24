Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Assigned replanning task

## Task Spec

{{task_spec}}

## Depedency and Inheritance

Please call `read_task_details` to check the dependency or parent tasks.

{{#if your_parent_task_id}}
Your parent task id: `{{your_parent_task_id}}`
{{/if}}
{{#if your_failed_task_id}}
Failed task id: `{{your_failed_task_id}}`
{{/if}}
{{#if your_deps_ids}}
Your dependency task ids: {{your_deps_ids}}
{{/if}}

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}
