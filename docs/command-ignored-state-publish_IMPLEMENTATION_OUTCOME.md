# Command Ignored-State Publish: Milestone 1 Outcome And Handoff

Date: 2026-06-16

Spec: `docs/command-ignored-state-publish_SPEC.md`

## Scope Completed

This iteration completed Milestone 1: non-success command discard semantics and
lane metadata publication. It did not attempt the later protected/Git/opaque
lane routing, file-backed spool capture, lane-aware publish API replacement, or
compaction work.

The final behavior is:

- Non-success ephemeral commands do not publish source or ignored writes into
  the mutable layer.
- Non-success finalization still records bounded lane diagnostics for response
  metadata and tracing.
- Responses include a flattened `publish_lanes` metadata object.
- Finalize tracing emits `command.publish_lanes_decided`.
- Timeout and cancellation paths now use the same finalizer path as ordinary
  nonzero command exits, so discard behavior is consistent.

## Milestone Checklist

| Item | Status | Notes |
| --- | --- | --- |
| Gate source and ignored publish for non-success commands | Complete | The non-success branch returns before OCC conflict handling, LayerStack publish, and spool installation. |
| Include flattened `publish_lanes` response metadata | Complete | Metadata records source and ignored lane statuses plus routing counts/bytes. |
| Emit `command.publish_lanes_decided` trace event | Complete | Foreground response trace and durable finalize records include the same lane object. |
| Route timeout/cancel/nonzero through shared finalizer | Complete | Lifecycle discard paths now call `finalize_ephemeral_command`. |
| Preserve successful command behavior | Complete | Successful commands still use the existing capture/publish path, with lane metadata derived from the current route snapshot. |
| Add unit and contract coverage | Complete | Operation, LayerStack, trace, response flattening, and fixture coverage were updated. |
| Add live E2E coverage | Complete | Added a workspace runtime command test proving nonzero source and ignored writes are both discarded while metadata is present. |
| Run focused and full verification | Complete | Cargo unit suites, package build, and full workspace-runtime-command live E2E suite passed. |

## Implementation Notes

Primary implementation files:

- `crates/daemon/operation/src/command/contract.rs`
  - Added `PUBLISH_LANES_METADATA_KEY`.
  - Added `PublishLanesMetadata`, source/ignored lane metadata, routing metadata,
    and insertion helpers.
- `crates/daemon/operation/src/command/finalize.rs`
  - Added the non-success publish gate before OCC/publish/spool side effects.
  - Added response metadata for dropped source and ignored lanes.
  - Added lane metadata for successful command responses.
- `crates/daemon/operation/src/command/service/lifecycle.rs`
  - Routed ephemeral timeout, cancellation, and non-success lifecycle outcomes
    through `finalize_ephemeral_command`.
- `crates/daemon/operation/src/command/trace.rs`
  - Added `publish_lanes` to finalize trace facts.
  - Emitted `command.publish_lanes_decided`.
- `crates/daemon/layerstack/src/commit/mod.rs`
  - Added `CaptureRouteStats`.
  - Added snapshot-manifest route classification so lane diagnostics are stable
    against the finalize-time route manifest.
- `crates/daemon/layerstack/src/service.rs`
  - Exposed `capture_route_stats_for_snapshot`.
- `crates/daemon/layerstack/src/lib.rs`
  - Re-exported `CaptureRouteStats`.

## Post-Review Fixes

The review pass found two Phase 1 correctness gaps. Both were fixed in this
follow-up:

- `publish_capture_with_options` now prepares route decisions and gated base
  hashes from the command snapshot manifest and passes those decisions to the
  commit worker. Successful command publish routing no longer drifts to the
  active head's current `.gitignore` view after the command snapshot was leased.
- Non-success ephemeral finalization now falls back to a terminal
  `dropped_command_failed` response with flattened `publish_lanes` metadata when
  upperdir payload capture itself fails, such as an oversized failed-command
  ignored write. The fallback does not enter OCC or publish a LayerStack layer.
- Added regression coverage for snapshot-route publish drift and oversized
  non-success capture fallback.

Test and fixture files updated:

- `crates/daemon/layerstack/tests/unit/route.rs`
- `crates/daemon/operation/tests/command/service.rs`
- `crates/daemon/operation/tests/contract.rs`
- `crates/daemon/operation/fixtures/command_finalize_conflict_response.json`
- `crates/e2e-test/tests/workspace-runtime-command/command_error_and_backpressure.rs`

## Verification

Commands run:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack capture_route_stats_use_supplied_manifest_snapshot
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation command::
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation --all-targets
cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p e2e-test --bin e2e-runner -- --suites workspace-runtime-command --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4
git diff --check
```

Results:

- `cargo fmt` passed.
- Focused LayerStack route stats and snapshot-publish regression tests passed.
- Focused operation command tests passed.
- Full `layerstack` package tests passed.
- Full `operation --all-targets` tests passed.
- `xtask package` passed and rebuilt `dist/eosd-linux-amd64`.
- Final live workspace-runtime-command E2E run passed 60/60.
- `git diff --check` passed.

Live E2E report root:

```text
crates/e2e-test/test-reports/runs/e2e-run-1781606945305
```

Important verification context:

- An initial live E2E attempt used a stale packaged daemon and failed the new
  test. Rebuilding with `cargo run -p xtask -- package` fixed that.
- One existing `setsid_nohup_contract` case failed once during a full run,
  passed when rerun alone, and passed again in the final full 60/60 suite.

## Subagent Coordination

The central plan was split into implementation, test coverage, and adversarial
review lanes.

- One subagent completed a useful implementation pass touching the operation
  contract, finalizer, lifecycle, trace, tests, and fixture work. Its output was
  reviewed and integrated against the spec.
- Two subagent runs failed because the selected model was at capacity. Their
  intended coverage was completed locally before this handoff.
- The final gate remained local: changed code was inspected against the
  milestone checklist, focused tests were run, the daemon was repackaged, and
  the full live E2E suite passed.

## Remaining Work

The following spec areas are intentionally left for later iterations:

- Protected path, Git path, and opaque ignored-path classification reasons.
- Bounded spool capture with file-backed digests.
- Lane-aware publish API that replaces the existing all-capture publish path.
- Configurable response/trace byte limits for ignored-lane metadata.
- Compaction and retention semantics for ignored-lane artifacts.
- Contract expansion for later milestones once those behaviors exist.

## Handoff Risks And Review Focus

- Non-success commands now capture bounded route metadata before returning, but
  they do not publish source or ignored writes. This is deliberate diagnostic
  metadata, not a mutable-layer side effect.
- Successful commands still use the existing all-capture publish path. The new
  `publish_lanes` object reflects route classification, but the full lane-aware
  publish API is still future work.
- Live E2E validation depends on the packaged daemon under `dist/`; run
  `cargo run -p xtask -- package` before live E2E when daemon code changes.
- Reviewers should pay particular attention to whether the non-success gate is
  early enough in `finalize_ephemeral_command` and whether timeout/cancel paths
  should preserve any additional legacy discard-side trace facts.
