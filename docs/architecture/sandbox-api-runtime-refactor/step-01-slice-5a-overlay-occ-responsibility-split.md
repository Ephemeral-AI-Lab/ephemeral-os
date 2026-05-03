# Step 1 — Slice 5a — Refactor overlay/OCC responsibility split (in place, no move)

**Goal.** Reshape the overlay→OCC seam so each peer owns one concern.

- **Overlay** is a pure upperdir capture. After the user command runs, it walks the upperdir and emits one record per entry (path, kind, base bytes, upper bytes). It is fully git-unaware: no `git check-ignore`, no live-workspace mutation, no gitinclude/gitignore policy, no `.git/` path filtering. The only structural reject it owns is the upperdir-budget cap (`REJECT_UPPER_FULL`) — its own machinery's hard limit. Even `.git/` writes pass through to OCC, which drops them silently (OCC depends on git anyway).
- **OCC** is the per-file merge-policy decider. Given a change-set from any source — overlay shell run, write op, edit op — OCC classifies via `git check-ignore` and routes per file:
  - **Gitincluded, regular utf-8** → strict-base ledger commit.
  - **Gitignored or outside `workspace_root`** → direct-merge to live workspace, no ledger.
  - **Gitincluded, structurally uncommittable** (non-utf8, symlink, opaque-dir whiteout that cannot be narrow-pruned) → conflict.

This collapses today's split where overlay decides classification inside the namespace and partial-direct-merges in-band. After this slice, overlay is mechanical and OCC is the only place merge policy lives.

**Depends on.** None. This is the correctness fix and architectural correction; it ships before Step 6 / Slice 5b.

**Scope note.** This slice is larger than the original "in-place OCC-decoupling" framing — it folds in the responsibility shift (overlay no longer classifies; OCC owns merge policy). `IMPLEMENTATION-PLAN.md` Step 1 reflects the expanded scope. The slice still ships as one PR and is revertible without touching Steps 2–6.

## Files

### Modify

In-namespace overlay runtime — `backend/src/sandbox/code_intelligence/overlay/runtime/`. Consolidate 11 files → 4 (one-function-per-file granularity has no payoff once classification is gone, and the residual `REJECT_UPPER_FULL` is one constant):

```
runtime/
├── __init__.py
├── runner.py    # main(), walk_upperdir, run_user_command, lowerdir base read, build UpperChange, REJECT_UPPER_FULL
├── mounts.py    # namespace mount setup (renamed from namespace.py)
├── ndjson.py    # wire format: UpperChange + _meta + _reject
└── types.py     # UpperEntry, UpperChange, PolicyRejectOutcome
```

Strip `git check-ignore`, `DirectRouteApplier`, narrow-prune, classifier, the `.git/`-write reject (`REJECT_DOTGIT`, `IGNORABLE_DOTGIT_WRITES`, `filter_ignorable_dotgit_writes`), and the `has_git_routing_metadata` precheck. The only structural overlay invariant is the upperdir-budget reject (`REJECT_UPPER_FULL`); **`.git/` filtering moves to OCC** because OCC depends on git anyway, and overlay should know nothing about git, including the `.git/` path string.

The runtime walks upperdir and emits one NDJSON record per entry:

```
UpperChange {
  rel: str,
  kind: "regular" | "whiteout" | "symlink" | "opaque_dir",
  base_bytes: bytes | None,
  upper_bytes: bytes | None,
  base_existed: bool,
}
```

Bytes are base64-encoded on the wire so binaries cross the seam unchanged. **`kind` decodes the overlayfs encoding (whiteout char-device, opaque-dir xattr, symlink mode), not git policy** — OCC needs the discriminator to choose write vs delete vs narrow-prune vs conflict, because these markers vanish when the namespace exits. Per-kind conventions:

| kind | upper_bytes | base_bytes | OCC routing |
|---|---|---|---|
| `regular` | file content | base content or `None` | gitinclude→ledger commit (utf-8 validated at OCC), gitignore/external→direct-merge |
| `whiteout` | `None` | base content or `None` | gitinclude→ledger delete, gitignore→`rm` on live |
| `symlink` | utf-8 link target | base content or `None` | gitincluded→conflict; gitignored→direct-merge as link |
| `opaque_dir` | `None` (children walked separately) | n/a | gitignored→narrow-prune; gitincluded→conflict |

utf-8 enforcement on regular-file content happens at the OCC seam, not in the runtime.

Orchestrator-side overlay — `backend/src/sandbox/code_intelligence/overlay/`:
- `auditor.py`:
  - Drop the OCC commit invocation.
  - Drop the gitinclude/gitignore split fields.
  - New return shape: `OverlayRunOutcome { exit_code, stdout, upper_changes: tuple[UpperChange, ...], overlay_rejected: bool, conflict: ConflictInfo | None, warnings, overlay_run_timings, overlay_stage_timings, policy_reject }`.
  - Drop `_overlay_runtime_bundle_bytes` compatibility shim (former-monolith test wrapper; remove if those tests are gone).
- `command_committer.py`: deleted. Its job was translating gitinclude `OverlayChange` → `OperationChange`; with overlay no longer producing OCC-shaped values, there is nothing to translate.
- `daemon_local.py`, `process_exec.py`: keep only as temporary execution shims
  for the existing daemon-local and process-exec paths. Prune the
  gitinclude/gitignore branches and carry `upper_changes` end-to-end. These
  files are not durable overlay policy modules; later runtime/client slices
  replace this plumbing with `runtime/server.py` plus `overlay/client.py`.
- `types.py`: add `UpperChange`; drop `OverlayChange`, `OverlayAuditResult` (unused after the shift), and the rich `OverlayDiff` classification fields. `OverlayRunOutcome` slims to the new return shape above.
- `results.py`: parser-only — `parse_diff_ndjson` returns `UpperChange` records. Move `audit_result` / `reject_result` SimpleNamespace builders into `command_executor.py` (caller-side projection, not overlay output).

Caller — `backend/src/sandbox/code_intelligence/overlay/command_executor.py`:
- `_render_outcome` calls a new OCC changeset entry on `upper_changes` and projects the verdict onto the legacy `SimpleNamespace` shape so upstream callers (`InProcessBackend.cmd`, agent tools) see `gitinclude_committed`, `gitignore_merged`, `git_commit_status`, etc., unchanged.

OCC — `backend/src/sandbox/code_intelligence/mutations/`:
- New entry `WriteCoordinator.apply_changeset(upper_changes, *, agent_id, edit_type, description) -> ChangesetResult`.
- `ChangesetResult { success, status, ledgered: tuple[str, ...], direct_merged: tuple[str, ...], conflict_reason: str | None, conflict_file: str | None, ... }`.
- Internally:
  1. **Drop `.git/` writes silently.** Filter every `upper_change` whose `rel == ".git"` or starts with `.git/` before classification. No conflict, no warning surfaces. Covers benign cases (`git status` mutating `.git/index` / `.git/index.lock`) and hostile cases (`echo > .git/HEAD`); the live `.git/` was always safe because the namespace upperdir absorbed the writes.
  2. Read orchestrator-side `git check-ignore` for the remaining change-set.
  3. Validate gitincluded entries (utf-8, no symlinks, no opaque dirs); per-file conflict on violation.
  4. Direct-merge gitignored / out-of-workspace entries to live workspace via the moved-out `direct_merge_factory` / `narrow_prune_opaque_factory` (lifted from the runtime into OCC).
  5. Build strict-base `OperationChange` values for the gitincluded utf-8 partition; commit through the existing strict-base path.
  6. Order: drop `.git/`, then direct-merge, then ledger commit. A ledger failure leaves runtime side-effects intact (matches today's mixed-partial-apply semantics).

### Add
- `backend/src/sandbox/code_intelligence/mutations/changeset.py` — the `apply_changeset` implementation; owns the lifted `git check-ignore` + direct-merge / narrow-prune helpers.
- `backend/src/sandbox/code_intelligence/overlay/runtime/runner.py` — fully rewritten (~60 lines): consolidates the stripped `main()` with `walk_upperdir`, `run_user_command`, lowerdir base-read, and the inline kind discriminators (`is_whiteout` / `is_opaque_dir` / `is_symlink`). All previously their own modules.
- `backend/src/sandbox/code_intelligence/overlay/runtime/mounts.py` — renamed from `namespace.py` (no functional change; new name reflects post-classifier scope).
- Integration tests covering the gating scenarios below.

### Delete
- `backend/src/sandbox/code_intelligence/overlay/command_committer.py`.
- `backend/src/sandbox/code_intelligence/overlay/runtime/classifier.py` — entire file; classification moves to OCC.
- `backend/src/sandbox/code_intelligence/overlay/runtime/direct_routes.py` — lifted into OCC `mutations/changeset.py`.
- `backend/src/sandbox/code_intelligence/overlay/runtime/gitignore.py` — lifted into OCC.
- `backend/src/sandbox/code_intelligence/overlay/runtime/command.py` — folded into `runner.py`.
- `backend/src/sandbox/code_intelligence/overlay/runtime/lowerdir.py` — folded into `runner.py`.
- `backend/src/sandbox/code_intelligence/overlay/runtime/upperdir.py` — folded into `runner.py`.
- `backend/src/sandbox/code_intelligence/overlay/runtime/overlay_kinds.py` — three one-line stat/xattr discriminators inlined into `runner.py`.
- `backend/src/sandbox/code_intelligence/overlay/runtime/namespace.py` — renamed to `mounts.py` (treat as delete-then-add for diff purposes).
- `backend/src/sandbox/code_intelligence/overlay/runtime/policy.py` — entire file deletes. `REJECT_DOTGIT`, `REJECT_GITIGNORE_WHITEOUT`, `REJECT_NON_UTF8_GITINCLUDE`, `REJECT_UNSUPPORTED_OPAQUE_DIR`, `REJECT_UNSUPPORTED_SYMLINK`, `IGNORABLE_DOTGIT_WRITES`, `reject_dotgit_writes`, `filter_ignorable_dotgit_writes` all removed (`.git/` policy moves to OCC; classification rejects move with the classifier). Residual `REJECT_UPPER_FULL` constant + `reject_exit_code` mapper inline into `runner.py`.
- `OverlayChange`, `OverlayAuditResult` from `overlay/types.py` (unused after the seam shift).

### Move
- None at the package level — overlay stays under `code_intelligence/`; peer relocation is Step 6 / Slice 5b's job.
- Within `overlay/runtime/`: 11 files collapse to 4 per the layout above. Internal reorganization, not peer relocation; revertible without touching Step 6 / Slice 5b.

## Implementation tasks

1. Reshape the in-namespace runtime to produce `UpperChange` records only, per the 4-file Option-B layout above. The runtime no longer reads `.gitignore`, no longer mounts direct-merge plumbing, no longer mutates the live workspace, no longer rejects `.git/` writes, and no longer requires git metadata in the lowerdir to run (drop the `has_git_routing_metadata` precheck). The only structural reject is upperdir-full (run-fatal); everything else flows to OCC. `kind` extends to `"regular" | "whiteout" | "symlink" | "opaque_dir"` so OCC can decode overlayfs semantics that disappear with the namespace; symlink targets ride in `upper_bytes`, opaque dirs carry `upper_bytes=None`.
2. Lift `direct_merge_factory` and `narrow_prune_opaque_factory` from the overlay runtime into `mutations/changeset.py`. They now run orchestrator-side after namespace exit.
3. Implement `WriteCoordinator.apply_changeset`. Per file:
   - **Path under `.git/`** (or `rel == ".git"`): silently drop. No conflict, no warning. The namespace upperdir absorbed the write; live `.git/` was never touched.
   - **External** (path outside `workspace_root`): direct-merge.
   - **Gitignored regular**: direct-merge.
   - **Gitignored whiteout**: rm on live workspace; opaque-dir whiteout → narrow-prune.
   - **Gitincluded regular utf-8**: strict-base ledger commit.
   - **Gitincluded whiteout, base existed**: strict-base ledger delete.
   - **Gitincluded non-utf8 / symlink / opaque-dir**: per-file conflict → aggregate `success=False, conflict_reason="patch_failed"`. Direct-merges already ran; gitinclude partition does not commit.
4. Wire `AuditedCommandExecutor._render_outcome`:
   - On `overlay_rejected=True` → render rejection verdict, no OCC call.
   - On overlay success → call `apply_changeset` with `outcome.upper_changes`. Project the verdict onto the legacy SimpleNamespace.
5. Replace `_apply_remote_batch_checked`'s bare-string failure with structured `ConflictInfo(reason="argv_too_large", ...)`. Streaming the payload via stdin is the proper fix and is tracked separately; this slice's job is only to surface the condition cleanly.
6. Keep the wire shape compatible with existing daemon dispatch — server relocation lands later in the runtime scaffolding steps.

## Tests (gating — step doesn't merge without these green)

- **OCC silently drops `.git/` writes.** A run that writes `src/foo.py` AND `.git/HEAD` AND mutates `.git/index` (e.g. via `git status`). Overlay emits `UpperChange` records for all three (no overlay reject); `apply_changeset` filters the two `.git/*` entries before classification. Live `.git/HEAD` is byte-identical pre/post (the namespace upperdir absorbed the write — live was never touched). Ledger commits exactly `src/foo.py`. No conflict, no warning surfaces; `git_commit_status` is the normal success verdict.
- **Overlay success / mixed change-set.** A run that writes `src/foo.py` (gitinclude) AND `.venv/x` (gitignore) AND `/tmp/y` (external) produces:
  - `gitinclude_committed=("src/foo.py",)`, ledger advances by exactly one commit.
  - `gitignore_merged` covers `.venv/x` and `/tmp/y`; both visible on the live workspace post-call (byte-identical to upperdir).
  - `mixed_gitinclude_gitignore=True`.
- **In-namespace runtime is read-only on live workspace.** Run a command that writes only `.venv/x`. Snapshot the live workspace immediately after the namespace exits and before `apply_changeset` returns: no change. After `apply_changeset` returns: `.venv/x` present.
- **OCC conflict on gitincluded non-utf8.** A run that writes `src/foo.py` (utf-8) AND `src/bin.dat` (binary). Direct-merges (none here) run first; OCC verdicts `success=False, conflict_reason="patch_failed", conflict_file="src/bin.dat"`. `src/foo.py` does not land in the ledger; gitignore paths in the same change-set already merged.
- **OCC conflict on symlink under workspace.** Symlink at `src/link` → conflict; gitignore siblings in the same change-set still direct-merge.
- **Argv overflow.** OCC commit exceeds `ARG_MAX` → `conflict.reason="argv_too_large"`. No bare-string failure surfaces to the caller.
- **Wire pass-through for binaries.** A run that writes a non-utf8 gitignored file (e.g. compiled `.pyc`) flows through NDJSON unchanged and lands on disk byte-identical via direct-merge.
- All existing overlay tests stay green.

## Exit criteria

- Build / ruff / tests green.
- `grep -rn "git check-ignore\|check_ignore\|direct_merge\|narrow_prune\|gitignore\|REJECT_DOTGIT\|IGNORABLE_DOTGIT\|has_git_routing_metadata\|\\.git" backend/src/sandbox/code_intelligence/overlay/` returns zero hits — overlay knows nothing about git, including `.git/` as a path string.
- `grep -rn "from sandbox.code_intelligence.mutations\|from sandbox.occ" backend/src/sandbox/code_intelligence/overlay/` returns zero hits — overlay never imports OCC.
- The OCC coordinator — not overlay — is the only place that calls `git check-ignore` or writes to the live workspace.
- The caller — not overlay — drives `apply_changeset`.

## Risks

- **Wire size.** Today gitignored upperdir content (`.venv/` from `pip install`, etc.) direct-merges inside the namespace and never crosses the orchestrator boundary. After 5a, the same content rides through NDJSON. The existing `EOS_OVERLAY_UPPER_SIZE_MB` cap (default 512 MB) bounds total upperdir bytes — same cap now bounds total NDJSON payload. Daemon-local mode is unaffected; remote-daytona pays one upload + one round-trip per `pip install`-class run. Mitigation: confirmed acceptable per design call; cap remains 512 MB.
- **Atomicity regression.** Today direct-merge happens before namespace exit; namespace exit is the atomic commit point. After 5a, direct-merge happens orchestrator-side after namespace exit; a crash mid-merge can leave live workspace partially updated. Mitigation: per-file atomic rename (lifted `direct_merge_factory` already does this); OCC orders direct-merges before ledger commit so a ledger failure preserves runtime side-effects.
- **Slice size.** This slice is no longer the small in-place correctness fix the original 5a contracted on. `IMPLEMENTATION-PLAN.md` Step 1 updated to reflect expanded scope; the integration tests above are the merge gate.
- **Independent revert.** Scope is overlay package + OCC coordinator (`changeset.py`) + one caller + tests; no edits to Step 2–5 surfaces. Revert by `git revert <step-1-merge>`.
