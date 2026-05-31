# Sandbox → Rust migration — PROGRESS

Living status tracker for `docs/plans/sandbox-rust-external-migration-PLAN.md`.
Spec = PLAN.md. Landed-status snapshot = PLAN §13. This file = done/next checklist.

**Last updated:** 2026-05-31 · **Phase:** 0 (Bootstrap) locally closed for amd64 with local build/package/upload; release-grade signing and arm64 matrix remain later.

---

## Phase status at a glance

| Phase | Scope | Status |
|---|---|---|
| **0 — Bootstrap** | workspace, eos-protocol, put_archive, pins, CP-0/local upload | ✅ **local amd64 closeout complete; signing/arm64 matrix deferred** |
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
- ✅ `xtask package` implemented for `eosd-linux-{amd64,arm64}`: default builder is `rust-lld` (`cargo` with `RUSTFLAGS=-C linker=rust-lld`), with optional `cargo`/`cross`; writes binary-only `SHA256SUMS`, `protocol_version`, per-artifact JSON manifests, and optional minisign `.minisig` signatures. Both target artifacts package locally (`amd64` SHA `ad69bd919d4ed912756180927af993047166a134659d67048153317534ecb8a9`, `arm64` SHA `8dce5809d22c39865158a97a964225cd62b0cba038498b59656c4ab37fb5ec76`); only amd64 has runtime upload verification in this session.
- ✅ **Build-time guarantee holds**: `cargo tree -p eos-isolated` has no `eos-occ` edge (direct/transitive). HINGE split (`SnapshotLeasePort` vs `CommitTransactionPort` in `eos-layerstack`) + 3 severings wired (`OccServicesInjector` impls both `eos_occ::` and `eos_ephemeral::OccRuntimeServicesPort`, returns the per-root single writer — MF-1-aware).

**Contracts & fixtures (ground truth)**
- ✅ `sandbox/docs/contract/01-06.md` — source-verified wire/CAS/audit/models/provider/crate-map specs.
- ✅ `sandbox/crates/eos-protocol/fixtures/` — 18 CAS cases + envelope/audit/metrics fixtures (executed from real Python).
- ✅ `sandbox/docs/RUST-GUIDANCE.md` — the Rust standard for all builders (incl. exact `ensure_ascii` escaper spec).

**Python-side Phase 0 (surgical; focused sandbox tests passed)**
- ✅ `put_archive` on `ProviderAdapter` Protocol + Docker adapter (async → `container.put_archive`) + Daytona stub.
- ✅ `backend/src/sandbox/host/runtime_artifact/__init__.py` pins the local amd64 artifact: `EOSD_VERSION=0.1.0-local.20260531`, SHA256 `ad69bd919d4ed912756180927af993047166a134659d67048153317534ecb8a9`, protocol version `1`. Minisign remains empty until the later release-provenance gate.
- ✅ `backend/src/sandbox/_contract_fixtures/` vendors the Rust fixtures; `pin.json` is hard-pinned to `2df20649b3158324d1be9c4c6c53a5844034ebc2` with `fixtures_sha256=3d62ff3017bf1b1a76e36de08ea4a3185d9640cb9ca98f7e4a1796b153aab221`; the backend pin assert is hard-fail (no skip).
- ✅ `EOS_SANDBOX_RUNTIME=python|rust` no-op host read exists in `daemon_client.py` and validates values; the actual dispatch fork remains Phase 2.
- ✅ `backend/scripts/bench_sandbox_e2e.py` has Docker-backed Phase 0 mode for CP-0 + CP-1 (`--phase0`) plus local artifact upload verification (`--eosd-binary`) that uses `put_archive`, Docker archive readback, and direct binary exec. It does not install `apt`/`pkg` packages or require Rust/Python/sha256 tools inside the target sandbox image for the artifact check.
- ✅ GitHub CI is **not** part of the current Phase 0 closeout path. The current path is: build/package locally, then upload the static binary into the sandbox/container.

**Phase 0 CP baseline artifacts**
- ✅ `bench/baseline-amd64.json` captured in `sweevo-dask__dask-10042:latest` (Ubuntu 22.04.4, Python 3.10.14, kernel `6.10.14-linuxkit`, `x86_64`, `/eos-mount-scratch` tmpfs, overlay-in-userns probe green).
- ✅ CP-0 measured: runtime bundle upload `3957.262 ms`; daemon cold-start `816.200 ms`; daemon idle RSS `36,796 KiB`; Python process-start p50 `398.149 ms`; warm heartbeat p50 `1.235 ms`, p95 `2.341 ms`.
- ✅ CP-1 passed: `put_archive` vs base64-over-exec for `1.5 MiB` (`19.310 ms` vs `22,075.007 ms`, 64 chunks) and `3.0 MiB` (`31.074 ms` vs `44,192.962 ms`, 128 chunks); all SHA256s matched; put-archive size ratio `1.609` ≤ `2.5`.
- ✅ `bench/local-eosd-amd64-upload.json` captured the local artifact handoff: `sandbox/dist/eosd-linux-amd64` (417,664 bytes, static PIE) uploaded to `/tmp/eosd-local/eosd` in `10.840 ms`; readback SHA256 matched `ad69bd919d4ed912756180927af993047166a134659d67048153317534ecb8a9`; mode `0755`; direct exec returned `eosd 0.1.0`. The report records `rustc=missing` and `cargo=missing` inside the dask image.

**Docs**
- ✅ PLAN §12 (verified Docker/dask/plugin config) + §13 (Phase-0 status + 8 source-verified corrections).

**Re-verify everything:**
```
cd sandbox && cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist
cd .. && .venv/bin/python backend/scripts/bench_sandbox_e2e.py --docker-image sweevo-dask__dask-10042:latest --eosd-binary sandbox/dist/eosd-linux-amd64 --report bench/local-eosd-amd64-upload.json
cd sandbox && cargo test -p eos-protocol && cargo check --workspace && cargo clippy --workspace && cargo fmt --all --check
cd .. && .venv/bin/python -m pytest backend/tests/unit_test/test_sandbox/test_provider/ backend/tests/unit_test/test_sandbox/test_contract_fixtures_pin.py backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py -q
.venv/bin/python backend/scripts/bench_sandbox_e2e.py --commands 10 --report /tmp/eos-synthetic-bench.json
```

---

## NEXT — ordered, concrete

### A. Phase 0 closeout follow-ups (not blocking local amd64)
1. **Release-grade provenance** — minisign fail-closed verification remains a later AV-8 gate. Current Phase 0 local closeout is SHA-pinned but unsigned by design.
2. **Arm64 baseline leg** — capture `bench/baseline-arm64.json` and an arm64 `local-eosd` upload report on an arm64-native Docker host or explicit local runner. The local `sweevo-dask__dask-10042` image is the amd64 leg.
3. **Minimal-image matrix** — when Phase 1/CP-1b starts, extend local upload checks to non-root and read-only-rootfs images. The current amd64 gate proves the artifact needs no in-image Rust/toolchain and can be uploaded via provider `put_archive`.

**Re-run the amd64 CP baseline when needed:**
   ```
   cd sandbox && cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist
   cd ..
   .venv/bin/python backend/scripts/bench_sandbox_e2e.py \
     --docker-image sweevo-dask__dask-10042:latest \
     --eosd-binary sandbox/dist/eosd-linux-amd64 \
     --report bench/local-eosd-amd64-upload.json
   .venv/bin/python backend/scripts/bench_sandbox_e2e.py \
     --docker-image sweevo-dask__dask-10042:latest \
     --phase0 \
     --commands 10 \
     --report bench/baseline-amd64.json
   ```

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
- **macOS can build/package this pure-Rust static musl amd64 skeleton with `rust-lld`, but cannot validate Linux syscall behavior.** All syscall/overlay/OCC-contention work must be checked in the dask container (PLAN §12.2 recipe) — `cargo check` on macOS only validates the non-Linux `cfg` surface.
- **Not committed.** Treat the worktree as parallel-agent dirty; stage intentionally.
- **CAS byte-identity is the sharpest correctness lever** — any new code computing `manifest_root_hash`/`layer_digest` must pass `fixtures/cas/cases.json` (esp. the unicode cases).
