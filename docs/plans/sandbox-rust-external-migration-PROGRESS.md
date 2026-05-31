# Sandbox → Rust migration — PROGRESS

Living status tracker for `docs/plans/sandbox-rust-external-migration-PLAN.md`.
Spec = PLAN.md. Landed-status snapshot = PLAN §13. This file = done/next checklist.

**Last updated:** 2026-05-31 · **Phase:** 3 direct write/edit publish now has routed `eos-occ` validation; shell/search overlay daemon paths, background registry/control ops, PPC framing/no-OCC plugin edge, LayerStack squash/GC, and CP-4s live harness/evidence are in place; CP-4s shell optimization gate is still failing and plugin warm-server/live gates remain.

---

## Phase status at a glance

| Phase | Scope | Status |
|---|---|---|
| **0 — Bootstrap** | workspace, eos-protocol, put_archive, pins, CP-0/local upload | ✅ **local amd64+arm64 upload closeout complete; signing/full matrix deferred** |
| 1 — ns-runner (fresh-ns) | `eos-runner` unshare→mount→exec | ✅ **scoped direct `eosd ns-runner` closeout complete; host dispatch is Phase 2** |
| 2 — daemon + read paths | `eos-daemon` RPC, read verbs, readiness | ✅ **CP-3/AV-2 closed on local amd64 Docker/dask** |
| 3 — write/publish + shell/search + plugin (HIGH risk) | OCC/LayerStack publish, PPC | 🟡 direct `write_file`/`edit_file` publish flows through routed `eos-occ`; `api.v1.shell`/`glob`/`grep` overlay paths, background registry/control ops, PPC framing/no-OCC plugin edge, LayerStack squash/GC, and CP-4s live evidence are in place; CP-4s optimization + plugin warm-server/live gates remain |
| 3.5 — isolated workspace | ns-holder + setns + shell-free net | ⬜ skeleton only |
| 5 — cutover | flip default, delete Python | ⬜ |

Legend: ✅ done · 🟡 partial · ⬜ not started.

---

## DONE (verified 2026-05-31, all checks re-run independently)

**Rust workspace `/sandbox` — 11 crates + xtask, ~7,800 LOC**
- ✅ `eos-protocol` **fully implemented + tested**: version/envelope/cas/audit/models/canonical. **29 tests green incl 18 executed CAS golden fixtures** (the `ensure_ascii` Unicode trap reproduced).
- ✅ Faithful **skeletons** for layerstack/overlay/occ/ephemeral/isolated/plugin/runner/ns-holder/daemon/eosd — `// PORT backend/…:line` anchors remain the precise later-phase work map; remaining `todo!()` bodies now live mostly in Phase 3.5/plugin/port-adapter skeletons.
- ✅ `cargo check --workspace` green (12 crates) · Phase 1 deny-gate clippy green for `eos-overlay`, `eos-runner`, and `eosd` on host and Linux-musl targets · `cargo fmt --all --check` clean. Later-phase skeleton crates still emit expected unused/dead-code warnings until their bodies land.
- ✅ `xtask package` implemented for `eosd-linux-{amd64,arm64}`: default builder is `rust-lld` (`cargo` with `RUSTFLAGS=-C linker=rust-lld`), with optional `cargo`/`cross`; writes binary-only `SHA256SUMS`, `protocol_version`, per-artifact JSON manifests, and optional minisign `.minisig` signatures. Current artifacts package locally (`amd64` SHA `59c0ae7bc655ba55f59e9d4e228e33340fd6125238d9fc8f4ea1961fd395c7a4`, `arm64` SHA `4a39764bc3e13421a58835bc3294fb8f6f2801b2610690ebbe9e652d0a6c1758`).
- ✅ **Build-time guarantee holds**: `cargo tree -p eos-isolated` has no `eos-occ` edge (direct/transitive), and `cargo tree -p eos-plugin --edges normal` shows no `eos-occ` edge after the `eos-ephemeral` port surface stopped linking OCC directly. HINGE split (`SnapshotLeasePort` vs `CommitTransactionPort` in `eos-layerstack`) + 3 severings wired (`OccServicesInjector` impls both `eos_occ::` and `eos_ephemeral::OccRuntimeServicesPort`, returns the per-root single writer — MF-1-aware).

**Contracts & fixtures (ground truth)**
- ✅ `sandbox/docs/contract/01-06.md` — source-verified wire/CAS/audit/models/provider/crate-map specs.
- ✅ `sandbox/crates/eos-protocol/fixtures/` — 18 CAS cases + envelope/audit/metrics fixtures (executed from real Python).
- ✅ `sandbox/docs/RUST-GUIDANCE.md` — the Rust standard for all builders (incl. exact `ensure_ascii` escaper spec).

**Python-side Phase 0 (surgical; focused sandbox tests passed)**
- ✅ `put_archive` on `ProviderAdapter` Protocol + Docker adapter (async → `container.put_archive`) + Daytona stub.
- ✅ `backend/src/sandbox/host/runtime_artifact/__init__.py` pins the local artifacts: `EOSD_VERSION=0.1.0-local.20260531`, amd64 SHA256 `59c0ae7bc655ba55f59e9d4e228e33340fd6125238d9fc8f4ea1961fd395c7a4`, arm64 SHA256 `4a39764bc3e13421a58835bc3294fb8f6f2801b2610690ebbe9e652d0a6c1758`, protocol version `1`. Minisign remains empty until the later release-provenance gate.
- ✅ `backend/src/sandbox/_contract_fixtures/` vendors the Rust fixtures; `pin.json` is hard-pinned to `2df20649b3158324d1be9c4c6c53a5844034ebc2` with `fixtures_sha256=3d62ff3017bf1b1a76e36de08ea4a3185d9640cb9ca98f7e4a1796b153aab221`; the backend pin assert is hard-fail (no skip).
- ✅ `EOS_SANDBOX_RUNTIME=python|rust` no-op host read exists in `daemon_client.py` and validates values; the actual dispatch fork remains Phase 2.
- ✅ `backend/scripts/bench_sandbox_e2e.py` has Docker-backed Phase 0 mode for CP-0 + CP-1 (`--phase0`) plus local artifact upload verification (`--eosd-binary`) that uses `put_archive`, Docker archive readback, and direct binary exec. `backend/scripts/build_upload_eosd_docker.py` is the narrower build/package/upload script for both arches. Neither path installs `apt`/`pkg` packages or requires Rust/Cargo inside the target sandbox image for the artifact check.
- ✅ GitHub CI is **not** part of the current Phase 0 closeout path. The current path is: build/package locally, then upload the static binary into the sandbox/container.

**Phase 0 CP baseline artifacts**
- ✅ `bench/baseline-amd64.json` captured in `sweevo-dask__dask-10042:latest` (Ubuntu 22.04.4, Python 3.10.14, kernel `6.10.14-linuxkit`, `x86_64`, `/eos-mount-scratch` tmpfs, overlay-in-userns probe green).
- ✅ CP-0 measured: runtime bundle upload `4092.846 ms`; daemon cold-start `885.234 ms`; daemon idle RSS `36,676 KiB`; Python process-start p50 `428.128 ms`; warm heartbeat p50 `1.103 ms`, p95 `1.993 ms`.
- ✅ CP-1 passed: `put_archive` vs base64-over-exec for `1.5 MiB` (`17.260 ms` vs `23,003.217 ms`, 64 chunks) and `3.0 MiB` (`32.196 ms` vs `45,602.537 ms`, 128 chunks); all SHA256s matched; put-archive size ratio `1.865` ≤ `2.5`.
- ✅ `bench/local-eosd-amd64-upload.json` captured the historical Phase 0 bootstrap amd64 handoff: `sandbox/dist/eosd-linux-amd64` (683,328 bytes, static PIE) uploaded to `/tmp/eosd-local/eosd` in `8.121 ms`; readback SHA256 matched `c81993538d4cfb6425e1a00f91d38d0a85dd07a1706907c3b07db6faf5a5629e`; mode `0755`; direct exec returned `eosd 0.1.0`; target `rustc`/`cargo` absent. Current Phase 1 amd64 artifact verification is `bench/phase1-ns-runner-amd64.json`.
- ✅ `bench/local-eosd-arm64-upload.json` captured the historical Phase 0 bootstrap arm64 handoff: `sandbox/dist/eosd-linux-arm64` (597,848 bytes, static aarch64 ELF) uploaded to `/tmp/eosd-local/eosd` in `8.444 ms`; readback SHA256 matched `6edbe7bdc7bb4d6414b2b331d58857b1ce55bcf61bd391f34f34b36bdba716c6`; mode `0755`; direct exec returned `eosd 0.1.0`; target `rustc`/`cargo` absent. Current arm64 artifact is rebuilt and pinned but not re-upload-smoked in this dask-only pass.

**Phase 1 implementation artifacts (local, 2026-05-31)**
- ✅ `eos-overlay::kernel_mount` now validates `O_DIRECTORY|O_NOFOLLOW` inputs, pins lower/upper/work dirs through `/proc/self/fd/*`, calls the raw `fsopen→fsconfig(lowerdir+)→fsconfig(upperdir/workdir)→fsmount→move_mount` sequence, and tears down stacked mounts via RAII drop.
- ✅ `eos-overlay::writable_dirs` now creates the canonical `/eos-mount-scratch/eos-sandbox-runtime` root and per-run `upper`/`work` dirs.
- ✅ `eos-runner` fresh-ns mode now performs best-effort `setsid` (Docker exec may already be process-group leader), `unshare(NEWUSER|NEWNS)`, root uid/gid map setup, private mount propagation, overlay mount guard acquisition, shell command execution with cwd/env policy, timeout kill, and `RunResult` JSON construction. Fast-child wait polling is `5 ms` to avoid an avoidable 100 ms floor.
- ✅ `eosd ns-runner` now reads a `RunRequest` from stdin, `--request PATH`, or one positional request path; writes compact JSON to stdout or `--output PATH`; and wires the runner to the `eos-overlay` mount adapter.
- ✅ Compile/lint checks cover both host and Linux syscall cfg surfaces: host `cargo check --workspace`, host targeted tests, `x86_64-unknown-linux-musl` targeted check, and Linux-target clippy for `eos-overlay`, `eos-runner`, and `eosd`.
- ✅ `bench/phase1-ns-runner-amd64.json` captured direct `eosd ns-runner` in `sweevo-dask__dask-10042:latest` with artifact SHA `f374662b28337575aafb65995c7c3626e4731fc9464cb4ac24bc45ab262acefe`: AV shell smoke green (`hello.txt` read from lower, `generated.txt` captured in upper), timeout cleanup green (non-zero timeout, no lingering `sleep`, no parent-namespace `/testbed` mount leak), and 20/20 perf samples green.
- ✅ CP-2b direct-runner host-wall comparison passed: Rust fresh-ns `true` p50 `361.567 ms`, p95 `373.759 ms` vs refreshed CP-0 Python process-start p50 `428.128 ms` in the same dask image. This is the apples-to-apples direct-runner number: `66.562 ms` faster p50, `15.5%` latency reduction, `1.184×` speedup.
- ✅ CP-2a measured Rust mount-init path passed the ≥20× bar: `workspace.mount_s` p50 `1.076 ms` (`397.8×` faster than CP-0 Python process-start p50). This `397.8×` figure is intentionally **not** an end-to-end tool-call claim: it compares raw Rust/kernel overlay mount initialization (`fsopen→fsconfig→fsmount→move_mount`, no workspace copy) against Python process startup (`python3 -c pass`) in the dask container.
- ✅ Bottleneck interpretation recorded: network is not the main delay in this local dask run. Direct runner host-wall p50 is `361.567 ms`; internal `mount+tool` p50 is `319.288 ms`; raw mount p50 is `1.076 ms`; implied host/Docker/request overhead is about `42.279 ms`. The dominant remaining cost is shell/process startup (`bash -lc true`) under the amd64 dask container, likely amplified by Docker Desktop/emulation.

**Phase 2 implementation artifacts (local, 2026-05-31)**
- ✅ `eos-daemon` now has a real Phase 2 AF_UNIX + Docker-published TCP server: newline-delimited JSON framing, request-size/read-time handling, TCP auth-token stripping, structured error envelopes, `api.runtime.ready`, `api.v1.heartbeat`, `api.layer_metrics`, audit pull/snapshot/reset-floor stubs, and direct `api.v1.read_file` / `api.read_file` LayerStack reads.
- ✅ `eos-layerstack` now has read-side manifest loading, workspace binding translation, merged newest-first read semantics with whiteout/opaque ancestor handling, O(1) snapshot lease plumbing, a process-local dual-layer storage writer lease, and active-lease metrics needed by readiness/layer metrics.
- ✅ `eosd daemon` now starts the Rust daemon, supports `--spawn` for host recovery launches, and supports `--client SOCKET JSON` as the Rust AF_UNIX thin-client replacement preserving the 97/98 connect/I/O exit-code contract.
- ✅ `backend/src/sandbox/host/daemon_client.py` now selects Rust spawn/client commands when `EOS_SANDBOX_RUNTIME=rust`, while Python remains the default. Rust daemon TCP binds `0.0.0.0` inside Docker so the provider's host-loopback port mapping works; stale TCP empty-response/connect-failure paths invalidate the cached endpoint before respawn.
- ✅ Local verification: `.venv/bin/python -m pytest backend/tests/unit_test -q`; `cargo test --workspace`; `cargo check --workspace`; `cargo fmt --all --check`; `cargo clippy -p eos-layerstack -p eos-daemon -p eosd --all-targets`; focused daemon transport/API tests; `.venv/bin/python -m ruff check` and `py_compile` for the Phase 2 harness.
- ✅ Live Docker/dask evidence: `bench/phase2-rust-daemon-amd64.json` uploaded pinned amd64 `eosd` SHA `59c0ae7bc655ba55f59e9d4e228e33340fd6125238d9fc8f4ea1961fd395c7a4` into `sweevo-dask__dask-10042:latest`, launched with `EOS_SANDBOX_RUNTIME=rust`, and closed CP-3/AV-2. Rust daemon spawn was `367.015 ms` vs CP-0 Python `885.234 ms`; idle RSS was `4,112 KiB` vs CP-0 `36,676 KiB`; readiness after spawn was `9.760 ms`; warm TCP heartbeat p50/p95 was `1.173/1.444 ms`. AF_UNIX and TCP both proved `api.runtime.ready`, `api.read_file`/`api.v1.read_file`, `api.v1.heartbeat`, and `api.layer_metrics`. AV-2 killed pid `295`, respawned pid `424`, observed stale TCP `EOS_DAEMON_IO_FAILED:empty_response`, invalidated then repopulated the TCP endpoint cache, left exactly one `eosd daemon` process, and reported zero `eos-sandbox-runtime` mount entries.

**Phase 3 implementation artifacts (local OCC-integrated direct write/edit + overlay shell/search slice, 2026-05-31)**
- 🟡 `eos-layerstack` now has a policy-blind immutable layer publish primitive: aggregate accepted changes, compute the AV-1c `layer_digest`, skip duplicate head-layer writes, write layer bytes/whiteouts/symlinks/opaque markers, persist `.layer-metadata/*.digest`, and atomically temp-rename the active manifest. It also implements merged projection, checkpoint squash planning/build/relabel/rollback, manifest-prefix CAS checks, and lease-release GC that retains leased layers until the final lease drops.
- 🟡 `eos-daemon` now registers `api.write_file` / `api.v1.write_file` and `api.edit_file` / `api.v1.edit_file` on the Rust op table. The handlers translate workspace-bound paths through `workspace.json`, preserve create-only and edit-anchor guards, then publish direct writes/edits through a per-root `eos_occ::OccService<LayerStackCommitTransaction>` single-writer queue. Responses now expose OCC status/timings while retaining the guarded Python-compatible result shape.
- 🟡 `eos-occ::CommitQueue` now has the named single worker, close/drain, submit reply channels, disjoint non-atomic batching, atomic batch isolation, and bounded CAS retry exhaustion to `aborted_version`; `OccService` prepares `.git` drops, DIRECT routes root `.gitignore` matches, attaches GATED base hashes, and routes publishable changes through the queue. The daemon transaction bridge revalidates GATED hashes against current LayerStack bytes, rejects unsupported gated symlinks, drops all accepted paths on atomic validation failure, maps accepted DIRECT/GATED changes into `LayerStack::publish_layer`, and returns published manifest versions.
- 🟡 `eos-overlay::capture_upperdir` now captures upperdir regular-file writes, whiteout deletes, symlinks, and opaque directory markers/xattrs into validated `OverlayPathChange`s. `eos-occ` now consumes the real `eos_overlay::OverlayPathChange` one-way edge and converts it into `LayerChange`s with OCC-owned error wrapping.
- 🟡 `eos-daemon` now registers `api.v1.shell`, `api.glob` / `api.v1.glob`, and `api.grep` / `api.v1.grep`. Shell acquires a LayerStack snapshot lease, allocates overlay upper/work dirs, runs `eosd ns-runner`, captures upperdir changes, computes snapshot base hashes, publishes through OCC, and returns runner stdout/stderr/exit fields plus overlay timing aliases. Glob/grep acquire the same read-only overlay lease and execute in-namespace Rust primitives via the runner without OCC publish.
- 🟡 `eos-runner` now supports fresh-ns `glob` and `grep` in addition to `shell`. The Rust primitives preserve the documented wire shape for sorted/sliced glob results, read-only grep filenames/content/count modes, regex flags, UTF-8/2 MiB skip behavior, inert `head_limit`/`offset`, and workspace escape rejection.
- 🟡 `eos-daemon` now registers `api.v1.cancel`, `api.v1.heartbeat`, and `api.v1.inflight_count` against the server-owned `InFlightRegistry`. Server dispatch registers invocation id / agent id / background flag around handler execution, runs a TTL sweep loop, and the registry tracks active-call guards so stale background entries are not TTL-reaped while a call is active.
- 🟡 `eos-plugin` now has concrete PPC frame encode/decode over the shared `eos_protocol` newline-delimited request envelope, non-panicking warm-server teardown, and real process-local warm-server registry semantics: validate `layer_stack_root`, reuse one handle per root, refresh LRU on cache hits, evict oldest handles at `MAX_WARM_SERVERS`, and drop handles on session end. The crate graph no longer reaches `eos-occ` through `eos-ephemeral`. The actual process-backed warm-server spawn/round-trip and self-managed OCC callback remain open.
- 🟡 `backend/scripts/bench_rust_daemon_phase3.py` now provides the CP-4s live harness: upload/seed/start Rust daemon through the repo Docker provider path, measure `api.v1.shell` no-op, shell small-write publish, `api.v1.glob`, `api.v1.grep`, per-sample phase timings, final small-write readback hash, and daemon memory samples from `/proc/<pid>/smaps_rollup` with RSS fallback. `bench/phase3-rust-daemon-amd64.json` captured a live `sweevo-dask__dask-10042:latest` run with current artifact SHA `03075315d48c3c16fe6dc8a36c6269fd475b92be3a4c230205ef18162f97b22e`: upload/readback gate passed, daemon ready passed, all operation samples succeeded, final small-write readback/hash passed, host/TCP delta p95 `2.373 ms`, mount p95 `1.308 ms`, daemon PSS peak `5,416 KiB`, but CP-4s overall is **false** because the no-op shell process segment p50 is `311.072 ms`, above the required `238.671 ms` (25% faster than Phase 1 `318.228 ms`). The repo Docker provider path is the intended route; the `docker` CLI does not need to be present for this benchmark.
- 🟡 `sandbox/crates/eos-daemon/tests/phase3_write_paths.rs` covers Rust daemon direct write publish + readback, create-only existing-file conflict, edit publish + readback, duplicate-head idempotency, and `.git` path route-dropping. Daemon unit tests cover GATED stale-base abort, DIRECT stale-base publish, atomic validation failure drop, root `.gitignore` routing, in-flight TTL active-call protection, and cancel/heartbeat/count control ops. `eos-runner` unit tests cover glob/grep primitive contract slices. `eos-layerstack` unit tests cover squash read preservation and lease-retained GC. `eos-plugin` unit tests cover PPC frame round-trip/reject paths, mode selection, registration, teardown idempotence, warm-server reuse, root validation, LRU eviction, and LRU hit refresh. `eos-occ` unit tests cover batching, atomic isolation, CAS retry success/exhaustion, and overlay-change conversion. `eos-overlay` unit tests cover upperdir file/delete/symlink/opaque capture. Local checks passed: `cargo fmt --all`; `cargo check -p eos-occ -p eos-overlay -p eos-layerstack -p eos-daemon -p eos-runner`; `cargo test -p eos-runner --lib`; `cargo test -p eos-daemon`; `cargo test -p eos-occ -p eos-overlay -p eos-layerstack`; `cargo check -p eos-ephemeral -p eos-plugin -p eos-daemon`; `cargo test -p eos-ephemeral -p eos-plugin -p eos-daemon`; `cargo clippy -p eos-occ -p eos-overlay -p eos-layerstack -p eos-daemon -p eos-runner --all-targets`; `cargo clippy -p eos-ephemeral -p eos-plugin -p eos-daemon --all-targets`; focused `cargo test -p eos-plugin`; focused `cargo clippy -p eos-plugin --all-targets`; Phase 3 harness `py_compile` + `ruff check` (only pre-existing adjacent skeleton warnings).
- ⬜ This is not Phase 3 closeout: CP-4s is now live-run but failing the shell-segment optimization gate; the harness's final-state readback/hash slice passed, but full CP-4/CP-5 proof is still open. Daemon cancel still needs AV-3 process-tree cleanup proof under live shell/background load. Plugin process-backed warm-server dispatch/self-managed callback, AV-7 forward/back on-disk parity, AV-10 plugin parity, and the §7 differential/property contention gates remain open.

**Docs**
- ✅ PLAN §12 (verified Docker/dask/plugin config) + §13 (Phase-0 status + 8 source-verified corrections).

**Re-verify everything:**
```
.venv/bin/python backend/scripts/build_upload_eosd_docker.py --arch amd64 --image sweevo-dask__dask-10042:latest --report bench/local-eosd-amd64-upload.json
.venv/bin/python backend/scripts/build_upload_eosd_docker.py --arch arm64 --image python:3.11-slim --platform linux/arm64 --report bench/local-eosd-arm64-upload.json
cd sandbox && cargo test -p eos-protocol && cargo check --workspace && cargo clippy --workspace && cargo fmt --all --check
cd sandbox && cargo test -p eos-daemon --test phase2_read_paths && cargo clippy -p eos-layerstack -p eos-daemon -p eosd --all-targets
cd .. && .venv/bin/python -m pytest backend/tests/unit_test/test_sandbox/test_provider/ backend/tests/unit_test/test_sandbox/test_contract_fixtures_pin.py backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py -q
.venv/bin/python backend/scripts/bench_sandbox_e2e.py --commands 10 --report /tmp/eos-synthetic-bench.json
.venv/bin/python backend/scripts/bench_sandbox_e2e.py --docker-image sweevo-dask__dask-10042:latest --phase0 --commands 10 --report bench/baseline-amd64.json
.venv/bin/python backend/scripts/bench_rust_daemon_phase2.py --docker-image sweevo-dask__dask-10042:latest --artifact sandbox/dist/eosd-linux-amd64 --baseline bench/baseline-amd64.json --report bench/phase2-rust-daemon-amd64.json
.venv/bin/python backend/scripts/bench_rust_daemon_phase3.py --docker-image sweevo-dask__dask-10042:latest --artifact sandbox/dist/eosd-linux-amd64 --phase1-baseline bench/phase1-ns-runner-amd64.json --phase0-baseline bench/baseline-amd64.json --report bench/phase3-rust-daemon-amd64.json
# Direct Phase 1 dask evidence is currently captured in bench/phase1-ns-runner-amd64.json.
```

---

## NEXT — ordered, concrete

### A. Phase 0 closeout follow-ups (not blocking local amd64)
1. **Release-grade provenance** — minisign fail-closed verification remains a later AV-8 gate. Current Phase 0 local closeout is SHA-pinned but unsigned by design.
2. **Arm64 CP baseline leg** — `local-eosd` arm64 upload/run is captured; `bench/baseline-arm64.json` CP-0/CP-1 remains for an arm64-native Docker host or explicit local runner. The local `sweevo-dask__dask-10042` image is the amd64 CP baseline leg.
3. **Minimal-image matrix** — when Phase 1/CP-1b starts, extend local upload checks to non-root and read-only-rootfs images. The current amd64 gate proves the artifact needs no in-image Rust/toolchain and can be uploaded via provider `put_archive`.

**Re-run the amd64 CP baseline when needed:**
   ```
   .venv/bin/python backend/scripts/build_upload_eosd_docker.py \
     --arch amd64 \
     --image sweevo-dask__dask-10042:latest \
     --report bench/local-eosd-amd64-upload.json
   .venv/bin/python backend/scripts/build_upload_eosd_docker.py \
     --arch arm64 \
     --image python:3.11-slim \
     --platform linux/arm64 \
     --report bench/local-eosd-arm64-upload.json
   .venv/bin/python backend/scripts/bench_sandbox_e2e.py \
     --docker-image sweevo-dask__dask-10042:latest \
     --phase0 \
     --commands 10 \
     --report bench/baseline-amd64.json
   ```

### B. Phase 1 closeout guardrails
- Treat Phase 1 as closed for the scoped direct `eosd ns-runner` fresh-ns boundary. Keep `bench/phase1-ns-runner-amd64.json` as the direct-runner dask evidence until a checked-in Phase 1 harness exists.
- Do not flip the global default to `EOS_SANDBOX_RUNTIME=rust` from Phase 1 alone. Phase 2 now proves persistent daemon routing and endpoint readiness for the read path, but the global default flip still waits for the later cutover gates.
- Remaining scope clarification: setns mode stays Phase 3.5. Current `eosd ns-runner` is an executable request/response subcommand for the fresh path, not a full daemon runtime cutover.

### C. Phase 2 — daemon + read paths
- ✅ Closed by `bench/phase2-rust-daemon-amd64.json`. Keep write/publish, shell/search, plugin, and isolated mode out of the Phase 2 result; those remain Phase 3/3.5 gates.

### D. Phase 3 (HIGH risk) — write/publish + OCC/LayerStack + plugin PPC
- Continue from the landed routed OCC write/edit validation + structural shell/search overlay paths + background registry/control ops + PPC framing/no-OCC plugin edge + LayerStack squash/GC into Linux Docker/dask live verification, AV-3 process-tree cancel proof, and `eos-plugin` warm-server dispatch/self-managed callback + MF-1 single-writer routing.
- Continue CP-4s optimization from `bench/phase3-rust-daemon-amd64.json`: live `api.v1.shell` no-op host-wall already beats Phase 1 (`327.041/337.948 ms` p50/p95 vs `361.567/373.759 ms`) and mount/host-delta/memory gates pass, but `command_exec.run_command_s` p50 is `311.072 ms`, not the required `<=238.671 ms`. The next optimization should target the shell/process segment or add a separate direct-argv fast-path report without weakening shell-compatible semantics.
- Use the CP-4s daemon memory profile from that harness: sample `/proc/<pid>/smaps_rollup` before load, between operation groups, and after drain. Daemon PSS is the primary "total daemon memory" metric; RSS is the fallback. The harness reports private memory (`Private_Clean + Private_Dirty`), non-gating `VmSize`, and cgroup `memory.current` when available.
- Shell optimization pass criteria: no-op shell host-wall p50/p95 must be no worse than Phase 1 CP-2b (`361.567 ms` p50, `373.759 ms` p95 in the same image); the process/shell segment must improve by at least 25% versus Phase 1 `runner_tool_ms` p50 (`318.228 ms`) before we call it optimized; overlay mount p95 stays <= `5 ms`; TCP/daemon dispatch p95 stays <= `5 ms`; Rust daemon active PSS p95/peak must be no worse than the CP-0 Python daemon active PSS p95/peak under the same load, and idle-after PSS must return within max(10%, 2 MiB) of idle-before. If `smaps_rollup` is unavailable, RSS is the fallback gate and the report must say so; direct-argv/no-shell fast-path numbers, if added, are reported separately from shell-compatible semantics.
- Gate: CP-4s shell hot-path + CP-4 (final-workspace-state hash) + the **§7 differential/property tests under contention** (NOT fixtures) + AV-1c byte-identity + AV-7 forward/back on-disk parity + AV-10 plugin parity. Needs the Python differential harness.

### E. Phase 3.5 (isolated) then Phase 5 (cutover) — per PLAN §5.

---

## Notes / risks for next session
- **Skeletons are not logic.** Remaining `todo!()` bodies plus `// PORT` anchors are the precise work-list; each cites the exact Python `file:line` to port.
- **macOS can build/package this pure-Rust static musl amd64 skeleton with `rust-lld`, but cannot validate Linux syscall behavior.** All syscall/overlay/OCC-contention work must be checked in the dask container (PLAN §12.2 recipe) — `cargo check` on macOS only validates the non-Linux `cfg` surface.
- **Not committed.** Treat the worktree as parallel-agent dirty; stage intentionally.
- **CAS byte-identity is the sharpest correctness lever** — any new code computing `manifest_root_hash`/`layer_digest` must pass `fixtures/cas/cases.json` (esp. the unicode cases).
