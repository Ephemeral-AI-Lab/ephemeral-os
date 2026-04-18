# Corrective Fast Path

Use this reference only when the validator packet already names exact failing pytest ids and exact existing owner files.

## Task/Goal

- The validator packet already names an exact failing cluster and an exact live owner surface.

## Avoid

- Must keep owner paths exact and keep the failing command visible when the validator failed before target collection.
- The validator must still run after corrective developer failure.
- May carry one exact missing import-path file only when non-test production evidence proves the absent path is the intended live owner; a live parent package or in-scope source file is not enough.
- If the validator packet already names the live benchmark file and only the current verify command is wrong, correct the failure target and stop.
- Never reopen benchmark test bodies, decorators, parametrization markers, or shared plumbing to re-derive semantics.
- Never inspect benchmark tests or git history to overrule a failed developer's outside-scope missing-module stop signal.
- Never inspect similarly named live modules, package aliases, or adjacent compatibility files after an outside-scope missing-module stop signal just to justify a test-derived missing path.
- Never make a benchmark test file the corrective owner because the packet suggests the test import, decorator, parametrization, or assertion is wrong; leave it as evidence and choose production ownership or a production-boundary planner.
- Never split distinct corrective clusters into parallel tasks that share an owner file; sequence them with `deps`, or use one focused repair task when the same file owns all failures.
- Never create, rename, move, or re-export a missing compatibility module when the only evidence is a test import or collection error, even if an in-scope compatibility file has a similar name.
- Never use an in-scope source compatibility file to justify an absent outside-scope destination path named only by tests.
- When a missing path is named only by tests and no non-test production owner was already proven before the stop signal, the fast path is an empty `submit_replan(new_tasks=[], cancel_ids=[])`, not a new shim, alias, re-export, rename, move, or finder task.

## Workflow

1. Must confirm the owner surface is still live.
2. May use `read_task_note(paths=[...])` once to confirm a same-run shared brief on that exact owner surface before deeper archaeology.
3. Must load the relevant action reference before `submit_replan`.
4. Must draft corrective JSON as soon as the failing cluster, owner surface, and failure target are clear.
5. If a `submit_replan(...)` validation error occurs, do not run new discovery. Retry only a mechanical payload correction from the validation message and prior evidence.

## Expected Outcome

- The replanner emits a minimal corrective plan directly from the validator packet without reopening the benchmark surface.
