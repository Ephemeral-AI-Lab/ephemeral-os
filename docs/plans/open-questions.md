# Open Questions

## replace_all + multi_edit - 2026-05-29
- [ ] Should `docs/architecture/tools` get a one-line note on the shared `apply_search_replace` helper and the `replace_all` last-write-wins OCC semantic? — Keeps curated memory in sync with the new seam; low effort, deferred out of scope.
- [ ] Should `multi_edit` later report per-edit occurrence counts? — Would require threading counts back through the `DAEMON_OP_EDIT_FILE` payload from both apply sites (heavy); only worth it if agents need occurrence feedback.
- [x] Confirm the shared-helper import edge (`sandbox.shared.edit_apply` imported by `sandbox.occ.path_staging`) passes the dependency-boundary test — RESOLVED: `test_sandbox/test_occ/test_occ_dependency_boundaries.py` (4) and `test_sandbox/test_import_fence.py` (17) both pass with the new import in place.

### Implementation note — helper differs from PLAN §4 literal sketch (INTENTIONAL)
The shipped `apply_search_replace` does NOT raise on `count == 0` unconditionally
as §4's code sketch shows. §4 contradicts the plan's own success criterion
§1.5 ("default behavior byte-for-byte unchanged") and the two BL-05 regression
tests in `test_daemon/test_edit_handler.py`, which document
`expected_occurrences == 0` + absent anchor → **no-op success** as intended
behavior (a prior bug fix). The shipped helper:
- `replace_all=True`: `count == 0` → "anchor not found" (D6 honored).
- `replace_all=False`: aborts only when `count != expected_occurrences`, with
  "anchor not found" when `count == 0` (preserves Site A's existing message and
  `test_direct_merge`) else "anchor occurrence count mismatch"; `expected==0` +
  absent anchor stays a no-op success.
This is byte-for-byte identical to Site A (OCC) for all production inputs
(`expected` is always 1 — the payload drops `expected_occurrences`, both daemon
readers default it to 1). Only ONE pre-existing test changed: the
`expected_occurrences==0` + present-anchor case in `test_edit_handler.py` now
matches "anchor occurrence count mismatch" (the converged message) instead of
"expected 0 occurrences"; the rejection behavior is unchanged. Do NOT "fix" the
helper back to §4's literal form — it would regress BL-05.
