# Action Reference: Add Corrective Tasks

Use this after failure-mode classification and any diagnostics have produced a corrective mapping with no stale sibling cancellation. Final payload shape lives in `terminal-contract`; this reference only decides what work is allowed.

If the payload needs any `cancel_ids`, stop and load `action-cancel-and-redraft` instead.

## Allow

- Add direct children of this replanner for:
  - `scope_expansion`
  - `wrong_owner_or_role`
  - `unresolved_blocker` after diagnostics identify a production repair surface
- Create only `developer` repair tasks and optional `validator` verification tasks.
- Keep repair work anchored to the failed task and preserved dependents.
- Merge nearby same-file seams into one developer task.
- Add a validator only when a distinct verification lane is useful and no preserved downstream validator covers the repair.

## Drop

- Same-scope continuation with no root-cause trace; same-scope repair with a named production mechanism is valid.
- Budget exhaustion, failed attempts, incomplete verification, or ambient sibling drift.
- Benchmark-test edits, test-derived helpers, and missing paths proven only by tests.
- Skip, xfail, test rewrite, pytest configuration, or benchmark harness changes intended to make verification green.
- Dropping a named fail-to-pass variant by labeling it a test design issue, unsupported parametrization, or cross-engine mismatch.
- Documentation-only or validation-only tasks that accept a named fail-to-pass failure as non-fixable, environmental, unsupported, or residual risk without assigning a production repair or concrete production diagnostic.
- Work already owned by an uncancelled live sibling.
- Duplicate validators/dependents already rewired to this replanner.
- Child `team_planner`, `root_planner`, `team_replanner`, or `scout` tasks.
- New-file, move, shim, bridge, or re-export work without production evidence for the destination.

## Build

1. For each candidate task, name the failure mode and root-cause trace entry it addresses.
2. Keep the required top-level `cancel_ids` key explicitly set to `[]`.
3. Use local deps only for real output ordering; do not add deps for mere scope overlap. Overlapping `scope_paths` between sibling developers are allowed — the runtime uses OCC to resolve concurrent edits to the same file, so do not invent dependencies, narrow scopes, or merge tasks just to avoid file overlap.
4. It is fine for two developers to own the same production file when their work is logically distinct; OCC will reconcile concurrent edits. Only merge into one developer task when the seams are tightly coupled enough that splitting them would force one author to reason about the other's in-flight changes.
5. Tell corrective developers to run `ci_diagnostics(file_path=...)` first.
6. For moves/renames, name `daytona_move_file`; for production removals, name `daytona_delete_file`. Do not use CodeAct or shell cleanup commands for deletes, and do not create corrective tasks whose only work is removing `__pycache__`, `.pyc`, build caches, or other ignored transient artifacts.
7. If a separate verification lane is useful and no preserved downstream validator covers the surface, add a validator with deps on the local repair ids it verifies.
8. Reject any candidate whose `scope_paths` include tests, benchmark harness files, or pytest/config verification files unless the original user request explicitly asked to repair tests rather than production behavior.
9. Keep every named failing variant assigned to a production repair, a diagnostic developer that tests a concrete production seam, or an explicitly identified live repair owner whose task details or terminal summary covers that same variant and seam. A downstream validator may verify coverage, but it does not replace the repair owner. Do not satisfy this requirement with residual risk prose, "out of scope" text, broad validator coverage, or a task whose only outcome is documenting a known red command or declaring the failure non-fixable.
10. For value-selection bugs, reject a repair task whose proposed rule cannot satisfy every observed expected/actual row in the same failing assertion; make a diagnostic developer derive the rule instead.
11. Load `terminal-contract`, self-check the payload, then submit exactly one `submit_replan(...)` call.

## Expected Outcome

The replanner adds only missing corrective children, leaves valid siblings running, and lets already-rewired downstream tasks wait on this replanner instead of duplicating them.
