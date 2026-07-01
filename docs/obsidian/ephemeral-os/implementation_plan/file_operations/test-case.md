---
title: File Operation Test Cases
tags:
  - ephemeral-os
  - sandbox
  - runtime
  - file
  - testing
status: draft
updated: 2026-07-02
---

# File Operation Test Cases

These are the required tests for `file_read`, `file_write`, and `file_edit`.
Primary coverage belongs in
`crates/sandbox-runtime/operation/tests/file_operations.rs`; lower-level unit
tests are only for helpers that are awkward to drive through the runtime API.

## Must-Hold Contract

- [ ] Sessionless `file_read` reads the latest published layerstack snapshot.
- [ ] Sessionless `file_write` and `file_edit` publish through `amend_path` under
      the layerstack writer lock.
- [ ] Session `file_read`, `file_write`, and `file_edit` operate through the
      live session namespace and mounted workspace.
- [ ] Session writes/edits do not publish immediately and are attributed only
      when the session is captured.
- [ ] File operations never read from or write to the detached host workspace as
      the source of truth.
- [ ] Session writes/edits never mutate `entry.upperdir` directly from the host.
- [ ] The sandbox protocol field is `path`; CLI `--path` maps directly to it.

## Read Cases

### Sessionless Read

- [ ] Reads a file that exists in the current snapshot.
      Expected: returns `content`, `start_line`, `num_lines`, `total_lines`,
      `bytes_read`, `total_bytes`, `next_offset`, and `truncated`.
- [ ] Missing file.
      Expected: `not_found`, not empty content.
- [ ] Reads after a sessionless write.
      Expected: new published content is visible.
- [ ] Does not see uncommitted changes in a live session.
      Expected: `not_found` or previous snapshot content.
- [ ] Reads a large file with a small `offset`/`limit` window.
      Expected: succeeds if the selected window is within output limits; it
      must not fail only because the whole file is large.
- [ ] Selected read output exceeds the max response bytes.
      Expected: invalid request / `OutputTooLarge`, not `FileTooLarge`.
- [ ] `limit` omitted.
      Expected: default is 2000 lines.
- [ ] `limit = 0`.
      Expected: invalid request.
- [ ] `limit > 2000`.
      Expected: invalid request.
- [ ] `offset <= 1`.
      Expected: starts at line 1.
- [ ] `offset` past EOF.
      Expected: empty content window with correct totals, not `not_found`.
- [ ] UTF-8 with leading BOM.
      Expected: BOM is removed before windowing.
- [ ] CRLF and CR-only line endings.
      Expected: normalized to `\n` before windowing.
- [ ] Invalid UTF-8 bytes.
      Expected: invalid request / not UTF-8.
- [ ] Directory path.
      Expected: invalid request / not regular.
- [ ] Symlink path.
      Expected: invalid request / not regular; symlink is not followed.
- [ ] Symlink parent path.
      Expected: invalid request; no parent symlink traversal.
- [ ] Special file path, where test fixture can create one.
      Expected: invalid request / not regular.

### Session Read

- [ ] Reads a file created by an in-session shell command.
      Expected: namespace read sees the live overlay content.
- [ ] Reads a file created by session `file_write`.
      Expected: content is visible in the same session.
- [ ] Reads while a command is still alive in the same session.
      Expected: sees changes made through the mounted namespace.
- [ ] Missing file in the session overlay.
      Expected: `not_found`.
- [ ] Large session read with a small `offset`/`limit` window.
      Expected: runner returns only the requested window; it does not transfer
      the full file to the operation layer.
- [ ] BOM, CRLF/CR, `offset`, and `limit`.
      Expected: same shaping as sessionless read.
- [ ] Directory, symlink, symlink parent, and special file in the live overlay.
      Expected: invalid request / not regular.

## Write Cases

### Sessionless Write

- [ ] Create a new file.
      Expected: returns `type = create`; subsequent sessionless read sees it.
- [ ] Update an existing file.
      Expected: returns `type = update`; subsequent sessionless read sees it.
- [ ] Blame after create/update.
      Expected: owner is `operation:<request_id>` for changed lines.
- [ ] Identical-content write.
      Expected: final content is unchanged; blame behavior is explicit and stable.
- [ ] Missing parent directories.
      Expected: parents are created for the new file.
- [ ] Existing parent is a file.
      Expected: invalid request.
- [ ] Existing parent is a symlink.
      Expected: invalid request; no symlink traversal.
- [ ] Final target is a directory.
      Expected: invalid request / not regular.
- [ ] Final target is a symlink.
      Expected: invalid request / not regular; symlink is not followed.
- [ ] Final target is a special file.
      Expected: invalid request / not regular.
- [ ] Concurrent sessionless writes to one path.
      Expected: operations serialize under `amend_path`; final content is one
      complete write and no partial/stale publish is observed.
- [ ] Partial write failure injected before publish.
      Expected: no new layer is committed.

### Session Write

- [ ] Create a new file through `workspace_session_id`.
      Expected: visible inside the session and not visible in sessionless read.
- [ ] Update an existing session file.
      Expected: visible inside the session.
- [ ] Capture the session after a session write.
      Expected: later blame attribution is `workspace_session:<id>`.
- [ ] Write while a command is still alive in the same session.
      Expected: command can observe the new content through the mounted overlay.
- [ ] Verify storage target.
      Expected: write goes through `WorkspaceSessionService::run_file_op` and
      the namespace runner against the mounted workspace, not by host-side
      mutation of `entry.upperdir`.
- [ ] Missing parent directories.
      Expected: parents are created through the mounted overlay.
- [ ] Existing parent is a file.
      Expected: invalid request.
- [ ] Existing parent is a symlink.
      Expected: invalid request; no escape through symlink parents.
- [ ] Final target is a symlink, directory, or special file.
      Expected: invalid request / not regular.
- [ ] Update preserves regular-file mode.
      Expected: existing executable/readable mode is preserved.
- [ ] Simulated write failure before rename.
      Expected: no partially written target file.
- [ ] Temp file cleanup.
      Expected: no durable temp artifacts after success or expected failure.

## Edit Cases

### Sessionless Edit

- [ ] Empty `edits` array.
      Expected: invalid request / no edits.
- [ ] `old_string == new_string`.
      Expected: invalid request / no changes.
- [ ] `old_string` not found.
      Expected: invalid request / edit not found.
- [ ] `old_string` appears more than once and `replace_all` is false or absent.
      Expected: invalid request / edit not unique.
- [ ] `old_string` appears more than once and `replace_all` is true.
      Expected: all occurrences are replaced and replacement count is correct.
- [ ] Multiple edit entries.
      Expected: applied in array order against the evolving content.
- [ ] Later edit depends on an earlier edit.
      Expected: succeeds if the earlier edit creates the later target.
- [ ] Earlier edit removes a later target.
      Expected: later edit reports not found.
- [ ] Edit preserves normalized line-ending semantics.
      Expected: matching follows local-os normalization rules.
- [ ] Edit target exceeds `MAX_EDIT_BYTES`.
      Expected: invalid request / `FileTooLarge` before loading the whole file.
- [ ] Edit target contains invalid UTF-8.
      Expected: invalid request / not UTF-8.
- [ ] Concurrent sessionless edits/writes to one path.
      Expected: operations serialize under `amend_path`; edit applies to the
      current head while the lock is held, with no OCC retry loop.
- [ ] Partial publish failure.
      Expected: no new layer is committed.

### Session Edit

- [ ] Edit a file created in the session.
      Expected: read-modify-write happens against the live overlay.
- [ ] Edit a file modified by an in-session shell command.
      Expected: sees current namespace content, not the snapshot version.
- [ ] Sessionless read after session edit.
      Expected: does not see the uncommitted session edit.
- [ ] Capture after session edit.
      Expected: captured layer contains edited content and blame owner is
      `workspace_session:<id>`.
- [ ] Same replacement semantics as sessionless edit.
      Expected: no edits, no changes, not found, not unique, and `replace_all`
      all behave the same.
- [ ] Concurrent shell write races with session edit.
      Expected: documented last-writer-wins behavior; the final rename is atomic
      and no partial file is observed.
- [ ] Symlink parent or symlink target.
      Expected: invalid request; no symlink traversal.

## Path Cases

- [ ] Runtime request uses `path`, not `file_path`.
      Expected: direct sandbox requests accept `path`; any local-os adapter
      translation happens before the runtime call.
- [ ] Repo-relative path such as `src/file.txt`.
      Expected: resolves to the same layer path on both backends.
- [ ] Absolute path under workspace root.
      Expected: strips the workspace root and resolves to the same layer path as
      the repo-relative form.
- [ ] Absolute path outside workspace root.
      Expected: invalid path.
- [ ] Empty path.
      Expected: invalid path.
- [ ] Path containing NUL.
      Expected: invalid path.
- [ ] Path containing `..`.
      Expected: invalid path.
- [ ] Path with `.` components.
      Expected: normalization matches `LayerPath` behavior.
- [ ] Existing parent path is a whiteout or opaque directory case in layerstack.
      Expected: sessionless read/write/edit classify the merged view correctly.
- [ ] Parent path is hidden by a whiteout.
      Expected: treated as absent or invalid according to the merged manifest,
      never resolved to a lower-layer object.

## Runner And Protocol Cases

- [ ] Namespace runner mode.
      Expected: `--shell`, `--mount-overlay`, and `--file-op` are the only valid
      modes; no mode and multiple modes are invalid.
- [ ] Shell runner launch.
      Expected: `spawn_pty` passes `--shell`, keeps existing PTY/cgroup/cancel
      behavior, and does not apply a setup timeout.
- [ ] Request/result runner launch.
      Expected: mount-overlay and file-op pass request/result fds, start
      draining result output before waiting on the child, cap result bytes, and
      use the setup timeout.
- [ ] Session operation boundary.
      Expected: file service calls `WorkspaceSessionService::run_file_op`; it
      does not construct namespace entries or call namespace execution directly.
- [ ] Runner error mapping.
      Expected: missing paths map to `not_found`; invalid paths, UTF-8 errors,
      and not-regular files map to `invalid_request`; internal launch/I/O errors
      map to `operation_failed`.
- [ ] Runner body performs read/windowing inside the namespace.
      Expected: large session reads do not transfer the full file just to return
      a small line window.
- [ ] Hook/test backend support.
      Expected: session file-op tests either run against the real live runner or
      have an explicit file-op hook; tests must not pass by bypassing namespace
      semantics.

## Layerstack Helper Cases

- [ ] `read_current_window` classifies absent, file, directory, symlink, special
      file, invalid UTF-8, and selected-output-too-large cases.
- [ ] `read_current_window` does not reject a regular file solely because the
      whole file is larger than the output cap.
- [ ] `amend_path` write with `max_bytes = 0`.
      Expected: classifies existing target without loading large existing bytes.
- [ ] `amend_path` edit with `MAX_EDIT_BYTES`.
      Expected: rejects oversized edit input before transform.
- [ ] `amend_path` transform error.
      Expected: no commit and no blame record.
- [ ] `amend_path` commit success.
      Expected: `record_layer_publish` runs and `file_blame` shows the new owner.

## Live Smoke Checklist

AGENTS.md requires rebuilding the Docker sandbox gateway binary before live
sandbox checks:

```sh
bin/start-sandbox-docker-gateway --rebuild-binary
```

Use `sandbox-cli` for manual sandbox operations.

Live e2e is smoke-only for this pass: five cases per file operation. Do not run
the full automated matrix manually.

### Read Smoke

- [ ] Sessionless read of a file created by sessionless `file_write`.
- [ ] Session read of a file created by session `file_write`.
- [ ] Sessionless read with `offset` and `limit` over a multi-line file.
- [ ] Sessionless read of a missing file returns `not_found`.
- [ ] Sessionless read rejects an absolute path outside the workspace root.

### Write Smoke

- [ ] Sessionless write creates a new file and sessionless read sees it.
- [ ] Sessionless write updates an existing file and `file_blame` shows
      `operation:<request_id>`.
- [ ] Session write is visible with `workspace_session_id` and invisible to
      sessionless read before capture.
- [ ] Session write creates missing parent directories.
- [ ] Write to an existing directory is rejected.

### Edit Smoke

- [ ] Sessionless edit performs one unique replacement and sessionless read sees
      the result.
- [ ] Sessionless edit with `replace_all=true` replaces multiple occurrences.
- [ ] Sessionless edit with missing `old_string` returns edit-not-found.
- [ ] Session edit is visible with `workspace_session_id` and invisible to
      sessionless read before capture.
- [ ] Ordered multi-edit applies against evolving content.

### Session-Only Cases (Linux; not unit-testable on darwin)

These run through the Linux-gated setns file-op body behind the live namespace
runner, so the darwin unit harness cannot exercise them (the canned `run_file_op`
hook proves the operation-layer wiring, not the in-namespace rejection). They are
proven only here; record a transcript for each.

- [ ] Session write updates an existing executable file and preserves its mode.
- [ ] Session write to an in-session directory is rejected as invalid request /
      not regular.
- [ ] Session write to an in-session symlink is rejected as invalid request /
      not regular; the symlink is not followed.
- [ ] Session write to an in-session symlink parent is rejected as invalid
      request; no symlink-parent traversal.
- [ ] Session edit to an in-session symlink or symlink parent is rejected as
      invalid request; no symlink traversal.
