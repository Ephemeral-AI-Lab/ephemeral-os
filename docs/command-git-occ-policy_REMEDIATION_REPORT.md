# Command Git OCC Floor Remediation Report

Status: Complete
Date: 2026-06-17

## Scope

Implement `docs/command-git-occ-policy_REMEDIATION_SPEC.md` end to end against
the live repo. The work covers command-lane Git metadata route safety, object
database path validation, reflog append validation, incomplete operation marker
classification, command trace rejection details, docs drift, and focused plus
live E2E coverage.

Pre-existing worktree state at start:

- `docs/command-git-occ-policy_SPEC.md` had user-owned edits.
- `docs/command-git-occ-policy_REMEDIATION_SPEC.md` was untracked.

## Implementation Progress By Milestone

| Milestone | Status | Notes |
| --- | --- | --- |
| A: Safety fixes | Complete | Protected `.git` drops now reject command publish with Git-specific reason codes; object writes are canonical loose-object only; reflog append validation preserves record boundaries; incomplete operation markers were expanded. |
| B: Command finalization and trace | Complete | Git route rejection remains atomic for source, ignored, spooled ignored, and Git paths; command trace now emits per-path `command.publish_rejection_detail` with structured `publish_lanes`. |
| C: Live E2E coverage | Complete | Workspace-runtime-command test cases were added and the final live suite passed 83/83. |
| D: Docs and closeout | Complete | Ignored-state spec, Git OCC spec, implementation outcome notes, workspace-runtime-command README/index, and generated E2E index pages were updated. |

## Files Changed

- `docs/command-git-occ-policy_REMEDIATION_REPORT.md`
- `docs/command-git-occ-policy_SPEC.md`
- `docs/command-ignored-state-publish_SPEC.md`
- `docs/command-ignored-state-publish_IMPLEMENTATION_OUTCOME.md`
- `crates/daemon/layerstack/src/commit/mod.rs`
- `crates/daemon/layerstack/src/service.rs`
- `crates/daemon/layerstack/tests/unit/route.rs`
- `crates/daemon/operation/src/command/contract.rs`
- `crates/daemon/operation/src/command/finalize.rs`
- `crates/daemon/operation/src/command/trace.rs`
- `crates/daemon/operation/src/command/service/lifecycle.rs`
- `crates/daemon/operation/tests/command/service.rs`
- `crates/e2e-test/tests/workspace-runtime-command/command_error_and_backpressure.rs`
- `crates/e2e-test/tests/workspace-runtime-command/readme.md`
- `crates/e2e-test/tests/workspace-runtime-command/readme.json`
- `crates/e2e-test/tests/workspace-runtime-command/index.html`
- `crates/e2e-test/tests/index.html`

## Tests Added

- LayerStack route/unit coverage for command `.git` protected drops, canonical
  loose-object acceptance, unsupported object DB rejection, reflog record
  boundary rejection, reflog exact no-op routing, and expanded incomplete
  markers.
- Operation finalization coverage for Git rejection atomicity, structured
  rejection details, trace event bridging, and spooled ignored cleanup.
- Live E2E coverage for required workspace-runtime-command Git OCC remediation
  families.

## Verification Commands Run

| Command | Result | Notes |
| --- | --- | --- |
| `git status --short` | Pass | Showed modified `docs/command-git-occ-policy_SPEC.md` and untracked remediation spec. |
| `git diff --stat` | Pass | Showed 6-line user-owned spec edit only. |
| Read full remediation and related specs | Pass | Read `docs/command-git-occ-policy_REMEDIATION_SPEC.md`, `docs/command-git-occ-policy_SPEC.md`, and `docs/command-ignored-state-publish_SPEC.md`. |
| Source/test inspection with `rg` and `sed` | Pass | Inspected route decisions, command finalization, trace plumbing, and existing workspace-runtime-command tests. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack command_git` | Pass after test correction | Initial run exposed test-fixture issues; rerun passed 11/11. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation git_` | Pass after cleaning stale command artifact | Passed 5 operation Git-focused tests plus checkpoint tests. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p e2e-test --no-run` | Pass | E2E test targets compile. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation command::` | Pass | Passed 57 command-focused operation tests. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack route_tests` | Pass | Passed 50 route tests. |
| `cargo fmt --check` | Pass | Final ladder formatting gate passed. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack` | Pass | Passed 93 lib tests, 1 CAS fixture test, 9 stack tests, and doctests. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation --all-targets` | Pass | Passed 76 lib tests, 4 checkpoint tests, and 1 contract test. |
| `cargo run -p xtask -- package` | Pass | Rebuilt packaged daemon, sha256 `d2a3403f978a9aa39e3da69a3270aecfa6c6aad09ece472a4fd37580d2ab0fea`. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p e2e-test --bin e2e-runner -- --suites workspace-runtime-command --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | Fail, then fixed | First live run `e2e-run-1781637014626` passed 82/83. `rm -rf .git` exited nonzero on overlay lowerdir I/O errors before publish validation. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p e2e-test --features e2e --test workspace-runtime-command command_error_and_backpressure::rm_rf_git_rejects_and_preserves_head -- --nocapture --test-threads 1` | Pass | Focused rerun passed after forcing shell exit 0 while preserving the Git deletion publish-rejection assertion. |
| `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p e2e-test --bin e2e-runner -- --suites workspace-runtime-command --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | Pass | Final live run `e2e-run-1781637138818` passed 83/83; summary duration 38.67s, max_parallel 5, container_weight_cap 10. |
| `node tools/generate-readme-pages.mjs` from `crates/e2e-test` | Pass | Regenerated module E2E README pages after README/index edits. |
| `jq -e '.passed == true and .max_parallel == 5 and .container_weight_cap == 10 and ([.suites[].status] \| all(. == 0))' crates/e2e-test/test-reports/runs/e2e-run-1781637138818/summary.json` | Pass | Final E2E summary validation returned `true`. |
| `git diff --check` | Pass | No whitespace errors. |

## Pass/Fail Results

- Focused LayerStack and operation tests pass.
- E2E test binaries compile.
- Packaged daemon rebuild passed.
- Final live workspace-runtime-command E2E passed 83/83.
- `git diff --check` passed.

## Unresolved Risks Or Skipped Verification

- No skipped verification.
- Remaining architectural scope is outside this remediation: clean commit
  acceptance, ref fast-forward support, frame-shift handling, quiet-stack merge,
  repository health validation, ref deletion, and Git GC/pruning remain deferred
  by `docs/command-git-occ-policy_REMEDIATION_SPEC.md`.

## Final Status

Complete.
