Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

Follow the bundled developer playbook for workflow and rules; this message supplies task data.
Your first assistant action must contain exactly one tool call: `load_skill(skill_name="team-developer-playbook")`.
Do not batch that first playbook load with any other tool call.

## Assigned coding task

Your task id: `{{your_task_id}}`
{{#if your_parent_task_id}}
Your parent task id: `{{your_parent_task_id}}`
{{/if}}
{{#if your_deps_ids}}
Your dependency task ids: {{your_deps_ids}}
{{/if}}

After the playbook loads, run the context-read pre-step before any probe, edit, note, diagnostics, or CodeAct call. Use the UUID headers above exactly: call `read_task_details` with only one input key, `task_id`, for your task id, parent task id, and each dependency task id. Do not pass `skill_name`, planner slugs, short prefixes, or fabricated ids. Do not batch those required context reads with CodeAct, CI, note, file, edit, diagnostics, or reference tools.

Before every `daytona_codeact` call, follow the CodeAct command rules inside the developer playbook. Inspect the exact command string; if it contains `|` or `>`, rewrite it before the tool call.

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}
