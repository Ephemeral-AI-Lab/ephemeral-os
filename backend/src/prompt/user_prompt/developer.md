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

Mandatory CodeAct preflight: immediately before every `daytona_codeact` call, inspect the final command string. Use `command` for every shell, build, or test command; never put shell command text such as `python -m pytest ...` in `code`. The `code` field is Python source only. If the command contains `|`, `>`, `2>&1`, `head`, `tail`, or starts with `cd`, rewrite it first. Remove shell redirects and output filters entirely, drop the leading `cd`, split chained suites into separate CodeAct calls, and use pytest flags such as `-q --tb=short`, `-x`, a node id, or `-k` to bound output. Do not rely on sanitizer behavior as your normal workflow. If a `daytona_codeact` pre-hook sanitizes a non-destructive command anyway, treat the advisory as workflow guidance, record the sanitized command that actually ran, and cite that sanitized command as verification evidence. If a command is blocked as destructive or unsanitizable, rewrite it to a workflow-valid command before retrying; submit `type="request_replan"` with trigger `unresolved_blocker` only when no valid equivalent can preserve the needed evidence.

Scope guard: `scope_paths` are the assigned mutation surface for existing files, renames, moves, and deletes. Acceptance criteria, benchmark/test outcomes, and import errors do not by themselves expand them. Creating a new production file with `daytona_write_file` may extend scope when live evidence requires a compatibility shim, module, re-export, or bridge and no other worker owns that exact path; rely on the write-scope posthook to approve and record the expansion. If the mutation tool blocks expansion or reports a conflict, submit `type="request_replan"` with trigger `scope_expansion`. Test files remain read/verify-only unless explicitly owned.

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}
