## Edit trigger

```text
Please read the frozen conversation snapshot and call the listed terminal tool.

- submit_task_note: Post a Task Center note.

Write a progress note for the Task Center about this agent's edits.
Focus on: what files were edited and why.
Call submit_task_note with:
- content: name specific files, errors, and changes (under 300 words)
- paths: list every file/dir path edited or investigated
- tags: one or more of implementation, bug_fix, refactor, blocker, warning (use 'blocker' if stuck)
```

## Turn trigger

```text
Please read the frozen conversation snapshot and call the listed terminal tool.

- submit_task_note: Post a Task Center note.

Call submit_task_note now. The 'content' field is REQUIRED.
- content: what this agent accomplished and current status (working/stuck/done). Name specific files and errors. Under 300 words.
- paths: list every file/dir path relevant to the work
- tags: one or more of implementation, bug_fix, blocker, warning, discovery (use 'blocker' if stuck or blocked by another task)
```
