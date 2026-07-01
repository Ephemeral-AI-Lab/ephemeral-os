---
title: Runtime File Operations — Review Remediation Spec
tags:
  - ephemeral-os
  - sandbox
  - runtime
  - file
  - review
  - remediation
  - implementation-plan
status: implementation_plan
updated: 2026-07-02
---

# Runtime File Operations — Review Remediation Spec

Remediation plan for the multi-agent code review of the `read` / `write` /
`edit` runtime file operations (diff `git diff fbc6b15ba..HEAD -- crates/`,
commits `file-ops: M1..M6`).

Source docs: [[spec]], [[acceptance_criteria]], [[test-case]],
[[live_smoke_evidence]].

## Review verdict

The shipped implementation is correct and constraint-compliant. All nine hard
constraints hold; the setns no-follow walk is containment-sound and its atomic
write is leak-free; `amend_path`'s no-retry atomicity, deadlock-freedom, and
blame attribution are correct; the non-PTY launcher drains before waiting and
caps the envelope; windowing matches the local-os reference on both backends.

**The only merge blocker is test coverage:** five cases named explicitly by the
spec's Verification list and [[acceptance_criteria]] §2 have **no** enforcing
test.

| Dimension | Verdict | Blocks merge? |
|---|---|---|
| Implementation simplicity | minor | no |
| Correctness | minor | no |
| Completeness | must-fix | **yes** (P0 below) |

No high-severity defect, security escape, deadlock, or boundary violation was
found. The correctness/simplicity findings are LOW/MED hardening or a single
spec-vs-reference divergence to decide (P1).

## Priority overview

| Priority | Item | Blocks merge? |
|---|---|---|
| **P0** | Five missing required tests + promote live-only session cases | **yes** |
| **P1** | Decide `apply_edits` line-ending symmetry (align to `edit.ts` or note divergence) | no (decision) |
| **P2** | Low-cost cleanups: dedupe windowing, dead `RunnerPlacement::cgroup`, setns hardening | no |

---

## P0 — Close the required-but-untested cases (merge blocker)

Requirement source: [[acceptance_criteria]] §2 and [[spec]] "Verification". All
but P0-6 are sessionless / off-namespace tests that fit the existing harness.

Dispatch-level tests use the public seam
`sandbox_runtime::dispatch_operation(&operations, &request)` (see
`crates/sandbox-runtime/operation/tests/workspace_session.rs:246`); reuse the
operations-graph builder in
`crates/sandbox-runtime/operation/tests/observability_trace.rs:366`.

### P0-1 — `limit` range validation (0 / >2000 ⇒ `invalid_request`)

- **Why:** the check exists at `cli_definition/file_operations.rs:278`
  (`limit must be between 1 and 2000`) but every integration test constructs
  `ReadInput` directly and bypasses `parse_read_input`, so it is never
  exercised.
- **Where:** `crates/sandbox-runtime/operation/tests/file_operations.rs`.
- **How:** build a `file_read` `Request` with `args: { path, limit: 0 }`, then
  `limit: 2001`, dispatch through `dispatch_operation`, assert the `Response`
  kind is `invalid_request`.
- **Done when:** both bounds return `invalid_request` via the dispatch path.

### P0-2 — Parent-symlink rejection (sessionless, no traversal)

- **Why:** only a *final*-symlink test exists
  (`file_operations.rs:303 sessionless_read_symlink_is_not_regular_not_followed`).
  The symlink-*parent* path is safe but untested — `lookup_blocked_by_layer`
  (`layerstack/src/stack/projection/mod.rs:229-231`) classifies a symlink
  ancestor as blocking and returns `Absent` before the `symlink_metadata` join
  at `:133` can follow it.
- **Where:** `crates/sandbox-runtime/operation/tests/file_operations.rs`, in
  `env()` (`:61`) and a new test.
- **How:** add `std::os::unix::fs::symlink("sub", workspace.join("linkdir"))`
  next to the existing `link.txt` (`:76`). Assert:
  - `env.read(read_of("linkdir/nested.txt"))` ⇒ `NotFound`;
  - `env.layerstack.read_current_window(&layer_path("linkdir/nested.txt"), 1, 10, cap)`
    ⇒ `ManifestReadWindow::Absent`;
  - a `write` to `linkdir/new.txt` does not resolve through the link.
- **Note / decision:** a symlinked parent yields `not_found` (via `Absent`),
  which is **safe** (no traversal) but differs from the [[test-case]] wording
  "invalid request". Pin the actual behavior in the test and flag the wording
  mismatch for a product call — do **not** silently change the wording.
- **Done when:** the test proves no parent-symlink traversal on the sessionless
  backend.

### P0-3 — Whiteout / opaque-dir parent classification (sessionless)

- **Why:** the new read path `MergedView::read_classified`
  (`projection/mod.rs:118`) and its `is_whiteouted` / `lookup_blocked_by_layer`
  helpers have no test; existing whiteout tests are publish/capture only. This
  is a named spec Open Risk ("never resolved to a lower-layer object").
- **Where:** `crates/sandbox-runtime/layerstack/tests/` — put it where
  multi-layer whiteout fixtures already exist (`tests/unit/publish.rs`,
  `tests/overlay_capture.rs`), since a single-base fixture cannot express a
  whiteout.
- **How:** build a stack with a lower layer containing `dir/f.txt` and an upper
  layer whiteout of `dir`; assert `read_classified("dir/f.txt")` ⇒ `Absent`
  (never the lower-layer object). Add an opaque-directory variant.
- **Done when:** whiteout and opaque parents classify as absent/blocked, not as
  the lower-layer entry.

### P0-4 — Runner envelope over `MAX_RUNNER_RESULT_BYTES` (8 MiB) fails

- **Why:** the over-cap branch in `drain_result_fd`
  (`namespace-execution/src/launcher.rs:300-313`) is never hit — every launcher
  test `drop(result_write)` at EOF.
- **Where:** `crates/sandbox-runtime/namespace-execution/tests/launcher.rs`, in
  the existing `mod tests` (next to
  `overlay_mount_completion_timeout_terminates_and_reaps_child`).
- **How:** `result_pipe()`; spawn a thread that writes `> MAX_RUNNER_RESULT_BYTES`
  into `result_write` then drops it; spawn `sh -c true`; drive
  `ForkRunnerChild { mode_flag: Some("--file-op"), setup_timeout_s, .. }
  .wait_completion()` and assert the cap error. This confirms the drainer keeps
  reading past the cap (so the child never blocks) and then errors.
- **Done when:** an oversized envelope surfaces as an explicit error, not a hang
  or a silent truncation.

### P0-5 — Edit target over `MAX_EDIT_BYTES` (4 MiB) ⇒ `FileTooLarge` before load

- **Why:** the operation-level edit path
  (`operation/src/file/service/impls/edit.rs:116` ⇒ `read_classified` `TooLarge`
  at `projection/mod.rs:144`) is untested; only the generic
  `read_bytes_limited` mechanism is covered at `layerstack/tests/stack.rs`.
- **Where:** `crates/sandbox-runtime/operation/tests/file_operations.rs`.
- **How:** in `env()` add
  `write_fixture(&workspace.join("huge.txt"), &vec![b'x'; 4 * 1024 * 1024 + 1])`;
  new test:
  `env.edit(edit_of("huge.txt", vec![edit_op("x","y",false)], "r"))` ⇒
  `FileOperationError::FileTooLarge { .. }`.
- **Done when:** an oversized edit target is rejected before its bytes are
  loaded.

### P0-6 — Promote live-only session cases to the smoke checklist

- **Why:** session mode-preservation on update and in-session
  directory/symlink/special-file rejection run through the Linux-gated setns body
  behind the canned `run_file_op` hook, so they are **not** automatable on the
  darwin dev host — and the M6 smoke ([[live_smoke_evidence]]) did not exercise
  them, so they are proven nowhere.
- **Where:** [[test-case]] → "Live Smoke Checklist".
- **How:** add, under Write/Edit smoke:
  - session write updates an existing executable file and preserves its mode;
  - session write/edit to an in-session directory, symlink, and symlink-parent is
    rejected as invalid request.
- **Done when:** the live checklist covers these and a transcript records them.

---

## P1 — Decision: `apply_edits` line-ending symmetry

`crates/sandbox-runtime/operation/src/file/service/support.rs:74` gates
`old == new` on **line-ending-normalized** strings, per edit. The local-os
reference `edit.ts:67` gates the **raw** strings in a pre-pass and only checks
normalized equality at the batch level (`content === original`). A batch mixing
one line-ending-only edit with a real edit is therefore **rejected** here but
**accepted** by `edit.ts`.

This faithfully implements [[spec]] pseudocode (lines 511-513), so it is a
product decision, not a defect:

- **Option A (recommended — honor the symmetry contract):** move the
  `old == new` and `old.is_empty()` checks onto the **raw** strings and run them
  in `edit()` *before* the backend read (both session and sessionless branches),
  then rely on the existing `current == original` net-no-op check. This also
  fixes the LOW error-kind-ordering divergence (a missing/non-regular/oversized
  file plus a malformed edit currently returns `not_found` / `NotRegular` /
  `FileTooLarge` instead of `edit.ts`'s `invalid_request`). ~15 lines across
  `support.rs` + `impls/edit.rs`.
- **Option B:** keep as-is and add a one-line note to [[spec]] recording the
  intentional divergence from `edit.ts`.

Pick one so the behavior is a conscious choice. Not a merge blocker.

---

## P2 — Optional low-cost cleanups (non-blocking)

- **Dedupe windowing (MED simplicity).** `TextWindow` + `window_text` /
  `normalize_text` / `split_lines` are byte-identical in
  `operation/src/layerstack/service/impls/read.rs:79-137` and
  `namespace-process/src/runner/setns/file_op.rs:340-393`, and must stay in
  lockstep for read symmetry. Keep one `pub` copy in `namespace-process`,
  re-export via the workspace crate (already the path for the file-op types),
  and have `read_current_window` import it. No boundary violation.
- **`read_classified` walk dedup (MED simplicity).**
  `projection/mod.rs:118` re-walks the whiteout / opaque / blocked-by-layer loop
  that `read_entry_limited` (`:72`) already owns; only terminal byte policy
  differs. Factor the shared walk into one private helper.
- **Dead `RunnerPlacement::cgroup()` (LOW).** The constructor
  (`namespace-execution/src/launcher.rs:43`) has zero call sites — both cgroup
  placements use the struct literal `RunnerPlacement { cgroup_procs_path }`
  (`engine.rs:112,174`); `Default` is also unused. Switch the two sites to
  `RunnerPlacement::cgroup(cgroup_procs_path)` and drop the `Default` derive.
- **setns hardening (LOW, defense-in-depth).**
  - Mask preserved mode `0o0777` instead of `0o7777`
    (`namespace-process/src/runner/setns/file_op.rs:118`) to avoid re-stamping
    setuid/setgid across a temp+rename write.
  - `fstat` the read-final fd + `require_regular` before reading (`:240`) to
    close the classify→open TOCTOU race-free rather than relying on the pre-open
    `statat`.
  - Propagate the parent-dir `fsync` error (`:152`, currently
    `let _ = fsync(parent);`).
- **`read_current_window` dead `TooLarge` arm (LOW).** `read.rs:32` passes
  `usize::MAX`, so the `ManifestFileRead::TooLarge ⇒ OutputTooLarge` arm at
  `:42` is unreachable (sound on 64-bit; mislabels on a hypothetical 32-bit
  build). Map it to an internal/`unreachable` error or feed the real cap.

---

## Remediation checklist

P0 (merge blocker):

- [x] P0-1 `limit` 0 / >2000 dispatch test ⇒ `invalid_request`
      (`operation/tests/file_operations.rs::dispatch_file_read_limit_out_of_range_is_invalid_request`)
- [x] P0-2 sessionless parent-symlink test (no traversal; note `not_found`)
      (`file_operations.rs::sessionless_read_symlink_parent_is_not_followed`)
- [x] P0-3 sessionless whiteout / opaque-dir parent classification test
      (`layerstack/tests/stack.rs::read_classified_{parent_whiteout,opaque_parent}_never_resolves_lower_layer`)
- [x] P0-4 runner envelope over `MAX_RUNNER_RESULT_BYTES` fails
      (`namespace-execution/tests/launcher.rs::file_op_result_over_cap_surfaces_as_error`)
- [x] P0-5 edit target over `MAX_EDIT_BYTES` ⇒ `FileTooLarge` before load
      (`file_operations.rs::sessionless_edit_over_max_edit_bytes_is_file_too_large`)
- [x] P0-6 promote session mode-preservation + not-regular rejection to live smoke
      ([[test-case#Session-Only Cases (Linux; not unit-testable on darwin)]])

P1 (decision):

- [ ] `apply_edits` symmetry — Option A (align to `edit.ts`) or Option B (spec note)

P2 (optional):

- [ ] Dedupe windowing helper across crates
- [ ] Factor shared `read_classified` / `read_entry_limited` walk
- [ ] Remove dead `RunnerPlacement::cgroup()` + `Default`
- [ ] setns: `0o0777` mode mask, post-open `fstat`, propagate parent `fsync`
- [ ] `read_current_window` dead `TooLarge` arm

Nice-to-have coverage (non-blocking):

- [ ] Identical-content sessionless write — blame stability (unchanged owner)
- [ ] Edit where an earlier edit removes a later target ⇒ later reports not found

## Coverage-gap → test mapping

| Required case | New test location | P0 |
|---|---|---|
| `limit` 0 / >2000 ⇒ invalid_request | `operation/tests/file_operations.rs` (dispatch) | P0-1 |
| Parent-symlink rejection (sessionless) | `operation/tests/file_operations.rs` | P0-2 |
| Whiteout / opaque parent classification | `layerstack/tests/` (multi-layer) | P0-3 |
| Envelope > `MAX_RUNNER_RESULT_BYTES` | `namespace-execution/tests/launcher.rs` | P0-4 |
| Edit target > `MAX_EDIT_BYTES` | `operation/tests/file_operations.rs` | P0-5 |
| Session mode-preservation / not-regular | [[test-case]] Live Smoke Checklist | P0-6 |

## Verification

```sh
export PATH="$PWD/bin:$PATH"
cargo fmt
cargo test -p sandbox-runtime      # file_operations.rs + launcher.rs + layerstack tests
cargo test -p sandbox-daemon
cargo clippy --all-targets
```

Live smoke (P0-6), per [[test-case#Live Smoke Checklist]]:

```sh
bin/start-sandbox-docker-gateway --rebuild-binary
# run the smoke cases with sandbox-cli; save the transcript with milestone evidence
```

## Definition of done

- All P0 checklist items land with passing automated tests (P0-1..P0-5) and the
  live checklist updated (P0-6); [[acceptance_criteria]] §2 is satisfied.
- P1 is decided and recorded (code change or spec note).
- `cargo fmt`, `cargo build`, `cargo test -p sandbox-runtime`,
  `cargo test -p sandbox-daemon`, and `cargo clippy --all-targets` pass.
- P2 items are either applied or consciously deferred with a note.
