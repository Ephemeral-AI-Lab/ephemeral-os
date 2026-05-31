# Sandbox → Rust migration — PROGRESS

Living status tracker for `docs/plans/sandbox-rust-external-migration-PLAN.md`.
Spec = PLAN.md. Landed-status snapshot = PLAN §13. This file = done/next checklist.

**Last updated:** 2026-05-31 · **Phase:** 0 (Bootstrap) code-complete; measurement/CI gates remain.

---

## Phase status at a glance

| Phase | Scope | Status |
|---|---|---|
| **0 — Bootstrap** | workspace, eos-protocol, put_archive, pins, CP-0/CI | 🟡 **code-complete; CP-0 + CI + fixture-pin remain** |
| 1 — ns-runner (fresh-ns) | `eos-runner` unshare→mount→exec | ⬜ skeleton only (`// PORT` anchors in place) |
| 2 — daemon + read paths | `eos-daemon` RPC, read verbs, readiness | ⬜ skeleton only |
| 3 — write/publish + shell/search + plugin (HIGH risk) | OCC/LayerStack publish, PPC | ⬜ skeleton only |
| 3.5 — isolated workspace | ns-holder + setns + shell-free net | ⬜ skeleton only |
| 5 — cutover | flip default, delete Python | ⬜ |

Legend: ✅ done · 🟡 partial · ⬜ not started.

---

## DONE (verified 2026-05-31, all checks re-run independently)

**Rust workspace `/sandbox` — 11 crates + xtask, ~7,800 LOC**
- ✅ `eos-protocol` **fully implemented + tested**: version/envelope/cas/audit/models/canonical. **29 tests green incl 18 executed CAS golden fixtures** (the `ensure_ascii` Unicode trap reproduced).
- ✅ Faithful **skeletons** for layerstack/overlay/occ/ephemeral/isolated/plugin/runner/ns-holder/daemon/eosd — **546 `// PORT backend/…:line` anchors + 19 `todo!()`**.
- ✅ `cargo check --workspace` green (12 crates) · `cargo clippy --workspace` green at deny-gate · `cargo fmt --all --check` clean.
- ✅ **Build-time guarantee holds**: `cargo tree -p eos-isolated` has no `eos-occ` edge (direct/transitive). HINGE split (`SnapshotLeasePort` vs `CommitTransactionPort` in `eos-layerstack`) + 3 severings wired (`OccServicesInjector` impls both `eos_occ::` and `eos_ephemeral::OccRuntimeServicesPort`, returns the per-root single writer — MF-1-aware).

**Contracts & fixtures (ground truth)**
- ✅ `sandbox/docs/contract/01-06.md` — source-verified wire/CAS/audit/models/provider/crate-map specs.
- ✅ `sandbox/crates/eos-protocol/fixtures/` — 18 CAS cases + envelope/audit/metrics fixtures (executed from real Python).
- ✅ `sandbox/docs/RUST-GUIDANCE.md` — the Rust standard for all builders (incl. exact `ensure_ascii` escaper spec).

**Python-side Phase 0 (surgical; pytest 26 passed + 1 skipped)**
- ✅ `put_archive` on `ProviderAdapter` Protocol + Docker adapter (async → `container.put_archive`) + Daytona stub.
- ✅ `backend/src/sandbox/host/runtime_artifact/__init__.py` (pin scaffold — version/SHA256/minisign/protocol_version, currently empty/unpinned).
- ✅ `backend/src/sandbox/_contract_fixtures/pin.json` + dual-CI pin-assert test (skips while `UNPINNED`).

**Docs**
- ✅ PLAN §12 (verified Docker/dask/plugin config) + §13 (Phase-0 status + 8 source-verified corrections).

**Re-verify everything:**
```
cd sandbox && cargo test -p eos-protocol && cargo check --workspace && cargo fmt --all --check
cd .. && uv run pytest backend/tests/unit_test/test_sandbox/test_provider/ backend/tests/unit_test/test_sandbox/test_contract_fixtures_pin.py -q
```

---

## NEXT — ordered, concrete

### A. Finish the Phase 0 EXIT GATE (needs the dask Linux container + CI)
1. **CP-0 baseline** — extend `backend/scripts/bench_sandbox_e2e.py` to run inside the `sweevo-dask__dask-10042` container (launch recipe in PLAN §12.2: `--cap-add=SYS_ADMIN,NET_ADMIN --security-opt seccomp=unconfined,apparmor=unconfined --tmpfs /eos-mount-scratch`). Capture per-call runtime-init, e2e total, daemon idle RSS, cold-start, upload time + **kernel/userns/overlay config**; commit `bench/baseline-{arch}.json`.
2. **CI musl cross-build** — `xtask` + `.github/workflows/ci.yml`: build `eosd-linux-{amd64,arm64}` (static musl via `cross`/`cargo-zigbuild`), run unit + fixture-parity + benches, produce `SHA256SUMS` + **minisign** `.minisig` + `protocol_version`.
3. **CP-1** — measure `put_archive` vs base64-over-exec for a 1.5–3 MB blob in-container (≤ CP-0 upload time AND size-constant).
4. **Flip the fixture pin** — vendor `eos-protocol/fixtures` into `backend/src/sandbox/_contract_fixtures/`, set `pin.json` `upstream_commit` + `fixtures_sha256`; the dual-CI assert flips from skip → hard-assert.

### B. Phase 1 — `eos-runner` fresh-ns (lowest risk, first real port)
- Fill the `todo!()`s in `eos-runner` + `eos-overlay` (fresh path only): `unshare→uid_map→fsopen/fsconfig/fsmount/move_mount→execve→result JSON→cleanup`. Anchors: `// PORT overlay/namespace_runner.py:250`, `overlay/kernel_mount.py:63-70`.
- Gate (in container): CP-2a (≥20× runtime-init vs CP-0) + CP-2b (no e2e regression) + AV-1 verb results + AV-3 cancel/teardown. Toggle `EOS_SANDBOX_RUNTIME=rust`.

### C. Phase 2 — daemon + read paths
- Fill `eos-daemon` server/dispatcher/audit-ring + read verbs + LayerStack/OCC **read** paths; host `EOS_SANDBOX_RUNTIME` dispatch fork + AF_UNIX local-fallback connector reproducing the **97/98** exit-code contract. Gate: CP-3 + AV-2 (respawn/readiness/endpoint-cache).

### D. Phase 3 (HIGH risk) — write/publish + OCC/LayerStack + plugin PPC
- Fill OCC publish (single `occ-commit-queue`, 0.002/64/3), LayerStack squash/GC, the **reentrant-RLock→Mutex restructuring** (do NOT 1:1 port — see RUST-GUIDANCE §5), `eos-plugin` PPC channel + MF-1 single-writer routing.
- Gate: CP-4 (final-workspace-state hash) + the **§7 differential/property tests under contention** (NOT fixtures) + AV-1c byte-identity + AV-7 forward/back on-disk parity + AV-10 plugin parity. Needs the Python differential harness.

### E. Phase 3.5 (isolated) then Phase 5 (cutover) — per PLAN §5.

---

## Notes / risks for next session
- **Skeletons are not logic.** The 19 `todo!()` bodies + 546 `// PORT` anchors are the precise work-list; each cites the exact Python `file:line` to port.
- **macOS can't verify Linux paths.** All syscall/overlay/OCC-contention work must be checked in the dask container (PLAN §12.2 recipe) — `cargo check` on macOS only validates the non-Linux `cfg` surface.
- **Not committed.** `/sandbox` is untracked; backend has 3 modified provider files + 2 new dirs — staging left to the user (parallel-agent worktree).
- **CAS byte-identity is the sharpest correctness lever** — any new code computing `manifest_root_hash`/`layer_digest` must pass `fixtures/cas/cases.json` (esp. the unicode cases).
