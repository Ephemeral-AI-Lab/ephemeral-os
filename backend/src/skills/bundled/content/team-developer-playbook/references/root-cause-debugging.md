# Root Cause Debugging

Use this reference when the first reproduction still leaves the bug ambiguous, the traceback lands far from the likely source, or you catch yourself cycling through reads without a falsifiable hypothesis.

## Required checkpoint before first edit

Before the first source edit, write down this packet:

```json
{
  "observed_failure": "exact failing command, node, import, warning, or assertion",
  "first_boundary": "the first production function, module, helper, import chain, or config surface where behavior diverges",
  "hypothesis": "one concrete statement of what is wrong and why the evidence points there"
}
```

If you cannot state all three after the first reproduction, gather one more bounded piece of evidence instead of patching.

## Debug loop

1. Reproduce exactly once on the owned verify surface.
2. Read the traceback or assertion carefully.
3. Identify the first failing boundary, not just the final test assertion.
4. Gather one bounded confirming datum.
5. State one hypothesis.
6. Make one minimal edit or one minimal proving check.
7. Re-verify on the same narrow surface.

## Dead-cycle breaker

If one scoped packet, one symbol/reference query, and one proving repro all land on the same boundary, stop exploring. The next action must be one of:

1. Make the smallest production edit at that boundary.
2. Repair or revert your own last experiment first if it broadened the red surface into a shared startup, import, or warning-filter crash.
3. Surface one concrete blocker tied to that boundary.
4. Replan because the boundary is shared or unowned.

## Stop signs

- You are about to reread files without a new question.
- You are about to treat payload prose, repo history, or failure counts as stronger evidence than the current red node.
- You are about to call a still-red owned verify failure "pre-existing" or plan to ignore it.
- The same boundary already survived one proving repro and you are still reading siblings instead of patching or replanning.
- Do not call the red verify target inverted or a "wrong" test while the owned loader or access gate is still red.

## Few-shot examples

- Example:
  ```json
  {
    "observed_failure": "pytest pkg/tests/test_hdf.py -x dies while parsing warning filters after from pkg._compatibility import FLAG",
    "first_boundary": "startup import chain pkg/base.py -> pkg.compatibility",
    "hypothesis": "a new deprecation hook now fires during package import instead of only on explicit public access"
  }
  ```
  The first failing boundary is the shared compat/export surface. Deprecation hooks belong on explicit public access paths only.
  Confirm the importer chain once, then switch startup callers like `pkg/base.py` to a quiet supported path such as `pkg._compat` and rerun the exact verify command.
  do not rewrite the test import or add a module-level deprecation hook on the public wrapper while startup still uses it.
- Example: the exact pytest target returns `ERROR: not found`, exit code 4, or `no tests ran`.
  Treat that as a wrong-target or stale-target control failure, not proof the owned surface is green. Re-collect the current target or replan from the latest healthy checkpoint.
- Example: a verify file is red only because startup still imports the noisy public wrapper.
  Do not treat the verify target list as edit ownership, and never reach for a root-only skip, xfail, or verify-file rewrite instead of fixing the owned loader or access gate.
