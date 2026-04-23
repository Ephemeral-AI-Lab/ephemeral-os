# Action Reference: Add Corrective Tasks

Use this after classification and diagnostics produce corrective work with no stale sibling cancellation. Final payload shape lives in `terminal-contract`; this reference only decides what work is allowed.

If any `cancel_ids` are needed, load `action-cancel-and-redraft` instead.

## Decision Contract

Add direct children of this replanner for `scope_expansion`, `wrong_owner_or_role`, or `unresolved_blocker` after diagnostics identify a production repair surface. Create only `developer` repair tasks and optional `validator` verification tasks.

Same-scope continuation with no root-cause trace is invalid; same-scope repair with a named production mechanism is valid.

Keep the required top-level `cancel_ids` key explicitly set to `[]`.

## Drop

- Budget exhaustion, failed attempts, incomplete verification, or ambient sibling drift.
- Benchmark-test edits, test-derived helpers, missing paths proven only by tests, skip/xfail/test rewrite/pytest config/benchmark harness changes intended to make verification green.
- Test repair by proxy: trigger -> candidate spec tells a developer to edit, restore, checkout, or prove no diff for test, benchmark, pytest/config, or verification files while `scope_paths` names production; required action -> reject it and assign a production repair or concrete diagnostic instead; failure signal -> a new task whose goal/action mutates test evidence.
- Dropping a named fail-to-pass variant by labeling it a test design issue, unsupported parametrization, or cross-engine mismatch.
- Documentation-only or validation-only tasks that accept a named fail-to-pass failure as non-fixable, environmental, unsupported, or residual risk without production repair or concrete production diagnostic.
- Work already owned by an uncancelled live sibling.
- Duplicate validators/dependents already rewired to this replanner.
- Child `team_planner`, `root_planner`, `team_replanner`, or `scout` tasks.
- New-file, move, shim, bridge, or re-export work without production evidence for the destination.

## Build

1. For each candidate task, name the failure mode and root-cause trace entry it addresses.
2. Use local deps only for real output ordering; overlapping `scope_paths` between sibling developers are allowed. Merge same-file seams only when they are tightly coupled.
3. Tell corrective developers to run `ci_diagnostics(file_path=...)` first.
4. For moves/renames, name `daytona_move_file`; for production removals, name `daytona_delete_file`. Do not create cleanup-only tasks for `__pycache__`, `.pyc`, build caches, or other ignored transient artifacts.
5. Add a validator only when a distinct verification lane is useful and no preserved downstream validator covers the surface.
6. Reject any candidate whose `scope_paths` include tests, benchmark harness files, or pytest/config verification files unless the original user request explicitly asked to repair tests.
7. Keep every named failing variant assigned to a production repair, a diagnostic developer that tests a concrete production seam, or an explicitly identified live repair owner whose task details or terminal summary covers that same variant and seam. Do not satisfy this requirement with residual risk prose, "out of scope" text, broad validator coverage, or a task whose only outcome is documenting a known red command.
8. For value-selection bugs, reject a repair task whose proposed rule cannot satisfy every observed expected/actual row in the same failing assertion; make a diagnostic developer derive the rule instead.
9. Load `terminal-contract`, self-check the payload, then submit exactly one `submit_replan(...)` call.

## Expected Outcome

The replanner adds only missing corrective children, leaves valid siblings running, and lets already-rewired downstream tasks wait on this replanner.
