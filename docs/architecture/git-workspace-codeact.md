# Git Workspace CodeAct Audit Design

Status: implemented

## Problem

`daytona_codeact` previously reached live workspace mutation through
`CodeIntelligenceService.cmd(...)` and the overlay auditor.

The overlay path protects the live checkout with a single OCC boundary, but it
does too much work around the user command:

1. Probe overlayfs / user namespace support.
2. Materialize or refresh a lowerdir snapshot of the live workspace.
3. Run the command under an overlay-mounted view.
4. Package the overlay upperdir as a tarball.
5. Download that tarball through the sandbox process transport.
6. Read baseline contents per changed path.
7. Convert upperdir entries into `OperationChange(strict_base=True)` objects.
8. Submit the batch to the OCC coordinator.

For long CodeAct operations, this adds network bandwidth, extra remote file I/O,
and overlay-specific failure modes that are unrelated to the command being run.
Overlay support is also brittle across sandbox kernels and mount settings.

## Decision

The overlay auditor has been replaced with a Git workspace auditor.

For each `CodeIntelligenceService.cmd(...)` operation:

1. Lease exactly one isolated Git workspace slot.
2. Prepare that slot so its baseline commit represents the current live
   workspace state.
3. Run the CodeAct command inside the leased workspace.
4. Stage the slot and collect `git diff` plus a changed-path manifest.
5. Send strict-base changes to OCC.
6. Apply to the live workspace only through the OCC gate.
7. Reset and return the slot to the pool, or destroy it if reset fails.

The Git workspace is an audit and isolation mechanism, not the authority for
live mutation. OCC remains the only component allowed to write the live
workspace after CodeAct finishes.

## Goals

- Remove overlay upperdir tar transport from CodeAct mutation auditing.
- Avoid lowerdir refreshes and per-path lowerdir reads over the process
  transport.
- Preserve one atomic OCC boundary for all files changed by one CodeAct call.
- Keep long-running CodeAct commands isolated from the live workspace.
- Support one isolated workspace per `service.cmd` operation.
- Keep a prewarmed per-sandbox pool so CodeAct does not pay full workspace
  creation cost on every call.
- Preserve current `changed_paths`, `ambient_changed_paths`, stdout/stderr, and
  exit-code behavior at the `daytona_codeact` tool boundary.
- Preserve current conflict semantics: a peer edit to a changed path aborts the
  whole CodeAct commit with `aborted_version`.
- Keep configuration simple: one public knob,
  `CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX`.

## Non-Goals

- This does not replace typed OCC tools such as `daytona_write_file`,
  `daytona_edit_file`, `daytona_delete_file`, `daytona_move_file`, or
  `daytona_rename_symbol`.
- This is not a security sandbox. Daytona remains the process isolation
  boundary. The Git workspace protects the live checkout from accidental
  writes during CodeAct.
- This does not bypass write-scope policy. Write-scope enforcement remains in
  the existing tool hook layer; the Git auditor does not add a second
  pre-OCC hard-block path.
- V1 does not need to support every Git change kind. Symlinks, gitlinks,
  submodules, mode-only changes, and unsupported binary edits should fail
  closed until OCC can represent them cleanly.

## High-Level Architecture

```text
daytona_codeact
      |
      v
submit_codeact_cmd(...)
      |
      v
CodeIntelligenceService.cmd(...)
      |
      v
GitWorkspaceAuditor.execute(...)
      |
      +--> GitWorkspacePool.lease(...)
      |       returns one clean workspace slot for this service.cmd operation
      |
      +--> GitWorkspaceSlot.prepare_baseline(...)
      |       baseline commit = current live workspace state
      |
      +--> run user command inside leased workspace
      |
      +--> GitDiffCollector.collect(...)
      |       git add -A, git diff, changed-path manifest, content hashes
      |
      +--> GitDiffCommitter.commit(...)
      |       converts diff to strict-base OCC changes
      |
      +--> WriteCoordinator.commit_operation_against_base(...)
              locks changed paths, verifies live hashes, writes atomically,
              records ledger entries, invalidates caches, refreshes index
```

`GitWorkspaceAuditor` is wired behind `svc.cmd`. `submit_codeact_cmd(...)`
keeps its `FileChangeResult` contract and maps `git_commit_status` to tool
success.

The public command response should expose:

- `result`
- `exit_code`
- `changed_paths`
- `ambient_changed_paths`
- `files_written`
- `git_commit_status`
- `git_conflict_file`
- `git_conflict_reason`

## Workspace Pool

Each sandbox owns one `GitWorkspacePool`.

The pool keeps up to `CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX` prewarmed slots.
Default: `20`.

```text
/tmp/eos-codeact-git/{sandbox_id}/
  live-metadata/
  slots/
    slot-000/
    slot-001/
    ...
    slot-019/
```

A slot is leased by at most one `service.cmd` call at a time. The lease covers
the full operation:

```text
lease -> prepare baseline -> run command -> collect diff -> OCC commit/abort
      -> reset/clean -> release
```

This preserves ephemeral operation semantics even though the directory is
pooled. No operation observes another operation's filesystem state because every
lease begins with a fresh baseline and every release resets or destroys the
slot.

If reset fails, the pool must discard that slot and recreate it in the
background. A failed reset must never release a dirty slot.

### Slot Implementation

The product concept is a "Git workspace slot": an isolated checkout directory
used for one CodeAct operation at a time.

Implementation should prefer local shared clones over `git worktree add` if
CodeAct commands can run arbitrary Git commands:

```bash
git clone --shared --no-checkout "$repo_root" "$slot"
git -C "$slot" checkout --detach HEAD
```

This keeps object reuse and fast setup while avoiding the shared `.git`
metadata risks of native Git worktrees. If later policy proves destructive Git
commands cannot affect shared metadata, native `git worktree` can be evaluated
as an implementation optimization without changing the service contract.

## Baseline Preparation

The baseline must be the current visible live workspace, not only `HEAD`.

The live workspace can contain:

- committed tracked files,
- staged tracked changes,
- unstaged tracked changes,
- untracked non-ignored source files.

For each leased operation, prepare the slot with a synthetic baseline commit:

1. Reset the slot to the source repo `HEAD`.

   ```bash
   git -C "$slot" reset --hard HEAD
   git -C "$slot" clean -fdx
   ```

2. Apply tracked live workspace changes relative to `HEAD`.

   ```bash
   git -C "$repo_root" diff --binary --full-index HEAD -- \
     | git -C "$slot" apply --binary --index
   ```

3. Copy untracked, non-ignored live files.

   ```bash
   git -C "$repo_root" ls-files --others --exclude-standard -z \
     | tar -C "$repo_root" --null -T - -cf - \
     | tar -C "$slot" -xf -
   ```

4. Commit the synthetic baseline inside the slot.

   ```bash
   git -C "$slot" add -A
   git -C "$slot" \
     -c user.name=EphemeralOS \
     -c user.email=ephemeralos@example.invalid \
     commit -q -m "EphemeralOS CodeAct baseline"
   ```

The synthetic baseline is local to the slot. It must never be pushed or
referenced from the live repo.

If baseline preparation cannot represent the live workspace, fail closed before
running the command.

## Command Execution

The user command runs inside the leased slot:

- `cwd` is the live `exec_cwd` remapped by relative path into the slot.
- `EOS_CODEACT_WORKSPACE_ROOT` points at the slot root.
- `EOS_LIVE_WORKSPACE_ROOT` points at the live repo for diagnostics only.
- The existing CodeAct timeout behavior is preserved.

The command exit code does not decide whether file changes are collected. The
previous audit path preserved audited changes even when the command exited
non-zero; Git workspace mode preserves that behavior unless product policy
changes separately.

### Absolute Path Remapping

The auditor rewrites the live workspace root in the wrapped command to the
leased slot path. It also exposes:

- `EOS_CODEACT_WORKSPACE_ROOT`: the leased slot root.
- `EOS_LIVE_WORKSPACE_ROOT`: the live root, for diagnostics only.

Write-scope hard-block enforcement is intentionally not duplicated here. The
existing CodeAct prehook owns that policy before execution reaches `svc.cmd`.

## Diff Collection

After command completion, the collector stages the slot and asks Git for the
operation diff:

```bash
git -C "$slot" add -A
git -C "$slot" diff --cached --binary --full-index --find-renames HEAD --
git -C "$slot" diff --cached --name-status -z --find-renames HEAD --
```

The collector returns a structured `WorkspaceDiff`:

```python
@dataclass(frozen=True)
class WorkspaceDiff:
    patch: str
    files: tuple[WorkspaceDiffFile, ...]
    baseline_commit: str
    workspace_root: str
    command_exit_code: int
    stdout: str

@dataclass(frozen=True)
class WorkspaceDiffFile:
    path: str
    old_path: str | None
    status: Literal["add", "modify", "delete", "rename"]
    base_existed: bool
    base_hash: str
    final_existed: bool
    final_hash: str
```

`base_hash` and `final_hash` must use the same content hash function as the
existing OCC layer. They should not rely on Git object IDs.

V1 rejects:

- paths outside `repo_root`,
- submodule or gitlink changes,
- symlink changes when OCC cannot represent them,
- mode-only changes,
- non-UTF-8 payloads if the adapter produces text `OperationChange` objects,
- diffs over hardcoded safety limits.

These limits should be constants in code, not additional environment flags. The
only user-facing configuration is pool size.

## OCC Integration

OCC integration is the correctness boundary.

The live workspace must not be modified by the Git workspace auditor until OCC
accepts the full batch. The first implementation should reuse the existing
`WriteCoordinator.commit_operation_against_base(...)` path.

`GitDiffCommitter` converts `WorkspaceDiff` into existing `OperationChange`
entries:

```python
WriteCoordinator.commit_operation_against_base(
    changes,
    edit_type="svc_cmd_git_workspace",
    description="daytona_codeact git workspace",
)
```

For each changed file:

- `file_path` is the live absolute path.
- `base_content` comes from the slot baseline commit.
- `final_content` comes from the slot index/worktree after command execution.
- deletes use `final_content=None`.
- `strict_base=True`.
- `base_hash` is the hash of the baseline content.

The existing coordinator then:

1. Locks all changed paths in deterministic order.
2. Reads current live file hashes.
3. Rejects the whole batch with `aborted_version` if any live hash differs from
   the baseline hash.
4. Applies all changes as one operation.
5. Rolls back on partial failure.
6. Records ledger entries.
7. Invalidates content, tree, LSP, and symbol-index caches for changed paths.

This is intentionally conservative. It removes overlay tar bandwidth while
keeping the proven OCC lock, verification, rollback, and cache-refresh path.

## OCC Gate Verification

The implementation is gated by these checks.

Unit tests:

- `GitDiffCommitter` sends every file as `OperationChange(strict_base=True)`.
- No live file write occurs before `WriteCoordinator` is called.
- `commit_operation_against_base(...)` receives one atomic batch per CodeAct
  operation, not per file.
- A changed live hash returns `aborted_version` and leaves all live files
  unchanged.
- An unrelated live edit does not block commit.
- A command timeout still resets or destroys the leased slot.
- A slot reset failure destroys the slot and does not release it as reusable.

Integration tests:

- Run `daytona_codeact` shell mode that modifies several files and verify:
  `changed_paths`, disk state, ledger entries, and cache invalidation.
- Run Python CodeAct wrapper mode and verify the same OCC path is used.
- Run two concurrent CodeAct operations touching the same path; exactly one
  commits and the other returns `aborted_version`.
- Run two concurrent CodeAct operations touching disjoint paths; both commit.
- Run 10/30/50/100 CodeAct operations with
  `CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX=20` and verify all changed files land.
- Verify no tarball-style workspace payload is transferred for diff collection.

Live canary acceptance:

- p95 latency for trivial unique-file CodeAct operations should stay below the
  previous overlay-audit baseline.
- Throughput should not regress below the previous baseline.
- Conflict behavior must match strict-base OCC semantics.
- Stale pool directories must be removed on service startup or sandbox attach.

## Configuration

Git workspace mode knows changed paths before live commit. That lets the
runtime run write-scope policy before OCC:

- hard block: reject the diff, reset the slot, and do not call OCC,
- advisory: call OCC and report `ambient_changed_paths`,
- no policy issue: call OCC normally.

The existing post-hook can stay during migration as defense-in-depth, but the
expected enforcement point is pre-OCC.

## Conflict Semantics

CodeAct execution does not hold live file locks. Locks are acquired only for
the short OCC commit.

If another operation edits a changed path while CodeAct is running:

- the live hash no longer equals the Git workspace baseline hash,
- OCC rejects the batch with `aborted_version`,
- no files from the CodeAct diff are written,
- the tool result includes `git_commit_status="aborted_version"` plus the
  conflict file and reason.

If another operation edits an unrelated path, the CodeAct diff can commit.

## Pool Lifecycle

Pool startup:

1. Resolve `repo_root`.
2. Confirm Git is available.
3. Remove stale pool directories for the sandbox.
4. Create up to `CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX` slots lazily or in a
   background prewarm task.

Lease:

1. Acquire an idle slot or wait for one.
2. Prepare the synthetic baseline for the current live workspace state.
3. Mark the slot leased to one `service.cmd` operation.

Release:

1. Reset `HEAD`.
2. `git clean -fdx`.
3. Remove transient command metadata.
4. Mark the slot idle.

Discard:

- reset failure,
- Git metadata corruption,
- unsupported diff state that leaves the slot uncertain,
- process cancellation during cleanup.

Discarded slots are recreated opportunistically to restore pool size.

## Configuration

Only one public configuration knob:

```text
CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX=20
```

Semantics:

- `0` disables prewarming and creates one disposable workspace per operation.
- `1` serializes CodeAct workspace execution per sandbox.
- `20` is the default target pool size.

All other guardrails should be code constants or derived from existing runtime
timeouts. Avoid adding user-facing flags for diff byte limits, queue limits,
bind modes, copy fallbacks, or legacy fallback paths.

## Rollout Plan

1. Add `WorkspaceDiff` and `WorkspaceDiffFile` types. Done.
2. Add `GitWorkspacePool` and slot lease/reset tests. Done.
3. Add baseline preparation tests for dirty tracked files and untracked files.
   Done.
4. Add `GitDiffCommitter` that adapts to `OperationChange(strict_base=True)`.
   Done.
5. Add `GitWorkspaceAuditor.execute(...)`. Done.
6. Switch `CodeIntelligenceService.cmd(...)` to `GitWorkspaceAuditor`. Done.
7. Switch `submit_codeact_cmd(...)` and post-hooks to `git_*` status fields.
   Done.
8. Remove legacy overlay auditor code and tests. Done.
9. Run OCC gate verification tests. Done for targeted unit/integration scope.
10. Run live Daytona 10/30/50/100 CodeAct pool benchmark.

## Benchmark Baseline

Current overlay benchmark on an existing sandbox for unique-file CodeAct writes:

```text
10 ops:  8.018s wall, 10/10 ok
30 ops:  4.585s wall, 30/30 ok
50 ops:  7.799s wall, 50/50 ok
100 ops: 15.980s wall, 100/100 ok
```

A sandbox-side 20-slot Git workspace pool prototype, excluding full production
tool/OCC wrapping but including slot reset, command write, Git diff collection,
and cleanup:

```text
pool prepare: 3.708s for 20 slots
10 ops:       0.698s wall, 10/10 ok
30 ops:       1.826s wall, 30/30 ok
50 ops:       2.972s wall, 50/50 ok
100 ops:      5.736s wall, 100/100 ok
```

These numbers justify building the production Git workspace auditor and then
rerunning the benchmark through actual `daytona_codeact` plus OCC.

## Open Questions

- Should native `git worktree` ever replace shared-clone slots, or should slot
  metadata isolation remain mandatory because CodeAct can run Git commands?
- Should non-zero command exits continue to commit audited changes forever, or
  should CodeAct eventually expose an explicit commit-on-failure policy?
- Which unsupported diff kinds should become supported after V1: symlinks,
  binary files, mode changes, or renames?
