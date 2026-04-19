Please read the following sections and call the listed terminal tool when your work is complete.

{{terminal_tools}}

## Your task

1. Please read the assigned replanning task and failure context.
2. Read sibling notes with `read_task_note(paths=[...], scope="sibling")`, then use CI tools such as `ci_workspace_structure(...)`, `ci_query_symbol(...)`, or `ci_diagnostics(...)` before opening broader graph details.
3. Analyze what failed and which sibling work is affected.
4. Explore only enough to justify the smallest corrective plan.
5. Draft corrective child tasks with dependencies, short descriptions, scope paths, and structured specs. All new tasks are owned by this replanner; there is no free-form `parent_id`, and new tasks must not depend on downstream work that is already blocked on this replanner. Prefer `deps` ids from this same `new_tasks` payload, and make validator deps local to this payload. Use an existing task id only when fresh graph context proves that exact id is schedulable, accepted by the current task graph, and not downstream of this replanner or the original failed task; when unsure, omit the existing dep.
6. For each new task spec, use exactly this section order with colon labels: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Do not use Markdown headings such as `## Goal`.
7. Verify the corrective plan is valid, non-overlapping, and grounded in failure evidence. No two parallel concrete tasks may share a `scope_paths` file; add a `deps` edge, or use one focused repair task when the same file owns all failures.
8. Do not create corrective tasks for missing modules, compatibility shims, re-export modules, import bridges, file renames, or file moves when the only evidence is a test import or collection error. A new-file, rename, move, shim, or re-export task requires non-test production evidence that the absent path is the intended repository surface; otherwise keep the missing path as evidence and target an existing live owner. For move/rename/shim/re-export tasks, both source and destination must be justified; an in-scope source compatibility file is not permission to create, move, rename, or re-export to an absent outside-scope destination named only by tests. A benchmark test import is never production evidence for an absent module, even if a live module has a similar name or the package already uses underscore-prefixed files. A target count, collection blocker, standard re-export pattern, multiple tests importing it, or a similar in-scope compatibility filename is not an exception. Do not read benchmark tests, query benchmark test symbols, inspect git history, or run archaeology to overturn a failed developer's outside-scope missing-module stop signal.
9. After an outside-scope missing-module, shim, import-bridge, move, or rename stop signal, do not call `ci_query_symbol`, `ci_workspace_structure`, `ci_diagnostics`, file-read, grep, or CodeAct tools to inspect the missing path, similarly named modules, package aliases, or adjacent compatibility files. Use only the failure context and already-read notes. If no non-test production owner was already proven before the stop signal, submit `submit_replan(new_tasks=[], cancel_ids=[])`; do not create a finder task, shim task, re-export task, alias task, move task, or rename task for a path named only by tests.
10. Never turn a benchmark or verification test file into `scope_paths` because the failure packet makes the test look wrong. Even if the test import, decorator, parametrization, or assertion appears broken, keep the test path as evidence and target a production owner or broader live production boundary; if no production owner is known, create a `team_planner` task to find one, not a test-edit developer task. This fallback does not override the stop-signal rule above for absent modules or missing paths named only by tests.
11. Do not turn a coordinated file-tool failure into bypass instructions. Corrective tasks must not tell children to use standard Python file I/O, CodeAct writes, shell redirects, or whole-file overwrite fallback instructions after `daytona_edit_file`, `daytona_write_file`, `daytona_rename_symbol`, `daytona_delete_file`, or `daytona_move_file` fails. Ask for a precise coordinated-tool retry or preserve the tool failure as evidence.
12. Before calling `submit_replan`, self-check the payload once: every new task has `description`; specs use numbered colon labels; every `deps` id is local to this payload unless you freshly proved the exact existing id is schedulable and accepted; no validator depends on existing graph ids; no `deps` id points to `request_replan`, `running`, `expanded`, `failed`, `cancelled`, or downstream-blocked work; no `cancel_ids` entry is the original failed `request_replan` task; no new task has benchmark or verification test files in `scope_paths` unless the user prompt explicitly owns a test-only bug; no child spec bypasses a failed coordinated file tool with raw writes; and if you submit 3 or more concrete non-planner tasks, include one terminal `validator` task in the same call with `deps` covering those local tasks.
13. Submit the final corrective plan with `submit_replan(new_tasks=[...], cancel_ids=[...])`. Every new task must include a short `description`. `cancel_ids` may only target your **direct siblings**; cascade handles their subtrees. Put replacement work in `new_tasks` so downstream work remains blocked on this replanner until recovery completes. Do not include `task_note`, `output`, `background`, `parent_id`, or any other top-level fields.
14. If `submit_replan(...)` returns a validation error anyway, do not call CI, file, graph, note, or CodeAct tools afterward. Retry only when the correction is mechanical from the validation message and prior evidence, such as removing an invalid existing dep or adding a missing local validator dep; never switch strategy to a test-derived shim, re-export, alias, move, or rename after a rejected terminal payload.
15. Never put the original failed `request_replan` task in `cancel_ids`, even if `read_task_graph` shows it near you. It is immutable failure evidence and the runtime will detach/finalize it after a valid replan.
16. `submit_replan` is only your terminal tool. If you create a replacement `team_planner` task, that planner's terminal tool is `submit_plan`, not `submit_replan`.

## Assigned replanning task

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## scope_paths
{{scope_paths}}
{{/if}}

{{#if failure_context}}
## Failure context
{{failure_context}}

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
