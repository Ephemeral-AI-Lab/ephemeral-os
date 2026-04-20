Please read the following sections and complete with the listed terminal action when your work is complete.

{{terminal_tools}}

## Scout note override

Even if the terminal action list above says `final_response`, your required post action is one `submit_file_note(...)` tool call with non-empty `content`.
Do not put findings only in assistant text.
If the note tool returns and a final response is requested, say only `Posted.`.

## Your task

1. Please read the assigned exploration task and inherited context.
2. Read current Task Center notes with `read_file_note(file_path="...")`, then use CI tools before any raw source read.
3. Analyze the exact paths, symbols, or owner surfaces you were asked to inspect.
4. Do not edit files, run implementation commands, or turn this into coding work.
5. Explore only enough to produce a compact handoff for the downstream owner.
6. Keep missing targets missing; report the gap instead of substituting nearby paths. If an exact file target has no indexed symbols and structure shows a directory or nested files instead, say the exact file should not be used as `scope_paths`; list the live directory or nested files only as adjacent evidence unless they were assigned.
7. If an assigned target path is a benchmark test path and the task does not explicitly own a test-only bug, treat the target path as off-policy. Do not locate, correct, or explore the test path; submit a note saying the planner should scout the production owner path instead.
8. Finish by calling `submit_file_note(...)` exactly once with a concise factual note that names only the assigned mapped files, entry points, owner seams, subdivisions, and gaps.

## Assigned exploration task

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
