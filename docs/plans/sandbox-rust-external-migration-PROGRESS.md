# Sandbox → Rust migration — PROGRESS

Living status tracker for `docs/plans/sandbox-rust-external-migration-PLAN.md`.
Spec = PLAN.md. Landed-status snapshot = PLAN §13. This file = done/next checklist.

**Last updated:** 2026-06-01 · **Phase:** 3 is closed at the structural core boundary: direct write/edit publish routes through `eos-occ`; shell/search overlay daemon paths, background registry/control ops, PPC framing/no-OCC plugin edge, LayerStack squash/GC, and CP-4s structural live evidence are in place. Phase 3T has closed CP-4t for the Docker shared-workspace path under the final `exec_command` / PTY tool names and `/eos` runtime paths. Remaining Phase 3T work is typed subagent controls, Rust isolated-workspace command/PTY routing, process-backed plugin PPC, CP-4/CP-5, AV-4/AV-7/AV-10, and the §7 differential/property gates.

---

## Phase status at a glance

| Phase | Scope | Status |
|---|---|---|
| **0 — Bootstrap** | workspace, eos-protocol, put_archive, pins, CP-0/local upload | ✅ **local amd64+arm64 upload closeout complete; signing/full matrix deferred** |
| 1 — ns-runner (fresh-ns) | `eos-runner` unshare→mount→exec | ✅ **scoped direct `eosd ns-runner` closeout complete; host dispatch is Phase 2** |
| 2 — daemon + read paths | `eos-daemon` RPC, read verbs, readiness | ✅ **CP-3/AV-2 closed on local amd64 Docker/dask** |
| 3 — write/publish + shell/search + background control core | OCC/LayerStack publish, structural shell/search, PPC scaffolding | ✅ **closed at the structural boundary:** direct `write_file`/`edit_file` publish flows through routed `eos-occ`; `api.v1.shell`/`glob`/`grep` overlay paths, background registry/control ops, PPC framing/no-OCC plugin edge, LayerStack squash/GC, and CP-4s structural live evidence are in place |
| 3T — terminal sessions + deferred Phase 3 gates | non-login Bash shell/session tools, typed background/subagent controls, plugin PPC execution, CP-4/CP-5/AV gates | 🟡 **partial:** CP-4t is closed for Docker shared-workspace command/PTY paths under final `/eos` runtime paths; next non-plugin items are typed subagent controls, Rust isolated-workspace command/PTY routing, CP-4 mixed non-plugin load with AV-4 audit pull, CP-5 cache-lock churn, AV-7 parity, and §7 differential/property |
| 3.5 — isolated workspace | ns-holder + setns + shell-free net | ⬜ skeleton only |
| 5 — cutover | flip default, delete Python | ⬜ |

Legend: ✅ done · 🟡 partial · ⬜ not started.

---

## DONE (verified 2026-05-31, all checks re-run independently)

**Rust workspace `/sandbox` — 11 crates + xtask, ~7,800 LOC**
- ✅ `eos-protocol` **fully implemented + tested**: version/envelope/cas/audit/models/canonical. **29 tests green incl 18 executed CAS golden fixtures** (the `ensure_ascii` Unicode trap reproduced).
- ✅ Faithful **skeletons** for layerstack/overlay/occ/ephemeral/isolated/plugin/runner/ns-holder/daemon/eosd — `// PORT backend/…:line` anchors remain the precise later-phase work map; remaining `todo!()` bodies now live mostly in Phase 3.5/plugin/port-adapter skeletons.
- ✅ `cargo check --workspace` green (12 crates) · Phase 1 deny-gate clippy green for `eos-overlay`, `eos-runner`, and `eosd` on host and Linux-musl targets · `cargo fmt --all --check` clean. Later-phase skeleton crates still emit expected unused/dead-code warnings until their bodies land.
- ✅ `xtask package` implemented for `eosd-linux-{amd64,arm64}`: default builder is `rust-lld` (`cargo` with `RUSTFLAGS=-C linker=rust-lld`), with optional `cargo`/`cross`; writes binary-only `SHA256SUMS`, `protocol_version`, per-artifact JSON manifests, and optional minisign `.minisig` signatures. Current artifacts package locally (`amd64` SHA `ade88b2700f0c4894a08adc98e2a37dfc17deda0d614da465138a2bb6e5d525f`, `arm64` SHA `4a39764bc3e13421a58835bc3294fb8f6f2801b2610690ebbe9e652d0a6c1758`).
- ✅ **Build-time guarantee holds**: `cargo tree -p eos-isolated` has no `eos-occ` edge (direct/transitive), and `cargo tree -p eos-plugin --edges normal` shows no `eos-occ` edge after the `eos-ephemeral` port surface stopped linking OCC directly. HINGE split (`SnapshotLeasePort` vs `CommitTransactionPort` in `eos-layerstack`) + 3 severings wired (`OccServicesInjector` impls both `eos_occ::` and `eos_ephemeral::OccRuntimeServicesPort`, returns the per-root single writer — MF-1-aware).

**Contracts & fixtures (ground truth)**
- ✅ `sandbox/docs/contract/01-06.md` — source-verified wire/CAS/audit/models/provider/crate-map specs.
- ✅ `sandbox/crates/eos-protocol/fixtures/` — 18 CAS cases + envelope/audit/metrics fixtures (executed from real Python).
- ✅ `sandbox/docs/RUST-GUIDANCE.md` — the Rust standard for all builders (incl. exact `ensure_ascii` escaper spec).

**Python-side Phase 0 (surgical; focused sandbox tests passed)**
- ✅ `put_archive` on `ProviderAdapter` Protocol + Docker adapter (async → `container.put_archive`) + Daytona stub.
- ✅ `backend/src/sandbox/host/runtime_artifact/__init__.py` pins the local artifacts: `EOSD_VERSION=0.1.0-local.20260531`, amd64 SHA256 `ade88b2700f0c4894a08adc98e2a37dfc17deda0d614da465138a2bb6e5d525f`, arm64 SHA256 `4a39764bc3e13421a58835bc3294fb8f6f2801b2610690ebbe9e652d0a6c1758`, protocol version `1`. Minisign remains empty until the later release-provenance gate.
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
- ✅ Bottleneck interpretation recorded: network is not the main delay in this local dask run. Direct runner host-wall p50 is `361.567 ms`; internal `mount+tool` p50 is `319.288 ms`; raw mount p50 is `1.076 ms`; implied host/Docker/request overhead is about `42.279 ms`. The first optimization split removed the Python/wrapper shell-string cost from the low-level daemon primitive; the chosen model-facing shell engine is now the container's native `/bin/bash` plus PTY, measured separately below.

**Phase 2 implementation artifacts (local, 2026-05-31)**
- ✅ `eos-daemon` now has a real Phase 2 AF_UNIX + Docker-published TCP server: newline-delimited JSON framing, request-size/read-time handling, TCP auth-token stripping, structured error envelopes, `api.runtime.ready`, `api.v1.heartbeat`, `api.layer_metrics`, audit pull/snapshot/reset-floor stubs, and direct `api.v1.read_file` / `api.read_file` LayerStack reads.
- ✅ `eos-layerstack` now has read-side manifest loading, workspace binding translation, merged newest-first read semantics with whiteout/opaque ancestor handling, O(1) snapshot lease plumbing, a process-local dual-layer storage writer lease, and active-lease metrics needed by readiness/layer metrics.
- ✅ `eosd daemon` now starts the Rust daemon, supports `--spawn` for host recovery launches, and supports `--client SOCKET JSON` as the Rust AF_UNIX thin-client replacement preserving the 97/98 connect/I/O exit-code contract.
- ✅ `backend/src/sandbox/host/daemon_client.py` now selects Rust spawn/client commands when `EOS_SANDBOX_RUNTIME=rust`, while Python remains the default. Rust daemon TCP binds `0.0.0.0` inside Docker so the provider's host-loopback port mapping works; stale TCP empty-response/connect-failure paths invalidate the cached endpoint before respawn.
- ✅ Local verification: `.venv/bin/python -m pytest backend/tests/unit_test -q`; `cargo test --workspace`; `cargo check --workspace`; `cargo fmt --all --check`; `cargo clippy -p eos-layerstack -p eos-daemon -p eosd --all-targets`; focused daemon transport/API tests; `.venv/bin/python -m ruff check` and `py_compile` for the Phase 2 harness.
- ✅ Live Docker/dask evidence: `bench/phase2-rust-daemon-amd64.json` uploaded pinned amd64 `eosd` SHA `59c0ae7bc655ba55f59e9d4e228e33340fd6125238d9fc8f4ea1961fd395c7a4` into `sweevo-dask__dask-10042:latest`, launched with `EOS_SANDBOX_RUNTIME=rust`, and closed CP-3/AV-2. Rust daemon spawn was `367.015 ms` vs CP-0 Python `885.234 ms`; idle RSS was `4,112 KiB` vs CP-0 `36,676 KiB`; readiness after spawn was `9.760 ms`; warm TCP heartbeat p50/p95 was `1.173/1.444 ms`. AF_UNIX and TCP both proved `api.runtime.ready`, `api.read_file`/`api.v1.read_file`, `api.v1.heartbeat`, and `api.layer_metrics`. AV-2 killed pid `295`, respawned pid `424`, observed stale TCP `EOS_DAEMON_IO_FAILED:empty_response`, invalidated then repopulated the TCP endpoint cache, left exactly one `eosd daemon` process, and reported zero `eos-sandbox-runtime` mount entries.

**Phase 3 implementation artifacts (closed structural direct write/edit + overlay shell/search slice, 2026-05-31/2026-06-01)**
- 🟡 `eos-layerstack` now has a policy-blind immutable layer publish primitive: aggregate accepted changes, compute the AV-1c `layer_digest`, skip duplicate head-layer writes, write layer bytes/whiteouts/symlinks/opaque markers, persist `.layer-metadata/*.digest`, and atomically temp-rename the active manifest. It also implements merged projection, checkpoint squash planning/build/relabel/rollback, manifest-prefix CAS checks, and lease-release GC that retains leased layers until the final lease drops.
- 🟡 `eos-daemon` now registers `api.write_file` / `api.v1.write_file` and `api.edit_file` / `api.v1.edit_file` on the Rust op table. The handlers translate workspace-bound paths through `workspace.json`, preserve create-only and edit-anchor guards, then publish direct writes/edits through a per-root `eos_occ::OccService<LayerStackCommitTransaction>` single-writer queue. Responses now expose OCC status/timings while retaining the guarded Python-compatible result shape.
- 🟡 `eos-occ::CommitQueue` now has the named single worker, close/drain, submit reply channels, disjoint non-atomic batching, atomic batch isolation, and bounded CAS retry exhaustion to `aborted_version`; `OccService` prepares `.git` drops, DIRECT routes root `.gitignore` matches, attaches GATED base hashes, and routes publishable changes through the queue. The daemon transaction bridge revalidates GATED hashes against current LayerStack bytes, rejects unsupported gated symlinks, drops all accepted paths on atomic validation failure, maps accepted DIRECT/GATED changes into `LayerStack::publish_layer`, and returns published manifest versions.
- 🟡 `eos-overlay::capture_upperdir` now captures upperdir regular-file writes, whiteout deletes, symlinks, and opaque directory markers/xattrs into validated `OverlayPathChange`s. `eos-occ` now consumes the real `eos_overlay::OverlayPathChange` one-way edge and converts it into `LayerChange`s with OCC-owned error wrapping.
- ✅ `eos-daemon` now registers `api.v1.shell`, `api.glob` / `api.v1.glob`, and `api.grep` / `api.v1.grep`. The current daemon shell primitive acquires a LayerStack snapshot lease, allocates overlay upper/work dirs, accepts only a raw argv command wire shape, runs `eosd ns-runner`, captures upperdir changes, computes snapshot base hashes, publishes through OCC, and returns runner stdout/stderr/exit fields plus overlay timing aliases. This closes the CP-4s structural smoke only; Phase 3T replaces the model-facing shell path with non-login Bash and does not use raw argv for CP-4 throughput/contention. Glob/grep acquire the same read-only overlay lease and execute in-namespace Rust primitives via the runner without OCC publish.
- 🟡 `eos-runner` now supports fresh-ns `glob` and `grep` in addition to `shell`. The Rust primitives preserve the documented wire shape for sorted/sliced glob results, read-only grep filenames/content/count modes, regex flags, UTF-8/2 MiB skip behavior, inert `head_limit`/`offset`, and workspace escape rejection.
- 🟡 `eos-daemon` now registers `api.v1.cancel`, `api.v1.heartbeat`, and `api.v1.inflight_count` against the server-owned `InFlightRegistry`. Server dispatch registers invocation id / agent id / background flag around handler execution, runs a TTL sweep loop, and the registry tracks active-call guards so stale background entries are not TTL-reaped while a call is active.
- 🟡 `eos-plugin` now has concrete PPC frame encode/decode over the shared `eos_protocol` newline-delimited request envelope, non-panicking warm-server teardown, and real process-local warm-server registry semantics: validate `layer_stack_root`, reuse one handle per root, refresh LRU on cache hits, evict oldest handles at `MAX_WARM_SERVERS`, and drop handles on session end. The crate graph no longer reaches `eos-occ` through `eos-ephemeral`. The actual process-backed warm-server spawn/round-trip and self-managed OCC callback remain open.
- ✅ `backend/scripts/bench_rust_daemon_phase3.py` now provides the CP-4s live harness: upload/seed/start Rust daemon through the repo Docker provider path, seed the LayerStack base layer from the image's real `/testbed` workspace so overlay-mounted argv commands see the Dask checkout, measure canonical raw-argv `api.v1.shell` no-op, argv small-write publish, `api.v1.glob`, `api.v1.grep`, 1/3/5/10 concurrent raw-argv load waves, per-sample phase timings, final small-write readback hash, and daemon memory samples from `/proc/<pid>/smaps_rollup` with RSS fallback. Fresh `bench/phase3-rust-daemon-amd64.json` captured `sweevo-dask__dask-10042:latest` run `local-c30761120c9b` with artifact SHA `51854681d6de0d36d24b75dfb58b194f22088234329be27e57cdf8546ee19f63`: raw-argv `["true"]` passed (`host-wall` p50/p95 `31.477/31.831 ms`; `command_exec.run_command_s` p50/p95 `16.296/16.786 ms` vs required p50 `<=95.468 ms`; mount p95 `1.327 ms`; host-minus-api p95 `2.644 ms`). The 1/3/5/10 structural load matrix also passed: no-op host p95 `31.783/42.414/71.444/108.253 ms`, unique `touch` write host p95 `32.088/43.931/74.553/140.142 ms`, all well below Phase 1 host p95 `373.759 ms`; peak daemon PSS/RSS was `5,456/6,048 KiB`, with idle-return gate true. The repo Docker provider path is the intended route; the `docker` CLI does not need to be present for this benchmark.
- ✅ Native container Bash/PTY viability was measured in the same Dask image family before implementation lock-in. Non-overlay process microbenchmarks remain diagnostic only; overlay-inclusive Bash/PTY measurements are accepted as Phase 3T design evidence, but not as Phase 3T closeout until the real `exec_command`/`write_stdin_exec_command` tools exist. Existing evidence includes `bench/phase3-overlay-bash-microbench-amd64.json` and `bench/phase3-overlay-pty-bash-microbench-amd64.json`: raw argv `true` host p50/p95 `31.918/34.731 ms`, Bash `--noprofile --norc -c true` host p50/p95 `43.940/45.100 ms`, Bash `--noprofile --norc -i -c true` host p50/p95 `43.801/46.312 ms`, Bash write+publish host p50/p95 `43.329/47.303 ms`, and `script(1)` PTY-proxy Bash host p50/p95 `79.735/83.213 ms` (`81.413/88.942 ms` for `-i -c`). All of these go through `api.v1.shell` with LayerStack snapshot lease, overlay mount, capture, OCC publish/cleanup, and release. The PTY-proxy run is conservative because Rust `openpty` session management is not implemented yet and `script(1)` adds wrapper overhead.
- 🟡 `sandbox/crates/eos-daemon/tests/phase3_write_paths.rs` covers Rust daemon direct write publish + readback, create-only existing-file conflict, edit publish + readback, duplicate-head idempotency, and `.git` path route-dropping. Daemon unit tests cover shell argv-only validation, GATED stale-base abort, DIRECT stale-base publish, atomic validation failure drop, root `.gitignore` routing, in-flight TTL active-call protection, and cancel/heartbeat/count control ops. `eos-runner` unit tests cover glob/grep primitive contract slices. `eos-layerstack` unit tests cover squash read preservation and lease-retained GC. `eos-plugin` unit tests cover PPC frame round-trip/reject paths, mode selection, registration, teardown idempotence, warm-server reuse, root validation, LRU eviction, and LRU hit refresh. `eos-occ` unit tests cover batching, atomic isolation, CAS retry success/exhaustion, and overlay-change conversion. `eos-overlay` unit tests cover upperdir file/delete/symlink/opaque capture. Local checks passed: `cargo fmt --all`; `cargo check -p eos-occ -p eos-overlay -p eos-layerstack -p eos-daemon -p eos-runner`; `cargo test -p eos-runner --lib`; `cargo test -p eos-daemon`; `cargo test -p eos-occ -p eos-overlay -p eos-layerstack`; `cargo check -p eos-ephemeral -p eos-plugin -p eos-daemon`; `cargo test -p eos-ephemeral -p eos-plugin -p eos-daemon`; `cargo clippy -p eos-occ -p eos-overlay -p eos-layerstack -p eos-daemon -p eos-runner --all-targets`; `cargo clippy -p eos-ephemeral -p eos-plugin -p eos-daemon --all-targets`; focused `cargo test -p eos-plugin`; focused `cargo clippy -p eos-plugin --all-targets`; focused `cargo test -p eos-daemon shell_command`; focused `cargo check -p eos-daemon`; Phase 3 harness `py_compile` + `ruff check` (only pre-existing adjacent skeleton warnings).
- ✅ Phase 3 closeout boundary: CP-4s raw-argv structural performance/load evidence is green, direct write/edit publish and shell/search overlay paths have focused Rust coverage, background active-call TTL protection is covered, and PPC framing/registry/mode-selection scaffolding is covered. Deferred to Phase 3T: full `exec_command`/`write_pty_command_stdin` implementation, CP-4t proof, CP-4/CP-5 proof, AV-3 process-tree cleanup under live shell/background load, plugin process-backed warm-server dispatch/self-managed callback, AV-7 forward/back on-disk parity, AV-10 plugin parity, and the §7 differential/property contention gates.

**Phase 3T CP-4t closeout artifacts (Docker shared workspace, 2026-06-01)**
- ✅ Model-facing command tools now use the final names: `exec_command`, `write_pty_command_stdin`, `check_pty_command_progress`, and `cancel_pty_command`. Rust daemon ops are registered for `api.v1.exec_command`, PTY controls, and the completion collector. The public `exec_command` boundary accepts a shell-format `cmd` string and rejects raw argv; raw-argv evidence remains historical CP-4s structural evidence only.
- ✅ CP-4t artifact of record: `bench/phase3t-pty-command-docker-20260601-current-eos-paths-post-notify.json` passed with runtime upload to `/eos/daemon/eosd`, `layer_stack_root=/eos/layer-stack`, workspace root `/testbed`, and correctness gates for stdout/stderr split, explicit Dask command PATH, finite write publish/readback, finite `tty=false` descendant cleanup, and PTY `tty=true` descendant cleanup.
- ✅ Later timeout/cancel verification superseded the post-notify runtime hash: `bench/phase3t-pty-command-docker-20260601-current-eos-paths-timeout-cancel-fix.json` passed with amd64 SHA `cb949fce52784b6f7634589a707f54f40f01f75051bc7259832bc2fee63c54bf`. Operation p95s were finite `exec_command(tty=false)` `43.047 ms`, `exec_command(tty=true)` `48.337 ms`, `check_pty_command_progress` `1.781 ms`, `write_pty_command_stdin` `53.733 ms`, `cancel_pty_command` `55.796 ms`, and cancel cleanup `381.024 ms`.
- ✅ Full tiered Docker summaries passed for both the sidecar-minimum Rust scratch run `.omc/results/progressive-test-summary-phase3t-rust-scratch-full-final-20260601.jsonl` and the later `/eos` timeout/cancel run `.omc/results/progressive-test-summary-phase3t-current-eos-paths-timeout-cancel-fix-tier0-6-20260601.jsonl`; tiers 0-6 all reported `status=passed` with `failed_cells=0`.
- ✅ The accepted CP-4t samples go through the shared workspace overlay path: LayerStack workspace-base/binding on `/eos/layer-stack`, overlay command execution in `/testbed`, capture, OCC publish or discard, cleanup, and lease release. No model-facing raw-argv performance gate remains for Phase 3T.

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
.venv/bin/python backend/scripts/bench_rust_daemon_phase3.py --docker-image sweevo-dask__dask-10042:latest --artifact sandbox/dist/eosd-linux-amd64 --phase1-baseline bench/phase1-ns-runner-amd64.json --phase0-baseline bench/baseline-amd64.json --report bench/phase3-rust-daemon-amd64.json --samples 10 --load-concurrency 1,3,5,10 --load-rounds 10
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

### D. Phase 3 — closed structural core
- ✅ Closed at the structural boundary. The landed slice covers routed OCC write/edit validation, shell/search overlay daemon paths, background registry/control ops, PPC framing/no-OCC plugin edge, LayerStack squash/GC, and CP-4s structural live evidence.
- CP-4s raw-argv live evidence is green and retained as historical structural evidence only: `bench/phase3-rust-daemon-amd64.json` run `local-c30761120c9b` cleared the 70% target (`command_exec.run_command_s` p50 `16.296 ms` vs required `<=95.468 ms`), `host-wall` p50/p95 was `31.477/31.831 ms`, concurrent no-op host p95 was `31.783/42.414/71.444/108.253 ms` at 1/3/5/10, and concurrent unique `touch` host p95 was `32.088/43.931/74.553/140.142 ms`. This closes CP-4s, not CP-4 throughput/contention.
- The next shell contract no longer gates on raw argv. CP-4 and CP-4t must run against non-login Bash shell strings with overlay/OCC included.

### E. Phase 3T — remaining ordered implementation and deferred gate closeout
1. **CP-4t is closed for Docker shared-workspace command/PTY paths.** Keep `bench/phase3t-pty-command-docker-20260601-current-eos-paths-post-notify.json`, `bench/phase3t-pty-command-docker-20260601-current-eos-paths-timeout-cancel-fix.json`, `bench/phase3t-pty-command-docker-20260601-review-cleanup.json`, and the tiered summaries above as the command/session evidence. Do not reintroduce model-facing raw argv gates.
2. **Replace generic subagent background semantics with typed surfaces.** Keep `run_subagent(agent_name, prompt)` as launch but return a model-facing `subagent_session_id`; expose `check_subagent_progress(subagent_session_id, last_n_messages)` and `cancel_subagent(subagent_session_id)`; keep internal background records private.
3. **Implement Rust isolated-workspace command/PTY routing.** Register Rust isolated-workspace lifecycle/status ops, route `exec_command` through the active isolated handle for the calling `agent_id`, keep PTY sessions from allowing isolated exit unless force-cancelled, and prove isolated writes stay private until exit discards scratch state.
4. **Run CP-4 mixed non-plugin load with AV-4 audit pull.** Cover read/write/edit, `exec_command(tty=false)`, `exec_command(tty=true)`, search/glob/grep, and LayerStack maintenance under read-heavy, write-heavy, conflict-heavy, PTY long-session/input, and mixed shared-workspace load. Keep plugin operations out of this sidecar gate.
5. **Run CP-5 cache-lock churn.** Drive more than 256 distinct `layer_stack_root` values through Rust OCC runtime services and record cache-lock wait, service create/reuse, eviction, and publish latency.
6. **Run AV-7 forward/back parity and §7 non-plugin differential/property contention.** Compare Python/Rust on-disk state both directions, then drive identical non-plugin operation sequences through both runtimes with conflict, atomic multi-path, delete/whiteout, symlink, no-op capture, squash/GC, and PTY finalization cases.
7. **Finish plugin PPC execution outside the non-plugin sidecar.** Implement process-backed warm-server spawn/round-trip, READ_ONLY out-of-process dispatch, WRITE_ALLOWED eosd-owned overlay+OCC wrapping, and self-managed plugin OCC callback over PPC. MF-1 remains load-bearing: plugin callbacks route through the same per-root OCC writer and storage lock as primary publishes.
8. **Refresh architecture docs only where surfaces change.** If tool names, terminal-session lifecycle, background identifiers, isolated-workspace routing, or plugin-dispatch ownership change, update the smallest affected `docs/architecture` page alongside the implementation.

### F. Phase 3.5 (isolated) then Phase 5 (cutover) — per PLAN §5.

---

## Notes / risks for next session
- **Skeletons are not logic.** Remaining `todo!()` bodies plus `// PORT` anchors are the precise work-list; each cites the exact Python `file:line` to port.
- **macOS can build/package this pure-Rust static musl amd64 skeleton with `rust-lld`, but cannot validate Linux syscall behavior.** All syscall/overlay/OCC-contention work must be checked in the dask container (PLAN §12.2 recipe) — `cargo check` on macOS only validates the non-Linux `cfg` surface.
- **Not committed.** Treat the worktree as parallel-agent dirty; stage intentionally.
- **CAS byte-identity is the sharpest correctness lever** — any new code computing `manifest_root_hash`/`layer_digest` must pass `fixtures/cas/cases.json` (esp. the unicode cases).
