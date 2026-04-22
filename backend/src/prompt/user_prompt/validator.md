Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

Follow the bundled validator playbook for workflow and rules; this message supplies task data.
Your first assistant action must contain exactly one tool call: `load_skill(skill_name="team-validator-playbook")`.
Do not batch that first playbook load with any other tool call.

## Assigned validation task

Your task id: `{{your_task_id}}`
{{#if your_parent_task_id}}
Your parent task id: `{{your_parent_task_id}}`
{{/if}}
{{#if your_deps_ids}}
Your dependency task ids: {{your_deps_ids}}
{{/if}}

Context-read pre-step: after loading the validator playbook, use the UUIDs above exactly with `read_task_details(...)` for your task, parent, and each dependency before any CodeAct, CI, note, file, edit, or diagnostics tool. Each `read_task_details` input must contain only `task_id`; do not pass `skill_name`, planner slugs, short prefixes, or fabricated ids. If no dependency task ids are listed, read only your task and parent. Do not batch those required context reads with CodeAct, CI, note, file, edit, diagnostics, or reference tools.

Benchmark CodeAct preflight: before any `daytona_codeact(...)` call, run `load_skill_reference(skill_name="team-validator-playbook", reference_name="runtime-verification-examples")`. If that reference has not loaded in this agent run, do not call CodeAct. Before each CodeAct command, inspect the exact command string; any literal `|` or `>` character means the command is invalid and must be rewritten before the tool call. Do not run duplicate equivalent verification commands in parallel. A success verdict may cite only commands actually run after the final validator edit with their observed outcomes.

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}
