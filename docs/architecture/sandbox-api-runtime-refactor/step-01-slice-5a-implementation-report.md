# Step 1 / Slice 5a - Implementation Report

Companion to
[`step-01-slice-5a-overlay-occ-responsibility-split.md`](./step-01-slice-5a-overlay-occ-responsibility-split.md).
This report records what actually landed for the overlay/OCC responsibility
split, the deleted legacy surface, and the verification evidence.

---

## 1. Verdict

**Step 1 ships as the overlay/OCC correctness split.**

Overlay is now a pure upperdir capture path. It runs the command in a fresh
overlay namespace, reads `diff.ndjson`, and returns raw `UpperChange` records.
It no longer owns git classification, live workspace mutation, OCC commits, or
legacy `SimpleNamespace` response projection.

OCC now owns merge policy for the captured change-set. The caller layer invokes
`WriteCoordinator.apply_changeset(...)`, then projects the OCC verdict onto the
legacy shell-command response shape until the public sandbox shell API lands in
later slices.

The implementation is deletion-heavy: the Step 1 commit changed 36 files,
adding 1756 lines and deleting 3810 lines, for a net reduction of about 2054
lines.

---

## 2. File Inventory

### Overlay Runtime

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/sandbox/code_intelligence/overlay/runtime/runner.py` | rewritten | In-namespace command execution, upperdir walk, base-byte read, `UpperChange` construction, upperdir-full exit code |
| `backend/src/sandbox/code_intelligence/overlay/runtime/mounts.py` | renamed from `namespace.py` | Namespace and overlay mount setup |
| `backend/src/sandbox/code_intelligence/overlay/runtime/ndjson.py` | rewritten | Base64 `UpperChange` NDJSON writer and reject writer |
| `backend/src/sandbox/code_intelligence/overlay/runtime/types.py` | simplified | `UpperEntry`, `UpperChange`, `PolicyRejectOutcome` |
| `backend/src/sandbox/code_intelligence/overlay/runtime/__init__.py` | simplified | Runtime facade exports only capture runtime helpers |

Deleted from the runtime:

- `classifier.py`
- `direct_routes.py`
- `gitignore.py`
- `command.py`
- `lowerdir.py`
- `upperdir.py`
- `overlay_kinds.py`
- `policy.py`

### Overlay Orchestrator Side

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/sandbox/code_intelligence/overlay/capture_runner.py` | renamed from `auditor.py` | Runs overlay capture and returns `OverlayRunOutcome` with raw upper changes |
| `backend/src/sandbox/code_intelligence/overlay/results.py` | parser-only | Parses `diff.ndjson` into `OverlayCapture` or `OverlayPolicyReject` |
| `backend/src/sandbox/code_intelligence/overlay/types.py` | simplified | `UpperChange`, `OverlayCapture`, `OverlayRunOutcome`, reject/error types |
| `backend/src/sandbox/code_intelligence/overlay/process_exec.py` | updated shim | Remote/process exec path carries raw upper changes |
| `backend/src/sandbox/code_intelligence/overlay/daemon_local.py` | updated shim | Daemon-local path carries raw upper changes |
| `backend/src/sandbox/code_intelligence/overlay/support.py` | narrowed | Runtime bundle helpers and capture constants |

Deleted from overlay:

- `auditor.py`
- `command_executor.py`
- `command_committer.py`

### OCC And Caller Layer

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/sandbox/code_intelligence/mutations/changeset.py` | new | Applies raw upper changes through OCC-owned policy |
| `backend/src/sandbox/code_intelligence/mutations/write_coordinator/coordinator.py` | updated | Adds `WriteCoordinator.apply_changeset(...)` |
| `backend/src/sandbox/code_intelligence/mutations/content_manager.py` | updated | Adds byte write, path delete, symlink, and child-list helpers for direct merge |
| `backend/src/sandbox/code_intelligence/shell_command_executor.py` | moved out of overlay | Temporary git/OCC-aware legacy shell response adapter |
| `backend/src/sandbox/code_intelligence/backends/in_process.py` | updated | Imports the moved command executor |

### Tests

| File | Coverage |
| --- | --- |
| `backend/tests/test_sandbox/test_code_intelligence/test_changeset.py` | OCC routing for `.git` drops, gitignored direct merge, binary conflict, symlink conflict, opaque-dir prune, argv overflow |
| `backend/tests/test_sandbox/test_code_intelligence/test_overlay_occ_decoupling.py` | Overlay reject skips OCC, overlay success calls OCC, OCC conflicts surface on legacy shell response |
| `backend/tests/test_sandbox/test_code_intelligence/test_overlay_capture_runner.py` | NDJSON parsing, reject parsing, readback, freshness guard |
| `backend/tests/test_sandbox/test_code_intelligence/test_overlay_capture_runner_execution.py` | Capture-runner process execution and runtime bundle shape |
| `backend/tests/test_sandbox/test_code_intelligence/test_overlay_run.py` | Runtime helper unit coverage for whiteout/opaque detection and exit-code mapping |
| `backend/tests/test_sandbox/test_code_intelligence/test_overlay_run_filesystem.py` | Base64 wire format, binary pass-through, reject wire shape |
| `backend/tests/test_sandbox/test_code_intelligence/test_overlay_run_metadata.py` | Runtime bundle module inventory |
| `backend/tests/test_sandbox/test_code_intelligence/test_overlay_daemon_local_parity.py` | Daemon-local and process-exec result-shape parity |
| `backend/tests/test_sandbox/test_code_intelligence/test_overlay_dispatch.py` | `svc.cmd` dispatches through capture runner |

---

## 3. Behavior Delivered

### Overlay Is Mechanical

The runtime no longer reads git metadata, runs `git check-ignore`, classifies
paths, rejects `.git/` writes, or direct-merges gitignored paths. It only emits:

```text
UpperChange {
  rel,
  kind,
  base_bytes,
  upper_bytes,
  base_existed,
}
```

The wire uses base64 for bytes, so binary content crosses the seam unchanged.

### OCC Owns Policy

`WriteCoordinator.apply_changeset(...)` now owns the policy that used to be
split across overlay runtime and `OverlayCommandCommitter`:

- Drops `.git/` upper changes silently.
- Uses `git check-ignore` only in OCC-owned code.
- Direct-merges gitignored or external paths.
- Converts gitincluded UTF-8 regular/whiteout changes to strict-base
  `OperationChange` values.
- Returns structured `ChangesetResult` conflict data for binary, symlink,
  opaque-dir, patch, or argv-size failures.

### Caller Owns Legacy Projection

`shell_command_executor.py` is intentionally outside `overlay/`. It is the
temporary compatibility layer that turns `OverlayRunOutcome` plus
`ChangesetResult` into the old shell `SimpleNamespace` fields:

- `changed_paths`
- `ambient_changed_paths`
- `gitinclude_changed_paths`
- `gitignore_direct_merged_paths`
- `git_commit_status`
- `git_conflict_reason`
- `mixed_gitinclude_gitignore`
- `mixed_partial_apply`

That projection no longer lives in `overlay/`.

---

## 4. Removed Legacy Surface

Step 1 removed these old responsibility leaks:

- Overlay-side OCC commit invocation.
- `OverlayCommandCommitter`.
- `overlay/command_executor.py`.
- Runtime-side gitignore classification.
- Runtime-side direct-merge and narrow-prune helpers.
- Runtime-side `.git/` reject/filter policy.
- `OverlayChange`, `OverlayAuditResult`, and rich `OverlayDiff` classification
  fields.

The follow-up cleanup pass also removed unused remnants that survived the main
split:

- `OverlayCommandResult`
- `ConflictInfo.upper_layer_path`
- `_overlay_runtime_bundle_bytes` compatibility wrapper
- `SandboxTransport`'s runtime `ProviderAdapter` stub inheritance, which was
  unnecessary for Step 1 and forced extra daemon bundle coupling

---

## 5. Verification

Focused verification commands used for this implementation:

- `uv run pytest backend/tests/test_sandbox/test_code_intelligence -q`
- `uv run pytest backend/tests/test_sandbox/test_providers -q`
- `uv run pytest backend/tests/test_sandbox/test_eager_ci_bootstrap.py backend/tests/test_sandbox/test_lifecycle.py backend/tests/test_sandbox/test_workspace.py backend/tests/test_sandbox/test_api_contract.py backend/tests/test_sandbox/test_audited_sandbox_api.py -q`
- `uv run ruff check backend/src/sandbox/api backend/src/sandbox/lifecycle backend/src/sandbox/providers backend/tests/test_sandbox/test_providers backend/tests/test_sandbox/test_code_intelligence`
- `git diff --check`

Structural gates:

```bash
rg "git check-ignore|check_ignore|direct_merge|narrow_prune|gitignore|REJECT_DOTGIT|IGNORABLE_DOTGIT|has_git_routing_metadata|\\.git" backend/src/sandbox/code_intelligence/overlay
rg "from sandbox\\.code_intelligence\\.mutations|from sandbox\\.occ" backend/src/sandbox/code_intelligence/overlay
rg "gitinclude|gitignore|git_commit|apply_changeset|SimpleNamespace" backend/src/sandbox/code_intelligence/overlay
test ! -e backend/src/sandbox/code_intelligence/overlay/auditor.py
test ! -e backend/src/sandbox/code_intelligence/overlay/command_executor.py
test ! -e backend/src/sandbox/code_intelligence/overlay/command_committer.py
```

All structural gates returned clean results during verification.

---

## 6. Deferred Items

These remain intentionally outside Step 1:

- Moving overlay to the final `sandbox/overlay/` peer package. That is Step 6 /
  Slice 5b.
- Replacing `daemon_local.py` and `process_exec.py` shims with the runtime
  server/client shape.
- Removing `shell_command_executor.py` and the old shell `SimpleNamespace`
  response contract. That waits for the public sandbox shell API slice.
- Solving large changesets by streaming checked-apply payloads over stdin. Step
  1 only surfaces argv overflow as structured conflict data.
- Full live/e2e validation against external sandbox providers, where available.

---

## 7. Definition Of Done

- Overlay package is git-unaware and OCC-unaware.
- OCC owns `git check-ignore`, direct merge, strict-base ledger commit, and
  structural conflict policy.
- Caller layer owns legacy shell response projection.
- Old overlay files and classifier modules are deleted.
- Focused tests and structural greps pass.
- This implementation report records the delivered shape and remaining
  migration boundaries.
