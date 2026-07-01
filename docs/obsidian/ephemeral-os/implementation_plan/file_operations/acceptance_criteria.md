---
title: File Operations Acceptance Criteria
tags:
  - ephemeral-os
  - sandbox
  - runtime
  - file
  - acceptance
status: draft
updated: 2026-07-02
---

# File Operations Acceptance Criteria

Source docs: [[spec]], [[test-case]]

## 1. Spec Implementation

- [ ] `file_read`, `file_write`, and `file_edit` are exposed through the sandbox
      file domain with `path` and optional `workspace_session_id`; `file_edit`
      accepts ordered `edits`.
- [ ] Runtime outputs match the spec fields exactly: read window metadata,
      write `create`/`update`, and edit replacement counts. No host-only mtime
      fields are added.
- [ ] `FileService` stays audit-only. It does not gain `workspace_root`, own
      `LayerStackService`, own `WorkspaceSessionService`, or introduce a new
      top-level file-operation service.
- [ ] Sessionless read uses the active layerstack snapshot only. It does not
      mount, fork, enter a namespace, publish, or read the detached host
      workspace.
- [ ] Sessionless write/edit use `LayerStackService::amend_path` under the
      existing exclusive writer lock, publish one complete layer, and attribute
      blame to `operation:<request_id>`.
- [ ] Session read/write/edit go through
      `WorkspaceSessionService::run_file_op` and the live mounted namespace.
      They never mutate `entry.upperdir` from the host and never publish until
      session capture.
- [ ] Session capture attributes session file-op changes to
      `workspace_session:<id>` and preserves existing `file_blame` publish
      behavior.
- [ ] Path handling rejects empty paths, NUL, `..`, absolute paths outside the
      workspace root, directories, special files, final symlinks, and symlink
      parent traversal on both backends.
- [ ] Read semantics match the spec: `limit` defaults to 2000, `limit` is
      `1..=2000`, `offset <= 1` starts at line 1, BOM is stripped, CRLF/CR are
      normalized, invalid UTF-8 is rejected, and only selected output is capped.
- [ ] Write semantics are atomic: missing parents are created, existing regular
      file mode is preserved on update, final rename is complete, and failures
      leave no partial target or durable temp file.
- [ ] Edit semantics match local-os behavior: empty edits and no-op edits are
      rejected, each `old_string` must exist and be unique unless
      `replace_all=true`, edits apply in order, and `MAX_EDIT_BYTES` is enforced.
- [ ] The namespace runner supports only `ReadWindow`, `ReadFile`, and `Write`
      for this pass.
- [ ] `sandbox-daemon ns-runner` requires exactly one explicit mode:
      `--shell`, `--mount-overlay`, or `--file-op`. No mode and multiple modes
      are errors.
- [ ] `spawn_pty` passes `--shell` with existing PTY/cgroup/cancel behavior and
      no setup timeout. Mount-overlay and file-op launches pass request/result
      fds, drain result output before waiting, cap results at
      `MAX_RUNNER_RESULT_BYTES`, and use the setup timeout.
- [ ] No `RunnerWait` enum, OCC retry loop, host-side overlay merge, delete,
      move, stat, list, binary-file policy, or symlink-following policy is added
      in this pass.
- [ ] `cargo fmt`, `cargo build`, `cargo test -p sandbox-runtime`, and
      `cargo clippy --all-targets` pass.

## 2. Test Case Live E2E

- [ ] Automated coverage exists for the must-hold contract in [[test-case]]:
      sessionless snapshot behavior, session namespace behavior, no detached
      host workspace reads/writes, no direct `entry.upperdir` mutation, and
      protocol field `path`.
- [ ] Automated read tests cover sessionless and session reads, missing files,
      large-window reads, output caps, default/invalid `limit`, `offset`, BOM,
      CRLF/CR, invalid UTF-8, directories, symlinks, symlink parents, and special
      files where fixtures can create them.
- [ ] Automated write tests cover create, update, blame, identical content,
      parent creation, invalid parents/targets, concurrent sessionless writes,
      injected publish/write failures, mode preservation, and temp cleanup.
- [ ] Automated edit tests cover empty edits, no-op edits, not found, not
      unique, `replace_all`, ordered edits, dependent edits, removed targets,
      line-ending normalization, oversized edit files, invalid UTF-8,
      concurrent sessionless writes/edits, and failed publishes.
- [ ] Automated runner/protocol tests cover explicit mode parsing, no-mode and
      multiple-mode rejection, request/result fd draining before wait, result
      byte caps, setup timeout behavior, cgroup placement, and error mapping.
- [ ] Session file-op tests use the real live runner or an explicit file-op hook
      that preserves namespace semantics. They do not pass by bypassing the
      namespace path.
- [ ] Before live sandbox checks, rebuild the Docker sandbox gateway binary:

```sh
bin/start-sandbox-docker-gateway --rebuild-binary
```

- [ ] Live e2e uses `sandbox-cli` for manual sandbox operations.
- [ ] Live e2e is smoke-only for this pass: run the 15 cases in
      [[test-case#Live Smoke Checklist]], five each for `file_read`,
      `file_write`, and `file_edit`.
- [ ] The live smoke transcript proves both backends are exercised:
      sessionless layerstack publish/read and session namespace read/write/edit.
- [ ] Do not run the full negative, concurrency, runner, or path matrix as live
      e2e in this pass; those belong to automated tests unless promoted later.
- [ ] Final acceptance evidence includes the automated test command output and
      the live smoke command transcript or saved log.
