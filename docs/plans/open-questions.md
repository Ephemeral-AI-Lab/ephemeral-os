# Open Questions

## Sandbox Rust External Migration - 2026-05-31 (RESOLVED iteration 2)
- [x] Delivery mechanism — RESOLVED: Option A (pinned released artifact). Submodule (B) reintroduces a backend build-toolchain dependency; no atomic cross-repo need justifies it.
- [x] Parametric perf gates — RESOLVED: ACCEPTED as a strength. Thresholds lock at CP-0 against the measured in-sandbox baseline; no host-proxy number is quoted as a gate.
- [x] Fixture ownership — RESOLVED: `eos-protocol/fixtures` canonical + backend-vendored pinned copy + dual-CI pin assert; recovery contract (exit 97/98 + `api.runtime.ready{layer_stack_root}`) added to the canonical set.
- [x] Phase 4 timing — RESOLVED: DEFER isolated-workspace (`setns`) to a follow-up milestone; it stays on the Python `setns` path behind its own flag (DoD permits this).
- [x] Canary window — RESOLVED: per-sandbox A/B (not per-op), ≥1 full traffic cycle, N≥1,000/op-class, zero mismatch (AV-5b / M3).
- [ ] Artifact signing/provenance (S5 follow-up) — SHA-pin proves integrity, not provenance; design a signing/provenance step before GA.
- [ ] CP-1b BYO-image matrix membership — which kernel versions + non-root + read-only-rootfs base images to certify? Needs Architect input; not blocking the plan.

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
