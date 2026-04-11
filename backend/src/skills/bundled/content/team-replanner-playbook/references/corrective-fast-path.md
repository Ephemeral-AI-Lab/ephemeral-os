# Corrective Fast Path

Use this reference only when the validator packet already names exact failing pytest ids and exact existing owner files.

## Workflow

1. Must start with `ci_scoped_status(scope_paths=[...])` on the exact owner surface.
2. Must confirm the owner surface is still live.
3. Must draft corrective JSON as soon as the failing cluster, owner surface, and retry target are clear.

## Rules

- Must keep owner paths exact.
- May carry one exact missing import-path file when the parent package already exists live.
- Never reopen benchmark test bodies, decorators, parametrization markers, or shared plumbing to re-derive semantics.
- Never merge distinct corrective clusters into one item.
- If the same owner cluster was already reopened once and is still clear, must emit JSON now.

## One-shot example

If the validator packet says `tests/test_hdf.py` fails on `from pkg._compat import X` and live structure shows `pkg/` exists, the corrective target may be `pkg/_compat.py`.

Must not reopen the test body to rediscover the same import failure.
Must not route the corrective task into an unrelated sibling file.
