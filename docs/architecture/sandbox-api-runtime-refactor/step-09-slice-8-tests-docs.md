# Step 9 — Slice 8 — Tests + docs

**Goal.** Relocate tests into the new layout. Add coverage for runtime/pipelines that didn't exist in the old structure. Supersede earlier design docs.

**Depends on.** Step 8 / Slice 7.

## Files

### Move
- `test_sandbox/test_code_intelligence/test_overlay/` → `test_sandbox/test_overlay/`.
- `test_sandbox/test_code_intelligence/test_mutations/` (or equivalent) → `test_sandbox/test_occ/`.
- `test_sandbox/test_code_intelligence/test_daemon/` → `test_sandbox/test_runtime/`.

### Add
- `test_sandbox/test_runtime/test_bundle.py` — idempotent + content-addressed upload contract.
- `test_sandbox/test_runtime/test_setup_orchestrator.py` — registry behavior + `run_all` ordering.
- `test_sandbox/test_runtime/test_server.py` — generic OP_TABLE dispatch, unknown-op error, JSON envelope contract on stdout.
- `test_sandbox/test_runtime/test_shell_pipeline.py` (if not landed in Slice 5b) — overlay→OCC composition + one-wire-trip property.
- `test_sandbox/test_runtime/test_edit_pipeline.py` — atomic multi-edit; conflict rollback.
- `test_sandbox/test_runtime/test_write_pipeline.py` — write+commit in one server call.

### Modify
- `docs/architecture/sandbox-api-runtime-refactor.md`: add a "Status: shipped" header line.
- `docs/architecture/occ-overlay-daemon-refactor.md`: prepend a banner — "**Superseded by [`sandbox-api-runtime-refactor.md`](./sandbox-api-runtime-refactor.md).**"

### Delete
- `test_sandbox/test_code_intelligence/` — the umbrella; expected empty after the moves above.

## Implementation tasks

1. `git mv` each test directory in turn. Run `pytest` after each move; fix any import drift before the next move.
2. Fill in the new `test_runtime/` coverage. Three pipelines × at least: success path, conflict path, one-wire-trip property.
3. Update doc cross-references; add the superseded banner.
4. Verify `find test_sandbox/test_code_intelligence -type f` is empty before deleting the directory.

## Tests

- This slice is the test work itself.

## Exit criteria

- `make build`, `ruff check`, `make test` green.
- `find test_sandbox/test_code_intelligence -type f` returns empty.
- `pipelines.py` has direct test coverage for all three verbs (shell, edit, write).
- `occ-overlay-daemon-refactor.md` carries the superseded banner; cross-references resolve.

## Risks

- Test moves shadow imports satisfied implicitly by the old path. Mitigation: run `pytest` after each `git mv` rather than batching all moves.
- New pipeline tests duplicate Slice 5b/4 tests instead of complementing them. Mitigation: this slice's tests target `pipelines.py` directly (unit-level); 5b/4 tests target the verb dispatch end-to-end.
