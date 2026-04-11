# Corrective Fast Path

Use this reference only when the validator packet already names exact failing pytest ids and exact existing owner files.

## Workflow

1. Must start with `ci_scoped_status(scope_paths=[...])` on the exact owner surface.
2. Must confirm the owner surface is still live.
3. Must draft corrective JSON as soon as the failing cluster, owner surface, and retry target are clear.

## Rules

- Must keep owner paths exact.
- May carry one exact missing import-path file when the parent package already exists live.
- If a narrowed pytest node is missing but the parent packet still owns the exact benchmark file, keep the retry surface on that file path.
- Never reopen benchmark test bodies, decorators, parametrization markers, or shared plumbing to re-derive semantics.
- Never merge distinct corrective clusters into one item.
- If the validator failed before the target collected, must keep that failing command visible and route the correction toward the shared owner or runtime-control surface.
- If the same owner cluster was already reopened once and is still clear, must emit JSON now.

## Few-shot examples

- Example: the validator packet says `tests/test_hdf.py` fails on `from pkg._compat import X` and live structure shows `pkg/` exists.
  The corrective target may be `pkg/_compat.py`.
  Do not reopen the test body to rediscover the same import failure.
- Example: the validator command never reaches the named test because `pkg/__init__.py` crashes during collection on a missing symbol.
  Keep that exact command in the corrective payload and replan toward the shared import owner.
  Do not "fix" the issue by deleting the failing verification step.
- Example: the validator packet says `pkg/tests/test_io_json.py::test_chunksize` is missing, but the inherited payload already owned `pkg/tests/test_io_json.py`.
  Keep the corrective retry target on `pkg/tests/test_io_json.py` and route the lane back through the live owner file.
  Do not escalate to `benchmark_surface_mismatch`, and do not guess a nearby replacement node.
