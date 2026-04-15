# Pre-Completion Validation

Use this reference before signaling completion when you have made source edits.

## Mandatory diagnostics gate

Before your final message, run `ci_diagnostics(file_path)` on **every file you edited** during this task. This catches import errors, undefined names, syntax errors, and type mismatches before the validator or any parallel sibling sees your changes.

## Workflow

1. Collect the list of files you edited (from your `daytona_edit_file` / `daytona_write_file` calls).
2. For each file, call `ci_diagnostics(file_path)`.
3. If any diagnostic has severity `error`:
   - Fix the error immediately with `daytona_edit_file`.
   - Re-run `ci_diagnostics(file_path)` on the fixed file.
   - Repeat until the file is clean.
4. Only after all edited files pass diagnostics, proceed to your final verification command.

## Why this matters

A single unresolved `NameError` or broken import in a widely-imported file (e.g. `pkg/__init__.py`) cascades to every downstream test and every parallel developer sharing the workspace. Catching it here — before the validator cycle — prevents systemic damage that is expensive to recover from.

## Rules

- Do not skip this step even if your narrow verification command passed. A passing narrow test does not prove that your edits left no import or name errors in files outside the test's import chain.
- Do not treat `ci_diagnostics` as a substitute for runtime verification. You still must run the assigned verification command. This is a pre-flight check, not the verdict.
- If `ci_diagnostics` reports errors you cannot fix without widening scope, call `submit_task_summary(type='fail')` with the exact diagnostic output instead of leaving the errors in place.
