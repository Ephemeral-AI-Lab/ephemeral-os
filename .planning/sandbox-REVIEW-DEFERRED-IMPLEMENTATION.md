# Sandbox Deferred Review Implementation Report

Source review: `.planning/sandbox-REVIEW-DEFERRED.md`

## Current Baseline

- Starting dirty worktree contained pre-existing changes under `backend/src/task_center*`.
- Sandbox work will avoid those paths unless a later phase explicitly requires them.
- `.planning/sandbox-REVIEW-DEFERRED.md` is untracked in this checkout and treated as the source artifact for this pass.

## Phase 1 - Prep Guard

Status: complete

Scope:
- Inspect `git status --short`.
- Read `.planning/sandbox-REVIEW-DEFERRED.md`.
- Consult `.planning/sandbox-REVIEW.md` and `/tmp/sandbox_review/execution.md` only for the C2 blocker and implementation shape.
- Establish this report.

Selected implementation order:
1. C2 two-pipeline collapse.
2. S4 provider Daytona client collapse.
3. S5 OCC flattening.
4. S6 plugin runtime flattening with compatibility shim.
5. Deferred daemon depth decision.
6. Local cleanups S7-S10 and smaller wins.
7. Cross-cutting naming renames only after flattening phases are green.

Blocker review:
- The historical C2 blocker is `backend/tests/unit_test/test_sandbox/test_overlay/test_overlay_dependency_boundaries.py`, which asserts the overlay runner/pipeline/worker/mount files exist.
- The current task resolves the direction: collapse into `orchestrator.execute_command(..., occ_apply=False, mount_mode=MountMode.COPY_BACKED)` and rewrite/delete tests according to the new boundary.
- Public surface choice for C2: use `occ_apply: bool = True`, matching the deferred review's preferred flag.

Changed files:
- `.planning/sandbox-REVIEW-DEFERRED-IMPLEMENTATION.md`

Deleted files:
- None

Compatibility shims:
- None

Tests and guards run:
- `git status --short`
- `git diff --stat`
- `git diff --check`

Failures and fixes:
- None

Next phase recommendation:
- Proceed to C2. Start by migrating daemon overlay calls to the orchestrator with `occ_apply=False`, then remove obsolete overlay pipeline modules after tests are rewritten.
