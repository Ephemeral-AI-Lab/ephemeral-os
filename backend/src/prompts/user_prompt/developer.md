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
8. Do not spend the final tool call on inspection, CodeAct, diagnostics, cleanup, or another edit. If a budget warning appears and you cannot finish verification while reserving one call for `submit_task_summary(...)`, submit `type="request_replan"` with the current evidence now.
9. Never use `daytona_codeact` for path moves or git-index mutation tokens such as `mv`, `shutil.move`, `os.rename`, `git rm`, or `git mv`; use `daytona_move_file` for repo path moves. Pure removals such as `rm`, `unlink`, `os.remove`, `Path.unlink`, and `shutil.rmtree` may run through CodeAct because the overlay audit path converts tracked removals into OCC-gated deletes and rejects unsupported removal shapes. If `daytona_delete_file` or `daytona_move_file` fails, do not retry the same delete/move tool; submit a failure with the tool error.
10. Use repo-relative paths or `/testbed/...` sandbox paths in Daytona and CI tools. Never pass host workspace paths such as `/Users/...` into sandbox tools, and never search host directories from CodeAct.
11. If any tool result warns about `outside write_scope`, `verification-surface write allowed`, or a missing outside-scope module/shim/re-export/import bridge, stop immediately. If any test, CodeAct, or diagnostic output shows `ModuleNotFoundError`, `ImportError`, or collection failure naming a missing module outside `scope_paths`, stop immediately. Your next tool call must be `submit_task_summary(type="request_replan", content=...)` with the evidence. Do not call `daytona_glob`, `daytona_grep`, `ci_query_symbol`, `daytona_read_file`, `daytona_write_file`, `daytona_edit_file`, `daytona_move_file`, `daytona_delete_file`, `daytona_rename_symbol`, or another CodeAct command after that evidence. Do not read tests, inspect `__init__.py`, inspect git history, edit, run tests, verify, or "reconsider" after that evidence; the terminal failure summary is your next and only action. A missing module, compatibility shim, re-export, import bridge, similar in-scope compatibility file, or multiple tests importing the missing module is a stop signal, never permission to create, rename, move, or re-export a same-named path outside `scope_paths`. For path moves, file renames, shims, and re-exports, both source and destination must be in `scope_paths` unless non-test production evidence proves the outside-scope destination is the live owner; an in-scope source file is not permission to create, move, rename, or re-export to an outside-scope destination named only by tests. Do not attempt an out-of-scope edit or write, and do not attempt an out-of-scope move or rename, to see whether the tool allows it; the attempt itself is a failed lane.
12. End this lane with exactly one `submit_task_summary(...)` call. If verification is incomplete, the tool budget is low, the owner is wrong, or the task is still red, call `submit_task_summary(type="request_replan", content=...)` with the evidence instead of continuing without a terminal submission.

## Assigned coding task

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}

Benchmark and verification test files in this list are read/verify-only unless the task explicitly says the bug is in tests. Do not edit `*/tests/*`, `test_*.py`, or verification targets just because they appear here; patch the production owner or submit a failure for replanning when tests are the only apparent edit.
If live evidence identifies a missing module, compatibility shim, re-export, import bridge, or production owner outside this list, do not create or edit it in this lane. Submit `submit_task_summary(type="request_replan", content=...)` with the path and evidence so replanning can widen or resequence the task.
If verification fails with `ModuleNotFoundError`, `ImportError`, or collection failure for a module outside `scope_paths`, do not inspect tests, glob/grep for the module, query symbols for the missing import, inspect package `__init__.py`, inspect git history, read adjacent files, or use CodeAct again to self-widen. Submit a failure summary immediately with the missing module and command output.
Even when a test import literally names the missing module, do not create, rename, move, or re-export it from test evidence alone. "Needed to make tests collect", "standard re-export pattern", "target count requires it", "multiple tests import it", and "scope contains a similar in-scope compatibility file" are not exceptions. If `scope_paths` names a new file that is absent, confirm non-test production evidence that the new file is the intended repository surface before writing; otherwise submit a failure with the missing-path evidence.
For file moves/renames, compatibility shims, and re-export bridges, check both endpoints. A source path inside `scope_paths` does not authorize an absent destination outside `scope_paths`; if the destination is named only by tests or collection output, submit a failure before calling `daytona_move_file(...)`, `daytona_write_file(...)`, or `daytona_edit_file(...)`. Never call `daytona_move_file(...)` just to test whether an out-of-scope destination is allowed.
Before any `daytona_write_file(...)` or `daytona_edit_file(...)`, compare the target file to `scope_paths`. If it is outside scope, submit a failure instead of probing the write tool; the out-of-scope attempt itself is a failed lane even if the tool later emits an advisory warning.
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
