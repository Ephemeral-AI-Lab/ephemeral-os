# Corrective Fast Path

Use this reference only when the validator packet already names exact failing pytest ids and exact existing owner files.

## Workflow

1. Must confirm the owner surface is still live.
2. May use `read_task_note(paths=[...])` once to confirm a same-run shared brief on that exact owner surface before deeper archaeology.
3. Must draft corrective JSON as soon as the failing cluster, owner surface, and retry target are clear.

## Rules

- Must keep owner paths exact and keep the failing command visible when the validator failed before target collection.
- Validator `cascade_policy` must stay `"continue"` so the validator still runs after corrective developer failure.
- May carry one exact missing import-path file when the parent package already exists live.
- If the validator packet already names the live benchmark file and only the current verify command is wrong, correct the retry target and stop.
- Never reopen benchmark test bodies, decorators, parametrization markers, or shared plumbing to re-derive semantics.
- Never merge distinct corrective clusters into one item.
