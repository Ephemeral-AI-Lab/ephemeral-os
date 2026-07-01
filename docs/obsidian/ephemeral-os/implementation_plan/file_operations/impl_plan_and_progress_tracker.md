---
title: File Operations Implementation Plan And Progress Tracker
tags:
  - ephemeral-os
  - sandbox
  - runtime
  - file
  - implementation-plan
  - progress
status: draft
updated: 2026-07-02
---

# File Operations Implementation Plan And Progress Tracker

Source docs: [[spec]], [[test-case]], [[acceptance_criteria]]

## Tracker Rules

- Update this tracker at the start and end of every milestone.
- Add a checkpoint before any context switch, risky boundary change, or handoff.
- End each completed milestone with one focused git commit after review,
  cleanup, and acceptance verification.
- Do review, cleanup, and acceptance-criteria verification before marking a
  milestone done.
- Record proof in the tracker: commit hash, tests run, live commands run, and
  any accepted exception.
- Do not start the next milestone with unresolved boundary or safety failures
  from the previous milestone.

Status values: `todo`, `in_progress`, `blocked`, `review`, `done`.

## Progress Tracker

| Milestone | Status | Last updated | Commit / Evidence | Notes |
|---|---|---:|---|---|
| M0 Plan Lock | done | 2026-07-02 | Spec/test/acceptance re-read; codebase mapped | Naming confirmed: protocol field `path`; runner modes `Shell`/`MountOverlay`/`FileOp`. Non-goals held: no `RunnerWait`, no host `upperdir` mutation, no OCC retry, no delete/move/stat/list. Reference constants confirmed from local-os (`DEFAULT_LINE_LIMIT=2000`, `MAX_OUTPUT_BYTES=256 KiB`); spec caps `MAX_EDIT_BYTES=4 MiB`, `MAX_RUNNER_RESULT_BYTES=8 MiB`. Writer lock is reentrant per-thread → `amend_path` holds `exclusive()` and reuses `publish_layer_unlocked`. |
| M1 API And Dispatch | done | 2026-07-02 | `c6bcb7579` — `cargo build`, `cargo test -p sandbox-runtime` (all suites green), `cargo clippy` clean | DTOs, `FileOperationError`+`FileEntryKind`, CLI specs+dispatch+error mapping, `FileService::{read,write,edit}` signatures (collaborators by param). Backends stubbed → M2/M4. `service_graph` catalog test updated for the 3 new ops. |
| M2 Sessionless Layerstack Backend | done | 2026-07-02 | `a5f0cd99d` — build+clippy clean; operation+layerstack suites green; scratch verification (write/read/edit/blame/path/offset/CRLF) passed then removed | `LayerStack::{read_classified,amend_path}` (reentrant exclusive lock, base==head ⇒ no conflict/retry); `read_current_window`+windowing; `amend_path` records blame under audit gate; sessionless read/write/edit publish `operation:<request_id>`. Blame preservation verified (changed line→new owner, unchanged→prior). |
| M3 Namespace Runner Protocol | done | 2026-07-02 | `4c6cc0536` — macOS workspace+tests build; `cargo check --target aarch64-unknown-linux-musl -p sandbox-runtime-namespace-process` green (setns body); clippy clean (incl. Linux target) | Explicit modes `--shell`/`--mount-overlay`/`--file-op` (exactly one); file-op runner with no-follow fd walk; concurrent `result_fd` drain + `MAX_RUNNER_RESULT_BYTES` cap; `RunnerPlacement`; `spawn_file_op`; engine `run_file_op`. Daemon full-Linux link deferred to M6 (transitive `sha2-asm` needs musl C toolchain; daemon M3 code is cross-platform). |
| M4 Session Backend | done | 2026-07-02 | `70f77783e` — macOS workspace+tests build; scratch session read/write/edit/not-found/not-regular via `run_file_op` hook passed then removed; lib clippy clean | `NamespaceRuntime::run_file_op` (peer of mount_overlay); `WorkspaceRuntimeService::run_file_op` (+ `run_file_op` hook for tests); `WorkspaceSessionService::run_file_op` derives session `cgroup.procs`; file `namespace.rs` maps runner outcomes + base64 ReadFile decode; session ops never publish (verified: no blame record). Workspace/daemon Linux link verified in M6 rebuild. |
| M5 Automated Tests | done | 2026-07-02 | `ac035a63c` — `cargo fmt --check` clean; `cargo test -p sandbox-runtime` (file_operations 28 + suites) and `-p sandbox-daemon` (51) green; `cargo clippy --all-targets` clean for file-ops files (2 pre-existing warnings in untouched gateway/isolated_network) | `tests/file_operations.rs` (28 tests): sessionless read/write/edit, path, layerstack helpers, session via `run_file_op` hook, runner placement via engine+fake launcher. Daemon runner CLI test updated for exactly-one-mode (regression from M3 surfaced under `cargo test -p sandbox-daemon`). |
| M6 Live E2E Acceptance | done | 2026-07-02 | `cb0455dd8` — Gateway rebuilt (`xtask package` aarch64-musl OK — Linux-compiles all file-op code); 15/15 smoke cases pass via `sandbox-cli`; transcript in [[live_smoke_evidence]] | Sandbox `eos-ab5d6b74…`, session `00000118be41db9cba4263`. Both backends exercised: sessionless publish+blame (`operation:<request_id>`) and session namespace runner (write/read/edit through the mounted overlay, nested parent-dir creation, invisible to sessionless read = no publish). |

## Milestone Commit Policy

- Make one git commit per completed milestone.
- Commit only after the milestone exit routine passes.
- Stage only files that belong to that milestone; do not include unrelated dirty
  workspace or Obsidian state files.
- Use commit messages like `file-ops: M2 sessionless layerstack backend`.
- Record the commit hash in the progress tracker `Commit / Evidence` column.
- Mid-milestone checkpoints are tracker notes, not commits, unless a handoff
  needs a temporary WIP commit.

## Checkpoint Log

A checkpoint is a smaller evidence snapshot inside a milestone. Use IDs like
`M2.C1`. A checkpoint is useful only if it records what is working, what
remains, and how to resume. It is not a substitute for the milestone commit.

| Checkpoint | Milestone | Status | Evidence | Resume next |
|---|---|---|---|---|
| M0.C1 | Plan Lock | todo | | Spec/test/acceptance alignment confirmed. |
| M1.C1 | API And Dispatch | done | `c6bcb7579`; build+clippy+tests green | DTOs, CLI, dispatch compile and route; `FileEntryKind` lives in the operation `file` module (used immediately by `FileOperationError`); M2 low-level classified read maps into it. |
| M2.C1 | Sessionless Layerstack Backend | done | `a5f0cd99d` | Sessionless read projects the snapshot; offset/limit/BOM/CRLF/offset-past-EOF verified. |
| M2.C2 | Sessionless Layerstack Backend | done | `a5f0cd99d` | Sessionless write/edit publish via `amend_path`; `file_blame` shows `operation:<request_id>` on changed lines, prior owner preserved on unchanged. |
| M3.C1 | Namespace Runner Protocol | done | `4c6cc0536` | `Run`→`Shell`; `--shell`/`--mount-overlay`/`--file-op` require exactly one mode; PTY shell unchanged (18 exec_command tests green). |
| M3.C2 | Namespace Runner Protocol | done | `4c6cc0536` | File-op runner setns body + protocol typecheck on Linux target; launcher drains result_fd concurrently and caps at 8 MiB; engine `run_file_op` returns `RunResult`. |
| M4.C1 | Session Backend | done | `70f77783e` | Session read/write/edit route through `run_file_op` → workspace runtime → namespace runner; verified via the explicit hook (read content, write create-no-publish, edit RMW producing edited bytes, not-found, not-regular). |
| M5.C1 | Automated Tests | done | `ac035a63c` | `cargo fmt`/`build`/`test -p sandbox-runtime`/`clippy --all-targets` all pass; daemon suite green after runner-mode test fix. |
| M6.C1 | Live E2E Acceptance | done | [[live_smoke_evidence]] | Gateway rebuilt via `--rebuild-binary`; 5 read + 5 write + 5 edit smoke cases pass; transcript saved. |

## Milestone Exit Routine

Run this before changing a milestone to `done`:

- [ ] Update the progress tracker row and latest checkpoint row.
- [ ] Review the diff against [[spec]] and remove speculative fields, helpers,
      services, DTOs, or wiring.
- [ ] Cleanup: format, delete dead code, delete debug logs, and keep names aligned
      with repo conventions.
- [ ] Verify the milestone's slice of [[acceptance_criteria]].
- [ ] Stage only milestone files and create the milestone commit.
- [ ] Record the commit hash plus command output or live transcript location in
      `Commit / Evidence`.

## M0 Plan Lock

Goal: freeze implementation boundaries before code.

- [ ] Re-read `CLAUDE.md`, `AGENTS.md`, [[spec]], [[test-case]], and
      [[acceptance_criteria]].
- [ ] Confirm naming: protocol field `path`; file names use existing Rust module
      style; runner modes are `Shell`, `MountOverlay`, `FileOp`.
- [ ] Confirm non-goals remain out: no `RunnerWait`, no host-side `upperdir`
      mutation, no OCC retry loop, no delete/move/stat/list.
- [ ] Update tracker to `review`, run the exit routine, then mark `done`.

## M1 API And Dispatch

Goal: expose the file operations without changing service ownership.

- [ ] Add runtime input/output DTOs for read/write/edit.
- [ ] Add `FileService::{read, write, edit}` as `&self` methods taking
      `LayerStackService` and `WorkspaceSessionService` by parameter.
- [ ] Add CLI definitions and runtime dispatch for `file_read`, `file_write`,
      and `file_edit`.
- [ ] Keep `FileService` audit-only; add no `workspace_root` field and no new
      top-level file-operation service.
- [ ] Add error mapping skeleton shared by both backends.
- [ ] Verify acceptance criteria: API shape, output shape, no extra service
      wiring, naming.
- [ ] Update tracker to `review`, run cleanup, then mark `done`.

## M2 Sessionless Layerstack Backend

Goal: make sessionless file ops work through layerstack only.

- [ ] Add `read_current_window` for active snapshot reads.
- [ ] Add `amend_path` for atomic sessionless write/edit under the existing
      exclusive writer lock.
- [ ] Implement sessionless `file_read`, `file_write`, and `file_edit`.
- [ ] Preserve publish attribution through existing `record_layer_publish` and
      `file_blame`.
- [ ] Cover path classification, whiteouts, directories, symlinks, invalid UTF-8,
      output caps, `MAX_EDIT_BYTES`, and concurrent sessionless writes/edits.
- [ ] Verify acceptance criteria: snapshot-only reads, publish-on-write/edit,
      atomicity, blame, path safety.
- [ ] Update tracker to `review`, run cleanup, then mark `done`.

## M3 Namespace Runner Protocol

Goal: add the minimal runner path needed for live session file ops.

- [ ] Make `sandbox-daemon ns-runner` require exactly one mode:
      `--shell`, `--mount-overlay`, or `--file-op`.
- [ ] Rename module-private `Run` to `Shell`.
- [ ] Keep shell PTY behavior unchanged; `spawn_pty` passes `--shell` and no
      setup timeout.
- [ ] Add file-op request/result protocol with only `ReadWindow`, `ReadFile`,
      and `Write`.
- [ ] Drain `result_fd` before child wait can deadlock and cap envelopes at
      `MAX_RUNNER_RESULT_BYTES`.
- [ ] Keep cgroup placement in `RunnerPlacement`; do not add `RunnerWait`.
- [ ] Verify acceptance criteria: explicit modes, request/result fd behavior,
      result cap, setup timeout, runner placement.
- [ ] Update tracker to `review`, run cleanup, then mark `done`.

## M4 Session Backend

Goal: route session file ops through the live mounted namespace.

- [ ] Add `WorkspaceSessionService::run_file_op`.
- [ ] Resolve the session entry and delegate to namespace runtime file-op launch.
- [ ] Implement session read/write/edit using the runner.
- [ ] Ensure session write/edit do not publish and do not host-write
      `entry.upperdir`.
- [ ] Ensure session reads see live session changes and sessionless reads do not.
- [ ] Verify acceptance criteria: namespace-only session behavior, session
      capture attribution, no host-side overlay writes.
- [ ] Update tracker to `review`, run cleanup, then mark `done`.

## M5 Automated Tests

Goal: make regressions hard to ship.

- [ ] Add `crates/sandbox-runtime/operation/tests/file_operations.rs`.
- [ ] Cover the read, write, edit, path, runner/protocol, and layerstack helper
      cases listed in [[test-case]].
- [ ] Prefer runtime-level tests; add lower-level tests only where runtime tests
      cannot drive the helper directly.
- [ ] Run:

```sh
cargo fmt
cargo build
cargo test -p sandbox-runtime
cargo clippy --all-targets
```

- [ ] Verify acceptance criteria: automated coverage and clean local checks.
- [ ] Update tracker to `review`, record command output, then mark `done`.

## M6 Live E2E Acceptance

Goal: prove the feature works in the real sandbox path with a smoke run only.

- [ ] Rebuild the Docker sandbox gateway binary:

```sh
bin/start-sandbox-docker-gateway --rebuild-binary
```

- [ ] Use `sandbox-cli` for all manual sandbox operations.
- [ ] Run only the live smoke matrix in [[test-case#Live Smoke Checklist]]:
      five `file_read` cases, five `file_write` cases, and five `file_edit`
      cases.
- [ ] Do not run full live negative, concurrency, runner, or path matrices in
      this milestone.
- [ ] Verify the live-smoke items in [[acceptance_criteria]] are checked or have
      a recorded exception.
- [ ] Update tracker to `review`, attach transcript/log evidence, then mark
      `done`.
