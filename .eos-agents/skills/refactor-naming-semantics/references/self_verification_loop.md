# Autonomous Self-Verification Loop

Use this reference for multi-pass refactors and broad renames.

## Pre-Edit Baseline

1. Record the target boundary and expected behavior invariants.
2. Find importers, references, tests, fixtures, mocks, and public entry points.
3. Run or identify the narrowest relevant existing checks.
4. Run `scripts/refactor_audit.py` when the target spans more than one file or public symbol.
5. For longer work, initialize a loop note file with `scripts/refactor_loop_notes.py` and update it after each pass.

## Edit Loop

Repeat this loop until the change converges:

1. Make one coherent pass: deletion, rename, call-site update, or type-boundary cleanup.
2. Re-run the narrowest relevant check for that pass.
3. If the check fails, inspect the first concrete failure and classify it:
   - Refactor breakage: fix and rerun the same check.
   - Existing unrelated failure: document it and continue only if it does not hide the refactor risk.
   - Ambiguous contract: stop and ask the user.
4. Search for stale old names, deleted imports, aliases, and dead tests.
5. Append the pass outcome to the loop note file when one exists.
6. Continue only when the next pass has a clear reduction or semantic benefit.

## Separate Context Passes

Use separate passes instead of overloaded instructions:

- Implementer pass: performs one coherent rename, deletion, or consolidation.
- De-sloppify pass: removes redundant tests, over-defensive checks, stale comments, logging, and speculative abstractions introduced by the implementer.
- Reviewer pass: checks correctness, public compatibility, naming semantics, and missed call sites.
- Verifier pass: runs commands and summarizes the first concrete failure.

For non-trivial code authored by a subagent or a previous pass, prefer a reviewer subagent that did not write the code.

## Stop Conditions

Stop editing when:

- No high-confidence naming or deletion improvement remains inside the target boundary.
- Further changes require a public API decision not present in the repo.
- The remaining cleanup would touch unrelated ownership boundaries.
- Verification failures are unrelated or require broader product decisions.
- The maximum planned pass count is reached.
- The same concrete failure repeats after one focused retry.
- Context is too low and no current handoff file exists.

## Final Gate

Before reporting completion:

- Re-run importer/reference searches for all renamed or deleted public-looking symbols.
- Run `git diff --check` when in a git repo.
- Run the final narrow tests or checks.
- Confirm public facades preserved intentionally are thin and documented in the final response.
- Confirm loop notes, if used, match the final state.
- Report exact checks and any residual risk.
