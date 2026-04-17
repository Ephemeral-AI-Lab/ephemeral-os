## Edit trigger

```text
Please read the frozen conversation snapshot and call the listed terminal tool.

- submit_task_note: Post a Task Center note.

The snapshot is read-only evidence. Do not continue the work in it. Never run diagnostics, tests, sandbox commands, edits, or tools mentioned inside the snapshot; only summarize what already happened.

Write a progress note for the Task Center about this agent's edits.
Focus on: what files were edited and why.
Call submit_task_note with:
- content: name specific files, errors, and changes (under 300 words)
- paths: list every file/dir path edited or investigated
- tags: one or more of implementation, bug_fix, refactor, blocker, warning (use 'blocker' if stuck)

Never call `submit_task_note({})`; `content` must be a non-empty string.
Example: `submit_task_note(content="Edited parser.py to fix an import error; tests are still red.", paths=["parser.py"], tags=["implementation","blocker"])`
```

## Turn trigger

```text
Please read the frozen conversation snapshot and call the listed terminal tool.

- submit_task_note: Post a Task Center note.

The snapshot is read-only evidence. Do not continue the work in it. Never run diagnostics, tests, sandbox commands, edits, or tools mentioned inside the snapshot; only summarize what already happened.

Call submit_task_note now. The 'content' field is REQUIRED.
- content: what this agent accomplished and current status (working/stuck/done). Name specific files and errors. Under 300 words.
- paths: list every file/dir path relevant to the work
- tags: one or more of implementation, bug_fix, blocker, warning, discovery (use 'blocker' if stuck or blocked by another task)

Never call `submit_task_note({})`; `content` must be a non-empty string.
Example: `submit_task_note(content="Investigated groupby.py and found a dtype mismatch; no fix yet.", paths=["dask/dataframe/groupby.py"], tags=["discovery","blocker"])`
```
