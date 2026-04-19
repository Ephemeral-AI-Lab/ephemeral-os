# Git Worktree CodeAct Migration Plan

Status: proposed
Companion to: `docs/architecture/git-workspace-codeact.md`

## Goal

Replace the prewarmed shared-clone pool with on-demand `git worktree`-based
slots. Eliminate `git clone --shared`, prewarm, slot reset/reuse, and the
filesystem baseline snapshot copy. The OCC gate is unchanged and remains the
sole authority for live-workspace mutation.

## End-state per `service.cmd` operation

1. Acquire concurrency semaphore.
2. Build a dangling commit `SNAP` capturing live tracked + staged + unstaged +
   untracked. No ref is moved. No commit lands on `main`.
3. `git -C $repo worktree add --detach $slot $SNAP`.
4. Run command in `$slot`.
5. Diff slot final state vs `SNAP`; base content via `git show $SNAP:path`.
6. Submit `OperationChange(strict_base=True)` batch to OCC.
7. `git worktree remove --force $slot`; release semaphore.

## Why this works without committing to `main`

`git commit-tree` writes a commit object into the object database without
moving any ref. Combined with a redirected `GIT_INDEX_FILE`, the live `.git/index`
is also untouched:

```bash
TMP=$(mktemp)
GIT_INDEX_FILE=$TMP git -C $repo read-tree HEAD
GIT_INDEX_FILE=$TMP git -C $repo add -A     # honors .gitignore
TREE=$(GIT_INDEX_FILE=$TMP git -C $repo write-tree)
SNAP=$(git -C $repo commit-tree $TREE -p HEAD -m codeact-baseline)
rm $TMP
```

Properties:

- No branch is updated, so branch-protection / `pre-commit` / `commit-msg`
  hooks do not fire (they are bound to `git commit`, not plumbing).
- The live `.git/index` is byte-identical before and after.
- `SNAP` is reachable from the worktree because objects are shared, so
  `git -C $slot show $SNAP:path` is the authoritative source for `base_content`.
- `SNAP` is dangling. It will be GC'd by `git gc` after `gc.pruneExpire`
  (default 2 weeks). For an in-flight CodeAct op (seconds–minutes), this is
  safe.

## Why no pool

The pool existed to amortize `git clone --shared`. With worktrees there is no
clone — `git worktree add --detach` is `mkdir + .git pointer file + checkout`.
Per-lease cost is dominated by snapshot capture and checkout, neither of which
is amortizable across leases (each depends on current live state).

The pool degenerates into a concurrency cap. Replace it with
`asyncio.Semaphore`. Same backpressure, no idle disk usage, no slot-poisoning
state machine, no prewarm cost.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `commit-tree` fires a hook in some custom setup | Phase 1 verification + integration assertion |
| `git add -A` slow on huge repos | Set `core.untrackedCache=true` once per repo on first use |
| `SNAP` GC'd mid-op | Default `gc.pruneExpire=2.weeks`; never run `git gc` from inside CodeAct against the live repo |
| Worktree admin entries leak on crash | Startup `git worktree prune`; per-release `worktree remove --force` |
| CodeAct corrupts shared `.git` (`git gc`, ref deletion) | Pool-reliability problem only — OCC writes files directly, live data safe; failed lease retries; fatal corruption triggers sandbox rebuild |
| Concurrent `.git` lock contention | Measured in Phase 7 bench; mitigate per-case if observed |

## Out of scope

- Sparse checkout optimizations (measure first).
- Symlink, gitlink/submodule, mode-only diff support (defer per existing V1).
- Replacing OCC as the live-workspace write authority.

---

# Phase plan

Each phase follows the project's TDD discipline:

- Write the failing test (RED), commit `test: ...`.
- Implement minimally (GREEN), commit `fix: ...` or `feat: ...`.
- Optional refactor commit.
- Each commit must be reachable from the active branch's `HEAD`.

## Phase 1 — Verify hook safety on a real sandbox

Manual evidence-gathering. No production code changes.

Run on a representative sandbox:

1. Confirm `git commit-tree` does not fire `pre-commit` / `commit-msg` /
   `prepare-commit-msg` hooks.
2. Confirm `git worktree add --detach <slot> <dangling-sha>` succeeds against
   a commit not pointed to by any ref.
3. Confirm `git worktree remove --force <slot>` cleans both the filesystem
   and the `.git/worktrees/<name>/` admin entry.
4. Confirm `GIT_INDEX_FILE` redirection leaves the live `.git/index`
   byte-identical.

Record the evidence in this doc as a "Phase 1 results" section. Block all
remaining phases until these pass.

## Phase 2 — Snapshot primitive

**New module:** `backend/src/code_intelligence/routing/git_snapshot.py`

Public surface:

```python
async def build_live_snapshot(
    sandbox: Any,
    exec_process: Callable[..., Awaitable[Any]],
    repo_root: str,
) -> str:
    """Return the SHA of a dangling commit capturing current live state."""
```

**Tests** (`backend/tests/test_code_intelligence/test_git_snapshot.py`):

- snapshot of clean tree equals `HEAD`'s tree
- snapshot captures dirty tracked file
- snapshot captures untracked file
- snapshot respects `.gitignore`
- live `.git/index` is byte-identical before/after
- no ref is moved (`git for-each-ref` snapshot equality)
- no `pre-commit` hook fires (assert via instrumented hook)

TDD order:

1. Write all snapshot tests against a `pytest` `tmp_path` git repo. RED.
2. Commit: `test: reproducer for git snapshot primitive`.
3. Implement `build_live_snapshot`. GREEN.
4. Commit: `feat: git snapshot primitive for codeact baseline`.

## Phase 3 — Replace pool with `GitWorktreeSlots`

**Delete:** `backend/src/code_intelligence/routing/git_workspace_pool.py`
in full. All `_PREWARM_SCRIPT`, `_CREATE_SLOT_SCRIPT`, `_RESET_SLOT_SCRIPT`,
`ensure_slot`, `reset_slot`, `checkout_live_head`, `apply_live_state`,
`copy_baseline_snapshot` go.

**New:** `backend/src/code_intelligence/routing/git_worktree_slots.py`

```python
class GitWorktreeSlots:
    def __init__(
        self,
        *,
        sandbox_id: str,
        workspace_root: str,
        exec_process: Callable[..., Awaitable[Any]],
        max_concurrent: int,
    ) -> None: ...

    async def lease(self, sandbox: Any) -> GitWorktreeLease:
        """Acquire semaphore, snapshot live, add worktree, return lease."""

    async def release(self, sandbox: Any, lease: GitWorktreeLease) -> None:
        """Remove worktree (best-effort), release semaphore."""

    async def startup_prune(self, sandbox: Any) -> None:
        """One-shot `git worktree prune` to clear stale admin entries."""
```

**Update types** `git_workspace_types.py`:

- Drop `GitWorkspaceBaseline`, `GitWorkspacePrepareError`.
- Add `GitWorktreeLease(slot_path: str, snap_commit: str)`.
- Add `GitWorktreeError`.

**Tests** (`backend/tests/test_code_intelligence/test_git_worktree_slots.py`):

- semaphore caps concurrent leases
- `lease` registers a worktree visible to `git worktree list`
- `release` removes the worktree from `git worktree list`
- `release` after `worktree remove` failure still releases the semaphore
- startup prune clears stale admin entries
- `lease` returns a `snap_commit` reachable via `git cat-file -e`

TDD order: tests RED → commit → implement → GREEN → commit. Old pool tests
go in the same commit that deletes the pool module.

## Phase 4 — Wire the auditor

**Update** `git_workspace_auditor.py`:

- Replace the `pool: GitWorkspacePool` field with `slots: GitWorktreeSlots`.
- Remove the separate `prepare_baseline` call. `slots.lease()` already returns
  a ready-to-use lease with `snap_commit`.
- Pass `lease.snap_commit` to the diff collector instead of
  `baseline.snapshot_path`.

**Update** `command_executor.py`:

- Replace `GitWorkspacePool(...)` with `GitWorktreeSlots(...)`.
- Field rename `_git_workspace_pool` → `_git_worktree_slots`.
- Same lazy-init pattern.

**Tests** (`test_git_workspace_auditor.py`):

- repoint expectations from `snapshot_path` to `snap_commit`
- assert no separate `prepare_baseline` call is made
- existing failure / abort / OCC contract tests preserved

## Phase 5 — Diff collection via `git show`

**Update** the diff collector and `git_diff_committer.py`:

- Changed-path discovery: `git -C $slot diff --name-status -z $SNAP --`.
- Base content for OCC `OperationChange.base_content`:
  `git -C $slot show $SNAP:<path>`.
- Final content: read from slot working tree (unchanged).
- Deletes: present in `SNAP`, absent in slot.
- Drop all references to baseline snapshot directory paths.

**Tests:**

- `WorkspaceDiff` for add / modify / delete / rename matches the previous
  collector byte-for-byte on a fixture corpus.
- `git show $SNAP:path` is used for `base_content`, not filesystem reads.
- A non-UTF-8 base file fails closed per existing V1 policy.

## Phase 6 — Config

`git_workspace_config.py`:

- Keep env var name `CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX` for compat.
- Rename function: `git_workspace_pool_size_per_sandbox()` →
  `git_worktree_max_concurrent()`.
- Update docstring: "max concurrent CodeAct slots per sandbox".
- `0` semantics: redefine as "serialize to 1 concurrent slot" (simplest,
  preserves prior behavior closest). Document explicitly.

## Phase 7 — Stress, concurrency, and benchmark

**New stress test** (`backend/tests/test_code_intelligence/test_codeact_git_corruption.py`):

- CodeAct shell that runs `git gc`, `git update-ref -d refs/heads/main`,
  `git config user.email evil@example`, `rm -rf .git/objects/pack` from inside
  a slot.
- Expect: op fails cleanly with a `git_*` status, live workspace files intact,
  next op recovers.

**Existing concurrency tests** must still pass without modification:

- `backend/tests/test_e2e/test_live_ci_concurrent_edits.py`
- `backend/tests/test_e2e/test_live_ci_rename_perf.py`
- `backend/tests/test_e2e/test_live_daytona_occ_load.py`

**Re-run benchmark** from `docs/architecture/git-workspace-codeact.md:507-513`
on the same sandbox. Acceptance:

- p95 ≤ shared-clone p95 at 10 / 30 / 50 / 100 ops.
- Cold-start (first op after sandbox attach) is no worse than prior.

If `git add -A` dominates, enable `core.untrackedCache=true` on first use.

## Phase 8 — Doc + dead-code purge

- `git grep` for `git_workspace_pool`, `_PREWARM_SCRIPT`, `snapshot_path`,
  `copy_baseline_snapshot`, `prepare_baseline`. Confirm zero hits in source
  (excluding this migration doc).
- Update `docs/architecture/git-workspace-codeact.md`:
  - "Slot Implementation" → describe `commit-tree` + `worktree add --detach`.
  - Replace "Workspace Pool" with "Concurrency Cap" (semaphore).
  - Replace "Baseline Preparation" with "Snapshot Capture" (single shot).
  - Update "Pool Lifecycle" → "Slot Lifecycle" (lease → snapshot →
    worktree add → run → diff → OCC → worktree remove).
  - Update "Configuration" with redefined env-var semantics.
  - Close the open question about worktree.
  - Append new bench numbers.
- Move this migration doc to a `docs/architecture/history/` folder, or delete
  it — the design doc is now the canonical source.

---

# PR breakdown

The migration is structured as four PRs to keep blast radius and review load
contained.

| PR | Phases | Scope | Reverts cleanly? |
|---|---|---|---|
| 1 | 1, 2 | Verification evidence + `git_snapshot.py` + tests. Pure addition. | yes |
| 2 | 3, 4, 5, 6 | Replace pool with `GitWorktreeSlots`, wire auditor, diff via `git show`, redefine config. Service contract preserved at `service.cmd`, so existing E2E suite gates correctness. | yes — full module swap |
| 3 | 7 | Stress test + concurrency re-runs + benchmark numbers. | yes |
| 4 | 8 | Doc rewrite + final dead-code grep + remove this migration doc. | yes |

## PR 1 acceptance criteria

- `Phase 1 results` section appended to this doc with sandbox evidence.
- `git_snapshot.py` introduced.
- All snapshot tests pass.
- No production behavior change yet — `build_live_snapshot` is unused.
- Two checkpoint commits: one RED, one GREEN.

## PR 2 acceptance criteria

- `git_workspace_pool.py` deleted.
- `git_worktree_slots.py` introduced.
- All existing `service.cmd` E2E tests pass unchanged.
- Auditor and command executor use `snap_commit`, not `snapshot_path`.
- Diff collector reads base content via `git show $SNAP:path`.
- Config env var redocumented.

## PR 3 acceptance criteria

- New corruption stress test passes.
- Bench p95 ≤ prior at 10 / 30 / 50 / 100 ops.
- Bench numbers recorded in PR description for review.

## PR 4 acceptance criteria

- `git grep` clean of legacy identifiers.
- Design doc updated.
- This migration doc removed or archived.

---

# Open questions for the implementer

1. Should `startup_prune` also `git gc --prune=now` to evict orphaned `SNAP`
   commits, or rely on background gc cadence? Default: rely on background gc.
2. Should `GitWorktreeSlots` lease an actual `slot_path` directory, or hand
   out a placeholder and let `worktree add` create it? Cleaner: hand out a
   path string only; `worktree add` creates the dir.
3. Should `release` errors on `worktree remove` ever escalate to discarding
   the entire `GitWorktreeSlots` instance, or always best-effort? Default:
   always best-effort + log; the next op's `worktree prune` covers leaks.

# Phase 1 results

To be filled in by the implementer after running the verification checks on
a real sandbox.

```text
date:
sandbox id:
git version:
commit-tree fires hooks: <yes|no, evidence>
worktree add --detach <dangling-sha>: <ok|fail, evidence>
worktree remove --force cleans admin entry: <ok|fail, evidence>
GIT_INDEX_FILE leaves live index byte-identical: <ok|fail, evidence>
```
