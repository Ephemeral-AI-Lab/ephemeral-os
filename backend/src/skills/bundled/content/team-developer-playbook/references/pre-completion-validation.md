# Pre-Completion Validation

Use this reference before signaling completion when you have made source edits.

## Task/Goal

- You edited one or more source files and are preparing the final verification and summary.

## Avoid

- Do not skip this step even if your narrow verification command passed. A passing narrow test does not prove that your edits left no import or name errors in files outside the test's import chain.
- Do not treat `ci_diagnostics` as a substitute for runtime verification. You still must run the assigned verification command. This is a pre-flight check, not the verdict.
- Do not run final or optional regression verification through a `daytona_codeact` command containing `|` or `>` anywhere. `2>&1 | tail`, `| head`, output redirects, and stderr redirects are invalid even after a focused command passed. Use a direct command with pytest flags, a narrower node, background execution, or tool truncation; if a broader check needs forbidden shell syntax to be readable, skip it and summarize the narrower green evidence plus the gap.
- If `ci_diagnostics` reports errors you cannot fix without widening scope, call `submit_task_summary(type='request_replan')` with the exact diagnostic output and a replan-trigger classification from the main playbook's Replan handoff gate.
- Do not take another exploratory or cleanup turn when the remaining budget cannot cover diagnostics, verification, and the terminal summary. The terminal summary itself consumes a tool call; reserve it. Submit `submit_task_summary(type='request_replan')` with the current evidence and the trigger classification. If the only problem is same-scope unfinished work or budget exhaustion, classify it as `none`.

## Workflow

- Before your final message, run `ci_diagnostics(file_path)` on **every file you edited** during this task. This catches import errors, undefined names, syntax errors, and type mismatches before the validator or any parallel sibling sees your changes.

1. Collect the list of files you edited (from your `daytona_edit_file`, `daytona_write_file`, `daytona_rename_symbol`, `daytona_delete_file`, and `daytona_move_file` calls). A deleted file has no diagnostics to run; a moved file should be checked at its destination path.
2. For each file, call `ci_diagnostics(file_path)`.
3. If any diagnostic has severity `error`:
   - Fix the error immediately with `daytona_edit_file`.
   - Re-run `ci_diagnostics(file_path)` on the fixed file.
   - Repeat until the file is clean.
4. Only after all edited files pass diagnostics, proceed to your final verification command if you can still reserve one tool call for the terminal summary. If this command uses `daytona_codeact`, first inspect the literal command string and rewrite it unless it contains no `|` and no `>`.
5. After final verification, immediately call exactly one `submit_task_summary(...)`. Use `type="success"` only for green evidence; otherwise use `type="request_replan"` with the red command, diagnostic, blocker, or incomplete-verification evidence plus the trigger classification. Do not imply that a same-scope continuation is needed when the classification is `none`.

## Expected Outcome

- Every edited file is diagnostics-clean before the final runtime verification and summary.
