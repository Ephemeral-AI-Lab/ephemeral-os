# Slice 5a — Decouple overlay from OCC (in place, no move)

**Goal.** Strip the OCC call out of `OverlayCommandCommitter`. Have overlay return `dirty_changes` to the caller; the caller — never overlay — drives OCC commit. This is the correctness fix; landing it in place keeps it independently revertible.

**Depends on.** None (independent correctness fix; must land before Slice 5b). Per the implementation plan this slice ships first.

## Files

### Modify
- `backend/src/sandbox/code_intelligence/overlay/<committer>.py` (today's `OverlayCommandCommitter`):
  - Remove the OCC commit call.
  - New return shape: `OverlayRunOutcome { exit_code, stdout, stderr, dirty_changes, overlay_rejected: bool, conflict: ConflictInfo | None }`. This is overlay-local; not yet the §1.6 `ShellResult`.
  - Surface argv overflow as `conflict.reason="argv_too_large"` instead of today's bare-string failure. (Project memory: root fix is streaming the payload via stdin, not changing error semantics — but the result type now carries it cleanly.)
- The single current caller (today's `code_intelligence` shell path):
  - On `overlay_rejected=True`, short-circuit before any OCC ledger update.
  - On overlay success, call OCC commit explicitly with `dirty_changes`.
  - On OCC conflict after overlay success, propagate `ConflictInfo(reason="patch_failed", ...)` and leave overlay's upper layer captured for diagnosis.

### Add
- Integration tests covering the three gating scenarios below.

### Move / Delete
- None (in place — that's the point of 5a).

## Implementation tasks

1. Audit `OverlayCommandCommitter` for every place it touches OCC. Move that logic out to the caller. After this slice, overlay has zero `from sandbox.code_intelligence.mutations` (or any future `from sandbox.occ`) imports.
2. Wire the caller for the three branches: overlay-reject (no OCC), overlay-success / OCC-success, overlay-success / OCC-conflict.
3. Replace `_apply_remote_batch_checked`'s bare-string failure with structured `ConflictInfo(reason="argv_too_large", ...)`.
4. Keep the wire shape compatible with the existing daemon dispatch — entrypoint integration lands in 5b.

## Tests (gating — slice doesn't merge without these green)

- **Overlay-reject path.** Ledger untouched. Assert no OCC commit invocation; assert overlay's reject reason flows through `conflict.reason`.
- **Overlay-success → OCC-conflict.** `conflict.reason="patch_failed"`. Overlay's upper layer captured for diagnosis (e.g. addressable via the captured upper-layer path).
- **Argv overflow.** `conflict.reason="argv_too_large"`. No bare-string failure surfaces to the caller.
- All existing overlay tests stay green.

## Exit criteria

- Build / ruff / tests green.
- `grep -r "from sandbox.code_intelligence.mutations\|from sandbox.occ" backend/src/sandbox/code_intelligence/overlay/` returns zero hits.
- The caller — not overlay — is the one that calls OCC commit.

## Risks

- Decoupling regresses today's transactional behavior. Mitigation: the three integration tests above are the merge gate. Slice 5b moves files only after 5a is green.
- Independent revert: this slice should be revertible without touching slices 1–4. Mitigation: scope is overlay file + one caller + tests; no cross-package edits.
