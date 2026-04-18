Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned coding task and inherited context.
2. Before any sandbox file read, call `read_task_note(paths=[...])` for the owned scope, then use `ci_workspace_structure(...)`, `ci_query_symbol(...)`, or `ci_diagnostics(...)` to locate the owner boundary.
3. Treat `daytona_read_file(...)` as a fallback for narrow line ranges after notes and CI evidence, not as the opening move.
4. Analyze the implementation objective, expected behavior, and owned scope.
5. Explore only enough to locate the relevant code and understand the issue or gap.
6. Implement the smallest correct change within the assigned scope.
7. Verify the change against the acceptance criteria and apply a fix if the criteria are not met.
8. Do not spend the final tool call on inspection, CodeAct, diagnostics, cleanup, or another edit. If a budget warning appears and you cannot finish verification while reserving one call for `submit_task_summary(...)`, submit `type="fail"` with the current evidence now.
9. Never use `daytona_codeact` for cleanup/delete/move tokens such as `rm`, `mv`, `unlink`, `os.remove`, `Path.unlink`, `shutil.rmtree`, `shutil.move`, `git rm`, or `git mv`; use `daytona_delete_file` / `daytona_move_file` for repo files, or leave scratch cleanup alone. If `daytona_delete_file` or `daytona_move_file` fails, do not retry the delete or move through CodeAct; submit a failure with the tool error.
10. Use repo-relative paths or `/testbed/...` sandbox paths in Daytona and CI tools. Never pass host workspace paths such as `/Users/...` into sandbox tools, and never search host directories from CodeAct.
11. If any tool result warns about `outside write_scope`, `verification-surface write allowed`, or a missing outside-scope module/shim/re-export/import bridge, stop editing and make your next tool call `submit_task_summary(type="fail", content=...)` with the evidence.
12. End this lane with exactly one `submit_task_summary(...)` call. If verification is incomplete, the tool budget is low, the owner is wrong, or the task is still red, call `submit_task_summary(type="fail", content=...)` with the evidence instead of continuing without a terminal submission.

## Assigned coding task

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}

Benchmark and verification test files in this list are read/verify-only unless the task explicitly says the bug is in tests. Do not edit `*/tests/*`, `test_*.py`, or verification targets just because they appear here; patch the production owner or submit a failure for replanning when tests are the only apparent edit.
If live evidence identifies a missing module, compatibility shim, re-export, import bridge, or production owner outside this list, do not create or edit it in this lane. Submit `submit_task_summary(type="fail", content=...)` with the path and evidence so replanning can widen or resequence the task.
If a Daytona tool emits an `outside write_scope` warning, treat the packet as tainted and make the next tool call a failure summary for replanning; do not continue editing or verifying from that state.
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
