# Command Git OCC Floor Remediation Spec

Status: Draft
Date: 2026-06-17

Related:

- `docs/command-git-occ-policy_SPEC.md`
- `docs/command-ignored-state-publish_SPEC.md`
- `docs/command-ignored-state-publish_IMPLEMENTATION_OUTCOME.md`

## 1. Purpose

Remediate the adversarial review findings against the first sandbox-side
Command Git OCC floor.

This spec is deliberately narrower than the full Command Git OCC architecture.
It does not add clean commit acceptance, ref fast-forward support, frame-shift
classification, quiet-stack merging, agent hooks, command-text parsing, ref
deletion support, repository GC/pruning, or daemon-side command admission.

The goal is to make the current floor safe, internally documented, and covered
well enough that later Git OCC milestones can build on it without carrying
known correctness holes.

## 2. Current Floor Contract

The current floor permits only this command-produced Git metadata:

1. No-op/stat-cache-only `.git/index` refreshes, normalized away with
   `git_index_stat_refresh`.
2. New canonical loose Git object files under `.git/objects/XX/YYYY...`, where
   `XX` is two lowercase hex characters and `YYYY...` is the remaining
   38 lowercase hex characters.
3. Existing object paths rewritten with identical bytes, treated as idempotent
   gated metadata.
4. Append-only reflog writes that preserve complete existing records.
5. Allowed operation message writes such as `.git/COMMIT_EDITMSG`,
   `.git/MERGE_MSG`, and `.git/SQUASH_MSG`.

All accepted Git metadata must route through gated OCC. No accepted `.git/**`
path may route through ordinary ignored/direct LWW.

Every destructive, incomplete, unsupported, or unsafe Git metadata final state
must reject the whole command publish. A rejected command publishes no source
lane, no ignored lane, and no Git metadata lane.

## 3. Findings To Remediate

### 3.1 Git Special Files Bypass Fatal Git Rejection

Current capture records unsupported non-file entries as protected drops before
route classification. Protected drops currently set `reject_publish = false`,
so an unsupported special file under `.git` can be dropped while unrelated
source or ignored output from the same command still publishes.

Required behavior:

- Under command Git OCC policy, any protected drop whose path contains a `.git`
  segment must be a publish-rejecting Git metadata decision.
- More specific Git reason codes must be preserved when the path shape makes
  the class clear:
  - `.git/**/*.lock` => `git_lock_file`
  - `.git/hooks/**` => `git_hook_write`
  - incomplete operation markers => `git_incomplete_operation`
  - otherwise unsupported `.git` special entries => `git_metadata_unsupported`
- The command response must have `success = false`, no `changed_paths`, and
  `publish_lanes.routing.drop_reason_counts` containing the Git reason.
- The active manifest must not advance.

### 3.2 Object Database Acceptance Is Too Broad

The current object rule accepts any absent path below `.git/objects/**` as a new
object write. That includes object database control files and unsupported pack
files such as:

- `.git/objects/info/alternates`
- `.git/objects/info/packs`
- `.git/objects/pack/*.pack`
- `.git/objects/pack/*.idx`
- malformed loose object paths

Required behavior:

- Accept only canonical loose-object paths in the current floor:
  `.git/objects/[0-9a-f]{2}/[0-9a-f]{38}`.
- Reject every other `.git/objects/**` write with `git_metadata_unsupported`
  until pack/control-file handling and repository health validation are
  intentionally implemented.
- Continue rejecting existing canonical loose-object paths with different
  content as `git_object_rewrite`.
- Continue accepting existing canonical loose-object paths with identical
  content as idempotent gated metadata.

### 3.3 Reflog Append Check Is Byte-Prefix Only

The current reflog check accepts any new bytes that start with the old bytes.
That can mutate the final logical reflog record when the base reflog is not
newline-terminated.

Required behavior:

- Exact no-op reflog writes may be accepted through gated OCC.
- New reflog creation may be accepted only if the new content consists of
  complete newline-terminated records, or if the implementation deliberately
  documents and tests an empty-file edge case.
- Existing reflog append may be accepted only when the base bytes are a prefix
  and the appended suffix starts at a record boundary. In practice for this
  floor, non-empty base bytes must end in `\n` before any additional bytes are
  accepted.
- Prefix matches that extend a non-newline-terminated base record must reject
  with `git_reflog_rewrite`.

### 3.4 Incomplete Operation Marker Codes Are Incomplete

Some Git control files currently reject only as `git_metadata_unsupported`
instead of the more instructive `git_incomplete_operation`.

Required behavior:

- Extend incomplete operation marker detection for known root markers including:
  - `.git/REBASE_HEAD`
  - `.git/AUTO_MERGE`
  - `.git/MERGE_AUTOSTASH`
  - `.git/MERGE_MODE`
  - `.git/MERGE_RR`
  - `.git/BISECT_HEAD`, `.git/BISECT_LOG`, `.git/BISECT_NAMES`,
    `.git/BISECT_START`, `.git/BISECT_TERMS`
- Keep directory markers such as `.git/sequencer/**`, `.git/rebase-merge/**`,
  and `.git/rebase-apply/**` rejecting as `git_incomplete_operation`.

### 3.5 Command Trace Drops OCC Rejection Detail

`ChangesetResult::trace_events()` exposes OCC conflict details, but command
response trace records currently forward only `command.publish_lanes_decided`.

Required behavior:

- Command finalization must bridge OCC publish/conflict events into command
  trace records, or emit equivalent command-level events.
- A Git metadata route rejection trace must expose at least:
  - path,
  - status/reason,
  - message,
  - whether the rejection came from route validation,
  - the structured `publish_lanes` object.
- Trace-only diagnosis must not require reading only the flattened command
  response.

### 3.6 Documentation Drift

The ignored-state spec still contains pre-Git-OCC wording that says command
`.git/**` is dropped with `git_metadata_unsupported` until command Git OCC is
available.

Required behavior:

- Update ignored-state docs to say command finalization delegates `.git/**` to
  `docs/command-git-occ-policy_SPEC.md`.
- Reserve `git_metadata_unsupported` for generic non-command `.git` fallback
  and for command Git metadata outside the current safe accepted set.
- Update the workspace-runtime-command E2E README row that still describes old
  behavior where Git metadata does not block ordinary ignored output.

## 4. Implementation Plan

### Milestone A: Safety Fixes

1. Make protected-drop conversion policy-aware.
2. Under `GitMetadataPolicy::CommandOccFloor`, convert `.git` protected drops
   into publish-rejecting Git metadata decisions.
3. Add canonical loose-object path validation.
4. Reject unsupported object database paths with `git_metadata_unsupported`.
5. Tighten reflog append validation to preserve record boundaries.
6. Extend incomplete-operation marker detection.

Milestone A is not complete until unit tests prove every changed rule and the
existing command Git route tests still pass.

### Milestone B: Command Finalization And Trace

1. Preserve all-or-nothing behavior for Git rejections with source, ignored, and
   spooled ignored output.
2. Bridge OCC trace events, or equivalent per-path rejection events, into
   command trace output.
3. Add operation-level tests for Git rejection plus source output, ignored
   output, and spooled ignored output.
4. Pin lane metadata semantics for route rejection:
   - source lane with paths => `failed` and `publish_failed`;
   - ignored lane with paths => `failed` and `publish_failed`;
   - Git reason remains in `publish_lanes.routing.drop_reason_counts` and
     top-level `conflict_reason`.

### Milestone C: Live E2E Coverage

Add live workspace-runtime-command cases for:

1. `.git/index.lock` as a special file plus source and ignored output.
2. `rm -rf .git` opaque-root rollback.
3. Incomplete operation marker rejection.
4. Ref write rejection.
5. Object rewrite rejection.
6. Unsupported object database path rejection such as
   `.git/objects/info/alternates`.
7. Reflog append acceptance.
8. Reflog truncation/rewrite rejection.
9. Git rejection with spooled ignored output.

Every rejection case must assert:

- command process status is `ok` when the shell exits 0,
- command response `success` is `false`,
- `changed_paths` is empty,
- active manifest version is unchanged,
- source, ignored, and Git paths are not visible after the command,
- `publish_lanes.routing.drop_reason_counts` contains the expected code,
- command trace exposes `command.publish_lanes_decided` and the rejection detail.

### Milestone D: Docs And Closeout

1. Update stale ignored-state spec wording.
2. Update workspace-runtime-command README/index artifacts.
3. Add or update outcome notes with the remediation scope and verification.
4. Run full focused and live verification.

## 5. Required Unit Tests

Add or update `crates/daemon/layerstack/tests/unit/route.rs`:

- command-lane protected drop under `.git/index.lock` rejects whole publish with
  `git_lock_file`.
- command-lane protected drop under `.git/hooks/pre-commit` rejects whole
  publish with `git_hook_write`.
- command-lane unsupported special file under `.git/custom.fifo` rejects whole
  publish with `git_metadata_unsupported`.
- `.git/objects/info/alternates` rejects with `git_metadata_unsupported`.
- `.git/objects/pack/pack-test.pack` rejects with `git_metadata_unsupported`.
- malformed loose-object paths reject with `git_metadata_unsupported`.
- canonical absent loose-object path remains gated.
- canonical existing loose-object path with identical bytes remains gated.
- canonical existing loose-object path with different bytes rejects with
  `git_object_rewrite`.
- reflog append after newline remains gated.
- reflog exact no-op remains gated.
- reflog prefix extension of a non-newline-terminated base rejects with
  `git_reflog_rewrite`.
- added incomplete operation markers reject with `git_incomplete_operation`.

Add or update `crates/daemon/operation/src/command/finalize.rs` tests:

- Git special-file rejection prevents source and ignored lanes.
- Git rejection cleans spooled ignored payloads.
- Git rejection response exposes `conflict_reason` and
  `publish_lanes.routing.drop_reason_counts`.

## 6. Required Live E2E Tests

Add tests in
`crates/e2e-test/tests/workspace-runtime-command/command_error_and_backpressure.rs`
for the Milestone C cases. Prefer the existing `ensure_git_publish_rejected`
helper, but extend it where needed to assert source/ignored/Git readbacks and
trace rejection details.

The `rm -rf .git` case must use a real seeded Git workspace and must assert
that `.git/HEAD` is still readable after the rejected command.

The reflog append case must prove a positive `.git/logs/**` publish is later
visible and still does not route through ordinary ignored/direct LWW even when
`.git/` appears in `.gitignore`.

## 7. Verification Commands

Run after implementation:

```sh
cargo fmt --check
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack command_git
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack route_tests
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation git_
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation command::
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p e2e-test --no-run
cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p e2e-test --bin e2e-runner -- --suites workspace-runtime-command --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4
git diff --check
```

## 8. Acceptance Criteria

The remediation is complete only when:

1. `.git` special files are publish-rejecting under command finalization.
2. Unsupported object database paths no longer publish as object writes.
3. Reflog append checks preserve record boundaries.
4. Known incomplete operation markers use `git_incomplete_operation`.
5. Git rejection remains atomic for source, ignored, spooled ignored, and Git
   metadata paths.
6. Command traces expose structured lane metadata and per-path rejection detail.
7. Live E2E covers root `.git` deletion, every current-floor rejection family,
   and positive reflog append.
8. Ignored-state and E2E documentation no longer describe the superseded
   non-atomic `.git` drop behavior.
