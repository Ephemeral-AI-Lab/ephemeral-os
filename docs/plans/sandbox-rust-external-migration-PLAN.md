# Sandbox In-Sandbox Runtime → Rust External Project — Migration Plan

**Mode:** RALPLAN-DR DELIBERATE. **Status:** APPROVED (consensus iteration 3 — Architect APPROVE + Critic APPROVE; all PV-1/2/3 + M1–M5 closed against source).
**Plan file:** `docs/plans/sandbox-rust-external-migration-PLAN.md`
**External project root:** `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox` (sibling to `backend/`, currently empty — to be created).

Prior context (read, do not relitigate): `docs/plans/sandbox-daemon-go-vs-rust-comparison.md` (verdict: Rust for both) and `docs/plans/sandbox-daemon-go-vs-rust-comparison-REVIEW.md` (confirms Rust, demotes the namespace-capability argument, names `put_archive` as the criterion-4 lever and sequences it first, flags every perf number as in-sandbox-UNMEASURED). Existing perf infra to reuse: `backend/scripts/bench_sandbox_e2e.py` and `docs/plans/sandbox_perf_experiments_PLAN.md`.

---

## 0. Scope and settled context

**Migrating (Python → Rust, in-sandbox-resident only, ~19,474 LOC):**

| Module | LOC (verified) | Risk | What it owns |
| --- | --- | --- | --- |
| `daemon/` | 3,439 | MED | RPC server, dispatcher, in-flight tracking |
| `overlay/` | 1,873 | MED | namespace runner + raw-syscall overlay mount (`kernel_mount.py`) |
| `occ/` | 2,694 | **HIGH** | optimistic concurrency control |
| `layer_stack/` | 2,522 | **HIGH** | lease semantics / squash / GC |
| `shared/` | 2,278 | MED | read/write/edit/shell/search verbs + models |
| `ephemeral_workspace/` | 3,147 | MED | overlay pipeline / publish / plugin dispatch |
| `audit/` | 650 | LOW | audit ring buffer / pull |
| `isolated_workspace/` | 2,871 | **HIGH (NOW IN-SCOPE — iteration 4)** | `setns` existing-namespace path + persistent-ns holder (`scripts/ns_holder.py`, `setns_exec.py`, `setns_overlay_mount.py`, `_setns_libc.py`, `_control_plane/namespace_runtime.py`). Used on BYO/minimal images that lack Python — so it CANNOT stay on the Python `setns` path; `eosd` must support it with no Python in image. → **Phase 3.5**. |
| **In-sandbox total** | **19,474** | — | (16,603 prior + 2,871 isolated_workspace) |

**Also part of the port (otherwise in-image Python + shell survive on the local-fallback path — PV-2):** the daemon's in-image launch + thin-client glue, not just `daemon/` core:
- `daemon/scripts/launch_daemon.sh` (in-image shell launcher) — replaced by direct `eosd daemon` exec.
- `daemon/scripts/thin_client.py` + the `sh -c` python launcher (`daemon_client.py:595-666`, confirmed `_daemon_thin_client_command`) — the Rust **host-side AF_UNIX local-fallback connector** must reproduce the thin-client wire path AND the exit-code contract: **97 = CONNECT_FAILED** (`_THIN_CLIENT_CONNECT_FAILED`), **98 = IO_FAILED** (`_THIN_CLIENT_IO_FAILED`) (both confirmed `daemon_client.py:37-38`).
- `daemon/scripts/install_git.sh` (invoked at `host/bootstrap.py:246`, confirmed) — the git-bootstrap shell dependency; either dropped or replaced shell-free for a truly Python/shell-free image.

**Host stays Python, but two host files are ADAPTED (not just edited):**
- `backend/src/sandbox/host/isolated_workspace_lifecycle.py` (278 LOC, confirmed) — flips from untouched → **ADAPTED** to drive the Rust isolated lifecycle (enter/exit RPCs against `eosd`, preserving its host-only concerns: enter-gate on active background work, exit-drain, the `lifecycle_operation` audit wrapper).
- `backend/src/sandbox/host/daemon_client.py` — the AF_UNIX local-fallback connector + `EOS_SANDBOX_RUNTIME` dispatch fork (PV-2).

**NOT migrating (stays Python — out of scope except the named edits):** `backend/src/sandbox/api/` (`api.v1.*` contract), `backend/src/sandbox/provider/` (Docker adapter — gains `put_archive` + signature-verify), rest of `backend/src/sandbox/host/` (gains binary upload + launch, the `EOS_SANDBOX_RUNTIME` dispatch fork, the local-fallback connector, and signature verification; drops the Python-candidate probe at Phase 5). Engine, TaskCenter, `tools/_framework` entirely out of scope.

**Settled decisions (one-line rationale; not reopened):**
- **Rust for both** daemon and ns-runner — smallest static-musl artifact + smallest dependency surface (REVIEW §6). Per-call/RSS wins are Python-vs-compiled, shared with Go; they justify *leaving Python*, not Rust-over-Go.
- **One binary, THREE subcommands** `eosd daemon` / `eosd ns-runner` / `eosd ns-holder` (the third added by iteration 4 for the isolated-workspace persistent namespace — see PND below) — one artifact per arch, clean internal boundary, later split is protocol-free.
- **PND — persistent-namespace-holder design (iteration 4 DESIGN DECISION).** Isolated-workspace needs a user+mount(+pid+net) namespace held open across calls. **Topology: the daemon orchestrates but NEVER enters a namespace; two single-threaded child roles do all ns syscalls.** (1) `eosd ns-holder` — daemon spawns it on `enter`; while still single-threaded it does `unshare(CLONE_NEWUSER|NEWNS|NEWPID|NEWNET)`, holds the ns FDs open, runs the readiness/control pipe handshake (1:1 with `scripts/ns_holder.py`'s `ns-up`→`net-ready`→`ready`, confirmed), then `pause()`s until SIGTERM on `exit`. (2) `eosd ns-runner` gains a **setns mode**: per isolated call it `setns`-es (single-threaded — kernel requirement) into the holder's pre-opened FDs, then execs. **Rationale:** the kernel forces namespace syscalls into single-threaded callers — `unshare(CLONE_NEWUSER)` AND `setns()` into a userns both require a single thread — so neither the create NOR the per-call entry can run inline in a multithreaded tokio daemon; both must live in dedicated single-threaded children. This mirrors the existing Python topology (`ns_holder.py` is already a daemon-spawned long-lived subprocess), so it is the minimal-surprise shape, not a new abstraction. The holder is NOT folded into `eos-runner` — it holds, it does not exec tools.
- **Docker only.** Daytona out of scope.
- **Contract preserved.** Host keeps calling logical `api.v1.*`; only the sandbox-resident impl changes. Wire = newline-delimited JSON envelopes over AF_UNIX (local) + 127.0.0.1 TCP (host-forwarded), versioned by `_eos_daemon_protocol_version` (currently `DAEMON_PROTOCOL_VERSION = 1`, field confirmed in `daemon_client.py`).
- **Python and Rust coexist** through Phases 1–4 behind `EOS_SANDBOX_RUNTIME=python|rust` (Python default). **`EOS_SANDBOX_RUNTIME` does not exist today — it is net-new host machinery this plan builds** (Phase 0 flag + Phase 2 dispatch fork), not a settled control. Only Phase 5 removes the Python bundle/launcher.
- **Delivery = Option A: external Cargo workspace + pinned released binary artifact** (OQ#1 resolved — no atomic cross-repo need justifies a submodule). See §8.
- **Selection-per-sandbox, not per-op.** One runtime owns a sandbox for its lifetime (enforced by the singleton socket/PID and the storage flock lease — see PV-1/AV-5b). The flag flips which runtime *new* sandboxes get; it does not mix runtimes inside one sandbox.

---

## 1. External project layout (`/sandbox`)

A standalone Cargo workspace, decoupled from `backend/` (no shared build, no Python import). It produces released, pinned binary artifacts the backend consumes.

```
/sandbox/
  Cargo.toml                      # workspace
  rust-toolchain.toml             # pinned toolchain + musl targets
  crates/
    eos-protocol/                 # SINGLE SOURCE OF TRUTH for the wire contract
      src/                        #   envelope types (serde), protocol version const
      fixtures/                   #   golden request/response JSON (the canonical set)
    eosd/                         # the binary: `eosd daemon` | `eosd ns-runner` | `eosd ns-holder`
      src/main.rs                 #   subcommand dispatch only
    eos-daemon/                   # RPC server, dispatcher, in-flight, audit buffer; spawns ns-holder on enter
    eos-runner/                   # ns-runner: fresh-ns (unshare→uid_map→mount→exec) AND setns mode (enter holder FDs→exec)
    eos-ns-holder/                # ns-holder: single-threaded unshare(USER|NS|PID|NET), hold FDs, pipe handshake, pause() (PND)
    eos-workspace/                # OCC + LayerStack + overlay pipeline/publish + verbs + isolated lifecycle (enter/exit)
  xtask/                          # musl cross-build, release packaging, fixture export
  .github/workflows/ci.yml        # build + test + bench + release (or local CI equiv)
  CONTRACT.md                     # protocol version policy + fixture pin procedure
```

**Crate boundaries:** `eos-protocol` depends on nothing project-internal (so both `eosd` and the Python backend pin against the *same* fixture set). `eos-runner` and `eos-ns-holder` are single-threaded, syscall-only (`rustix`/`nix` + `libc` gaps), no `tokio` (the single-threaded constraint is a kernel requirement for `unshare(CLONE_NEWUSER)`/`setns`, not just a style choice — PND). `eos-daemon` may use `tokio` only if the async port justifies it (REVIEW: conservative dep set — `serde`, `serde_json`, `rustix`/`nix`, `libc`; `tokio`/`tracing`/`thiserror` only if justified).

**CI / artifacts:** static-musl build for `x86_64-unknown-linux-musl` and `aarch64-unknown-linux-musl` via `cross`/`cargo-zigbuild` (REVIEW: pure-Rust dep set makes this the easy case). Outputs **`eosd-linux-amd64`** and **`eosd-linux-arm64`**, stripped, plus a `SHA256SUMS` and a `protocol_version` manifest. CI runs: unit tests, the golden-fixture parity suite (§4 availability), and the benches (§3 perf). **Release SIGNS each binary (Change 2):** a detached **minisign** signature (`eosd-linux-{amd64,arm64}.minisig`) is produced on release with the release signing key. Minisign chosen over cosign — single Ed25519 key, no PKI/OCI/sigstore machinery — consistent with the smallest-dependency-surface principle. Release attaches the two binaries + `.minisig` signatures + checksums + fixture tarball to a tagged release.

**Decoupling + consumption (delivery = Option A, settled §0):**
- The backend does **not** build Rust. It consumes a **pinned released artifact** by tag/version + SHA256 + minisign signature, plus a pinned copy of the `eos-protocol` fixtures.
- A `backend/src/sandbox/runtime_artifact/` (new, small) records the pinned `eosd` version, per-arch SHA256, the protocol version it speaks, AND the **pinned minisign public key (trust anchor)**. Host verifies signature + SHA256 before upload/exec (fail-closed — see §2/AV-8).
- Versioned-protocol contract is the only coupling: bump `_eos_daemon_protocol_version` requires a coordinated release + backend pin bump (procedure in `CONTRACT.md`).

---

## 2. Integration contract (Rust ⇄ Python backend)

**Frozen, versioned JSON protocol.** Already exists at version 1 (`DAEMON_PROTOCOL_VERSION`, `DAEMON_PROTOCOL_FIELD = "_eos_daemon_protocol_version"`, `DAEMON_AUTH_FIELD`). Phase 0 freezes the *current* Python-emitted envelopes as golden fixtures; Rust must reproduce them to the **canonicalized-equal** bar (see AV-1, §4), with a separate **byte-identity** bar for the CAS digest payload only.

**Single source of truth for fixtures (mitigates protocol-drift pre-mortem #3):** the `eos-protocol` crate `fixtures/` directory is canonical. The Python backend **vendors a pinned copy** under `backend/tests/.../sandbox_protocol_fixtures/` recording the upstream commit/tag. **Both CIs assert the pin matches** (Rust CI: fixtures match emitted envelopes; Python CI: vendored copy hash == pinned upstream hash). A drift fails both pipelines, not neither. (OQ#3 resolved: this canonical+pinned+dual-assert layout is settled.)

**The canonical fixture set MUST include the recovery contract (PV-2):** the thin-client exit codes **97 (CONNECT_FAILED)** and **98 (IO_FAILED)** as wire-observable outcomes, and the daemon **readiness envelope** `api.runtime.ready{layer_stack_root}` (the daemon keys OCC services by `layer_stack_root` — confirmed `daemon/occ_runtime_services.py:48`). Rust must emit the readiness envelope and produce the 97/98 outcomes on the matching failure conditions.

**Artifact naming + delivery:** `eosd-linux-{amd64,arm64}`, pinned by release tag + SHA256 in `runtime_artifact/`. Host selects by container arch.

**Changes to `provider/` (the criterion-4 lever — sequenced FIRST, Phase 0):**
- Add `put_archive(sandbox_id, *, tar_stream, dest_dir)` to the `ProviderAdapter` Protocol (`provider/protocol.py` — confirmed exposes only `exec()` today). Docker adapter wraps `container.put_archive()` / `docker cp`: single streamed put, no in-image `tar`/`gzip`/`base64`.
- Math being fixed (REVIEW §5.2): today base64-over-`exec` 32 KiB chunks (`chunked_upload.py` `DEFAULT_CHUNK_SIZE = 32*1024`) = ~8 round-trips for the 178.7 KiB source; a 1.5–3 MB binary would be ~64–128 round-trips. `put_archive` collapses this to one streamed transfer regardless of size.

**Changes to `host/`:**
- New `EOS_SANDBOX_RUNTIME` flag + dispatch fork (net-new — Phase 0 flag, Phase 2 fork): selects Python vs Rust launch/connect per sandbox.
- **Signature verification (Change 2 — you ship an executable INTO untrusted BYO sandboxes, so SHA-pin proves integrity but NOT provenance).** Before upload/exec, the host verifies the `eosd` binary's **minisign signature against the pinned trust-anchor public key** in `runtime_artifact/`, in addition to the SHA256. **Fail-closed:** an unsigned, mis-signed, or SHA-mismatched binary is REJECTED with no exec (AV-8).
- New upload path: stream the verified `eosd` binary via `put_archive`, `chmod +x`, re-verify SHA256 in-sandbox. Replaces the tar.gz + `tar -xzf` finalize (`runtime_bundle.py:355-360`, confirmed).
- New launch path: `eosd daemon` instead of `unshare -Urm python -m ...`.
- **AF_UNIX local-fallback connector (host-side):** replaces the `sh -c` thin-client launcher (`_daemon_thin_client_command`, `daemon_client.py:595+`, confirmed). Must reproduce the **97/98 exit-code contract** and the TCP-endpoint-cache invalidation on CONNECT_FAILED (`daemon_client.py:445-449`, confirmed: drop cached endpoint, re-resolve via the docker adapter on the next call).
- **Drop the `_PYTHON_CANDIDATES` probe** (`daemon_client.py:36`, confirmed `("python3.13"…"python3")`) — only in Phase 5 cutover; gated by `EOS_SANDBOX_RUNTIME` until then.
- Preserve the CONNECT_FAILED retry/respawn recovery (`_CONNECT_RETRY_DELAYS_S`, confirmed) — the Rust daemon must satisfy the same readiness/respawn contract.

---

## 3. Performance checkpoints (experiments)

> **Gate philosophy (addresses the #1 review risk).** Every quoted perf number in the prior docs is a **macOS host-proxy prediction**; the REVIEW flags the in-sandbox Python baseline, the actual daemon RSS, and the syscall floor as **UNMEASURED**. Therefore **CP-0 establishes the in-sandbox Linux Python baseline first**, and every other gate is expressed as a **ratio against CP-0's measured baseline** (threshold parametric, locked at CP-0), never as a quoted absolute. A gate against an unmeasured baseline is not a gate.

| ID | Metric | Baseline | Method | Pass threshold | Gate for |
| --- | --- | --- | --- | --- | --- |
| **CP-0** | In-sandbox Linux Python: per-call runtime-init, end-to-end per-call total, daemon idle RSS, daemon cold-start, upload time | — (this *is* the baseline) | `bench_sandbox_e2e.py` extended to run inside a Docker sandbox image; record syscall-floor component by timing a no-op `unshare→mount→execve→cleanup` | All captured + checked in as `bench/baseline-{arch}.json`, **which MUST also record kernel version + the config that gates the overlay path: unprivileged userns enabled (`kernel.unprivileged_userns_clone`/equivalent) and overlay-in-userns support** (S2 — pre-mortem #2 is kernel variance) | **All later CPs** |
| **CP-1** | Upload time: `put_archive` vs base64-over-exec | CP-0 upload time for the source bundle | Time both paths for a 1.5–3 MB blob in-sandbox | `put_archive` ≤ CP-0 upload time AND constant w.r.t. size (no round-trip scaling) | Phase 0 exit |
| **CP-1b** *(viability, AV-class — M5; concrete matrix locked by Change 3)* | `put_archive` of `eosd` succeeds + the binary actually runs the migrated paths | — | **BYO-image matrix (locked):** kernel floor **5.11+ (binding constraint = unprivileged overlayfs-in-userns; the `fsopen`/`fsmount` mount API is older at 5.2, so overlay-in-userns is what binds the floor)** AND a current LTS kernel; **both arches** (amd64 + arm64); a **non-root-user** image; a **read-only-rootfs** image. Each image must validate: (i) upload/extract/chmod/SHA256/`eosd --version`; (ii) **EXDEV** — layer-stack storage root and publish staging dir on ONE filesystem (`publisher.py:104` `os.replace` raises `EXDEV` across mounts, so a /tmp-class binary path is insufficient); (iii) **the setns persistent-namespace path (Change 1)** — `eosd ns-holder` `unshare`s + holds, `eosd ns-runner` setns-enters, not just fresh-ns + binary placement. | **All of (per matrix image):** dest exists, executable, SHA256 matches, `--version` runs, storage-root/staging share one fs, AND a full isolated enter→setns-run→exit cycle succeeds | Phase 0 viability + Phase 3.5 isolated exit |
| **CP-5** *(OCC cache-lock contention — Change 4; was REVIEW §5.1 follow-up)* | Lock-wait time on the OCC services cache RLock | CP-0/CP-4 Python lock-wait under the same scenario | Profile the `_RUNTIME_SERVICE_CACHE` RLock (256-entry LRU — `occ_runtime_services.py:43-45`, confirmed `_OCC_RUNTIME_SERVICES_CACHE_MAX = 256`, `threading.RLock`) under a scenario that forces **LRU eviction churn (> 256 distinct `layer_stack_root`s)** — the GIL-vs-`Mutex` question the REVIEW raised. **Distinct from CP-4:** CP-4 measures publish p95; CP-5 measures cache-lock *wait/contention* specifically under eviction churn. | Rust lock-wait p95 ≤ Python lock-wait p95 (ratio vs CP-4 baseline); contention metrics emitted as `bench/cache-lock-{arch}.json` | Phase 3 exit |
| **CP-2a** | Per-call **runtime-init portion** (ns-runner) | CP-0 runtime-init | Microbench: process spawn → ready, isolated from syscall floor | **Gate: ≥ 20× faster** than CP-0 runtime-init. (50–120× is *expected*; below 50× = investigate, not fail — S1) | Phase 1 exit |
| **CP-2b** | Per-call **end-to-end total** (fresh-namespace tool call) | CP-0 end-to-end | Full `unshare→mount→execve→tool→cleanup`, in-sandbox | **No regression** vs CP-0 end-to-end (NOT a multiplier — floored by language-independent syscall cost) | Phase 1 exit |
| **CP-3** | Daemon cold-start + idle RSS | CP-0 daemon cold-start + RSS | Spawn daemon, sample RSS at idle | RSS ≤ 0.5× CP-0 (target 2–10×); cold-start ≤ CP-0 | Phase 2 exit |
| **CP-4** | Throughput / OCC publish under parallel contention | CP-0 throughput at same concurrency | N parallel agents driving the **defined op set** (read_file/write_file/edit_file/shell/search verbs × OCC publish + LayerStack squash/GC ops) in N parallel sequences | **Divergence detector (M2):** (a) canonicalized result equality per AV-1 across runtimes AND (b) equal **final-workspace-state hash** = `manifest_root_hash` + the **per-layer `layer_digest` byte-stream parity** (item 1 — not just the set of digests, the digests themselves must be byte-identical across runtimes) compared after each parallel batch; p95 publish latency ≤ CP-0 p95 | Phase 3 exit |

CP-2b is deliberately a no-regression gate, not a speedup claim: the REVIEW's guardrail is that end-to-end is bounded below by the unmeasured syscall floor, so the realized win = `runtime_saved / (syscall_floor + <1ms exec)` and cannot be quoted until CP-0 measures the floor.

**Parametric gates are accepted as a strength (OQ#2 resolved):** thresholds lock at CP-0 against the measured in-sandbox baseline. No host-proxy number is ever quoted as a gate.

**Shell-free dest-dir creation (M5, required for Phase 5).** Today the upload dest is created via in-image `mkdir -p` shell (`runtime_bundle.py:335`, confirmed). For a truly Python/shell-free image, `put_archive` must create the destination directory itself (tar entries can carry the parent dir) rather than relying on a preceding `exec("mkdir -p ...")`. CP-1b's read-only-rootfs case validates that the chosen writable dest (e.g. `/tmp`-class path) works without shell.

---

## 4. Availability checkpoints

| ID | Property | Method | Gate |
| --- | --- | --- | --- |
| **AV-1** | **Protocol parity (canonicalized-equal — M1)** | Golden fixtures (`eos-protocol/fixtures`): Rust output equals frozen Python envelopes under the **canonical form** below. A drift fails. | Every phase touching new verbs |
| **AV-1c** | **CAS byte-identity (narrow) — TWO persisted hashes** | **(1) `manifest_root_hash`:** Rust reproduces `json.dumps(payload, sort_keys=True, separators=(",",":"))` over `{"layers":[layer.to_dict()...]}` so SHA256 matches (`manifest.py:137`, confirmed). Achievability verified: payload is string-only — `LayerRef.to_dict() -> dict[str, str]` = `layer_id` + `path` only (`manifest.py:64`); no float/`repr` divergence. **(2) `layer_digest` (per-layer, correctness-bearing — item 1):** Rust reproduces the `update_digest` byte-stream exactly (`changes.py:145-157`): per change `kind\0` + `path\0` + (`write`→`write_content` \| `symlink`→`source_path`) + `\0`, over `aggregate_layer_changes` output. **Ordering binding:** `aggregate_layer_changes` (`changes.py:159`) is last-write-wins per path then emits in **`sorted(path)` order** (confirmed deterministic by sorted path, NOT dict-insertion order); the Rust port MUST sort identically (no Python-side ordering nondeterminism to inherit). This digest drives head-layer dedup (`publisher.py:76`), is persisted via `write_layer_digest_atomic` (`:106`), and is read back across publishes (`:171-175`). On-disk manifest uses a *different* serialization (`indent=2, sort_keys=True`, line 156) — parsed, not hashed → AV-1 canonical, NOT byte-identity. | Phases 2–3 (any path computing either CAS hash) |
| **AV-2** | **Crash containment + respawn + readiness (S4)** | Kill daemon mid-flight; host CONNECT_FAILED retry/respawn (`_CONNECT_RETRY_DELAYS_S`) reconnects; no orphaned mounts. Rust must emit `api.runtime.ready{layer_stack_root}` and the host must invalidate the cached TCP endpoint on CONNECT_FAILED then re-resolve (`daemon_client.py:445-449`, confirmed). | Phase 2 exit |
| **AV-3** | **Cancellation** | Cancel kills full process group (`start_new_session=True`, confirmed in `namespace_runner.py`); timeout cleanup unmounts overlay | Phase 1 + Phase 3 exits |
| **AV-4** | **Audit drop-free** | Audit pull under CP-4 load loses zero records | Phase 3 exit |
| **AV-5a** | **Read/idempotent shadow diff** | For **reads/idempotent ops only**, per-op sampled shadow against the *other* runtime on a separate read-only view; diff canonicalized results; alert on mismatch | Cutover gate (Phase 5) |
| **AV-5b** | **Write/publish A/B (per-sandbox, NOT per-op — M3)** | One runtime per sandbox for its lifetime (enforced by singleton socket/PID + storage flock lease). Gate = §7 differential/property tests under contention against **separate state** + production per-sandbox A/B. **Stopping rule (item 2 — outcome-class, NOT per-op byte equality, since per-op write byte-diff is forbidden by PV-1):** across N ≥ 1,000 publish ops per op-class through the Rust runtime over ≥ 1 full traffic cycle (canary = per-sandbox fraction ramped 1%→10%→50%), the Rust runtime's **outcome-class distribution** (success / conflict / error) and **error rate** must not diverge from the Python baseline beyond ε = 0 (any outcome-class divergence is a hard stop). Byte-level write equality is proven separately and offline by §7 differential tests against separate state, not by comparing two co-publishing runtimes. | Cutover gate (Phase 5) |
| **AV-6** | **Rollback** | **"Pre-write" = pre-Rust-*durable-publish*, not "no mutation".** Phase 1's ns-runner does mutate the overlay (`write_file`/`edit_file`/`shell`), but durable OCC/LayerStack **publish** (the on-disk CAS format M4 cares about) stays Python through Phase 2 — so no Rust-written on-disk state exists to roll back across yet. Phases 1–2: flip `EOS_SANDBOX_RUNTIME=python` once the flag is built; zero redeploy. **Phase 3+ (Rust durable publish):** see M4 — rollback requires forward+backward on-disk format parity (AV-7) or is forbidden. | Phases 1–2 continuous; Phase 3+ gated by M4 |
| **AV-7** | **Forward+backward on-disk format parity (M4 — enables write rollback)** | After a **Rust** publish (`os.replace(staging,layer_dir)`-equivalent, `publisher.py:104`; manifest write `manifest.py:156`; layer digest), a **Python** runtime reads the resulting layer stack and yields **canonically-equal** results to a Python-published baseline — and vice versa. **DETECTOR MUST also compare the persisted `layer_digest` stream across the round-trip (item 1), not just results.** A `layer_digest` divergence is SILENT under a results-only check: after rollback, a Python write of identical content computes the Python digest, mismatches the Rust-persisted head digest (`publisher.py:76`), and silently publishes a DUPLICATE layer instead of deduping — results stay correct, dedup invariant + layer count silently diverge. So AV-7 explicitly asserts: identical input change-sets → identical persisted `layer_digest` AND identical head-dedup decision, both Python↔Rust directions. | Phase 3 exit (REQUIRED — see M4) |
| **AV-8** | **Binary signature fail-closed (Change 2)** | Host verifies the `eosd` minisign signature against the pinned trust-anchor public key (`runtime_artifact/`) AND the SHA256 before upload/exec. **Assert REJECTION (no exec) for: unsigned, mis-signed (wrong key), and SHA-mismatched binaries.** A passing case (correctly signed + matching SHA) execs; all three failure cases fail-closed. | Phase 0 (host verify path) + every launch |
| **AV-9** | **Isolated-workspace lifecycle parity (Change 1)** | `enter/run/exit` + snapshot lease, phase timing, discard, audit semantics canonically-equal to Python; **plus the host concurrency semantics** (`isolated_workspace_lifecycle.py`, confirmed): **enter REJECTS when active background work is in-flight for the agent** (local + daemon counts), **exit DRAINS/cancels per-agent background work**, and the `lifecycle_operation` audit wrapper fires. The existing IWS concurrency + phase-budget test suites must pass against Rust. Verified on the CP-1b BYO matrix (setns path). | Phase 3.5 exit |

**Canonical form (AV-1):** JSON objects compared with **keys sorted recursively**; integers exact; floats normalized and compared within tolerance **1e-9 relative** (timing/latency fields excluded from the diff by an allowlist); strings compared as decoded UTF-8 (escaping style — `\uXXXX` vs literal — is normalized away before compare). This is the parity bar for all verbs. Byte-identity is NOT required here; it is required only at AV-1c.

**PV-1 — why dual-run is split.** Two runtimes cannot share one sandbox: the daemon socket/PID are singletons with idempotent spawn (`daemon_client.py:32-33`, confirmed), `layer_stack/storage_lock.py:71` holds `fcntl.flock(LOCK_EX|LOCK_NB)` as a **single-owner lease per storage root** (confirmed), and `occ/commit_queue.py` makes CAS races impossible only *within one runtime's single `occ-commit-queue` thread*. Per-op write shadowing would double-apply and corrupt. So AV-5b is per-sandbox A/B, never per-op. **Requirement (item 4 — the lease is TWO layers, reproduce BOTH):** the single-owner guarantee is OS-flock **PLUS** an in-process refcounted mutex — `storage_lock.py:63-66,78` keeps a process-wide registry that does `refcount += 1` and shares a per-root `threading.RLock()` (confirmed). A second `flock(LOCK_EX)` from the *same* process succeeds on Linux, so the OS lease alone does NOT serialize intra-process writers; the in-process refcount/RLock does. The Rust port MUST reproduce both: the `flock(LOCK_EX|LOCK_NB)` cross-process lease on the identical lock path AND the intra-process refcounted shared-mutex serialization.

**OCC/LayerStack note (HIGH risk).** Fixtures (AV-1/AV-1c) verify single-shot parity only — they do **not** catch a concurrency-invariant divergence. The real exit gate for these two modules is the §7 differential/property test under contention + CP-4's final-workspace-state hash + AV-5b's per-sandbox A/B, NOT fixtures.

**M4 DECISION — write-phase rollback is SUPPORTED, gated on AV-7.** Phase 3 lands Rust writes; if a sandbox running Rust must roll back to Python, the Python runtime has to read Rust-published manifests/layers (`manifest.py:137/156`, `publisher.py:104` `os.replace`, all confirmed) and vice versa. We **support** rollback rather than forbid it, because forbidding it would strand every sandbox that took a Rust write with no safe exit — the worse data-safety posture. Support is **conditional on AV-7 passing** (forward+backward on-disk format parity at Phase 3 exit). *Rationale:* the on-disk format is plain JSON manifests + content-addressed layer dirs produced by `os.replace`; reproducing the exact `manifest_root_hash` AND `layer_digest` serializations (AV-1c) makes the formats interchangeable, so the parity is achievable and testable rather than a leap of faith. If AV-7 cannot be made to pass, the fallback is to **forbid** write-phase rollback and narrow AV-6 to read-only phases — but that is the explicit non-goal, surfaced as a stop-and-escalate, not a silent default.

**schema_version binding (item 5).** `manifest.py:108-112` hard-rejects `schema_version > MANIFEST_SCHEMA_VERSION` with `ManifestConflictError` (confirmed) — so a Rust write that bumps the on-disk `schema_version` makes a rolled-back Python reader **hard-fail exactly when rollback is needed**. Therefore an on-disk `schema_version` change is a **protocol-version-class coordinated event**: it is bound to the same `CONTRACT.md` coordinated-bump procedure as the wire protocol and is NEVER a unilateral Rust change. While both runtimes coexist (Phases 1–4), the Rust runtime writes the current `MANIFEST_SCHEMA_VERSION` and does not bump it.

---

## 5. Phased migration

Each phase: deliverables → anchors replaced → EXIT GATE. Python stays default until Phase 5.

### Phase 0 — Bootstrap (no behavior change)
- **Deliverables:** Cargo workspace + crates skeleton; `eos-protocol` with frozen v1 fixtures; CI musl build of `eosd-linux-{amd64,arm64}`; `put_archive` on `ProviderAdapter` + Docker adapter; `runtime_artifact/` pin scaffold; **CP-0 baseline captured + checked in**.
- **Replaces:** nothing yet; *adds* the `put_archive` primitive alongside `chunked_upload.py`.
- **EXIT GATE:** CP-1 passes; CI green on both arches; AV-1 fixtures exist and Python side asserts the pin; CP-0 `baseline-{arch}.json` committed.

### Phase 1 — ns-runner in Rust (lowest risk, per-call win)
- **Deliverables:** `eosd ns-runner` does the **fresh-namespace path only** — `unshare→uid_map→mount(raw fsopen/fsconfig/fsmount/move_mount)→execve→result JSON→cleanup`. The still-Python daemon invokes the Rust runner instead of `unshare -Urm python -m sandbox.overlay.namespace_entrypoint`.
- **Replaces:** `overlay/namespace_runner.py:237-244` (fresh path), `namespace_entrypoint.py`, raw mount in `kernel_mount.py`. **Explicitly NOT** the `setns` existing-namespace path (`isolated_workspace/scripts/setns_*.py`, `_control_plane/namespace_runtime.py`) — that ships in **Phase 3.5**. (`eos-runner`'s setns mode is added there; Phase 1 builds only its fresh-ns mode.)
- **EXIT GATE:** CP-2a + CP-2b pass; AV-1 (verb results), AV-3 (cancel/timeout) pass; toggled by `EOS_SANDBOX_RUNTIME=rust`.

> **Phase 0 also delivers the `EOS_SANDBOX_RUNTIME` flag** (net-new env var + a no-op host read), so Phase 1 has a real toggle. The **dispatch fork** (host actually launching/connecting Rust vs Python) lands in Phase 2.

### Phase 2 — Daemon skeleton + read paths
- **Deliverables:** `eosd daemon` (AF_UNIX + 127.0.0.1 TCP, newline-delimited JSON, protocol v1, `ready`/`ping`, auth field, in-flight registry, audit buffer); **host `EOS_SANDBOX_RUNTIME` dispatch fork + AF_UNIX local-fallback connector reproducing the 97/98 exit-code contract**; direct file `read_file` + read verbs; LayerStack/OCC **read** paths; readiness envelope `api.runtime.ready{layer_stack_root}`.
- **Replaces:** `daemon/` server + dispatcher; `daemon/scripts/launch_daemon.sh` + `thin_client.py` (host-side connector); read-side of `shared/` verbs; read-side `occ/` + `layer_stack/`.
- **EXIT GATE:** CP-3 passes; AV-1 (read results), AV-1c (CAS digest where read paths compute it), AV-2 (respawn + readiness + endpoint-cache invalidation) pass.

### Phase 3 — Write/publish + shell/search + background (HIGH-risk core)
- **Deliverables:** OCC write/edit **publish**; overlay shell/search; background in-flight tracking, heartbeat, cancellation, TTL cleanup. Reproduce the commit-queue serialization params (PV-3): single `occ-commit-queue` writer thread, `batch_window_s = 0.002`, `max_batch_size = 64`, `MAX_OCC_CAS_RETRIES = 3` (all confirmed `occ/commit_queue.py`); and the `storage_lock.py` `flock(LOCK_EX|LOCK_NB)` single-owner lease.
- **Replaces:** write-side `occ/` + `layer_stack/` (squash/GC/lease), `ephemeral_workspace/` publish + plugin dispatch, write verbs in `shared/`, daemon in-flight/TTL.
- **EXIT GATE:** CP-4 passes (canonical equality + final-workspace-state hash); AV-1, AV-1c (CAS byte-identity), AV-3, AV-4 pass; **OCC/LayerStack differential+property tests under contention pass** (§7); **AV-7 forward+backward on-disk format parity passes** (gates write rollback per M4). Note: AV-5a/AV-5b are Phase-5 cutover gates, not Phase-3 exit gates (per-op write shadow is unsafe — PV-1).
- **Drain-then-swap (S3):** flipping `EOS_SANDBOX_RUNTIME` or swapping the daemon MUST drain in-flight requests first (reuse the existing per-agent inflight counter + drain event from `workspace_tool/dispatch.py`); no in-flight request crosses a runtime swap mid-call.

### Phase 3.5 — Isolated workspace (NOW IN-SCOPE — iteration 4; HIGH risk)
Placed after Phase 3 because it builds on the OCC/LayerStack core + the runner. Used on BYO/minimal images that lack Python, so it cannot stay on the Python path.
- **Deliverables:**
  - **`eosd ns-holder`** subcommand — single-threaded `unshare(CLONE_NEWUSER|NEWNS|NEWPID|NEWNET)`, hold the ns FDs, readiness/control pipe handshake (1:1 with `scripts/ns_holder.py` `ns-up`→`net-ready`→`ready`), `pause()` until SIGTERM (PND topology, §0).
  - **`eosd ns-runner` setns mode** — per isolated call, single-threaded `setns` into the holder's pre-opened FDs then exec (analog of fresh-ns mode). Ports `setns_exec.py`, `setns_overlay_mount.py`, `_setns_libc.py`.
  - **Daemon-side isolated lifecycle** (`enter`/`exit` RPC + `_control_plane/namespace_runtime.py`): spawn/teardown the holder, snapshot lease, phase timing, discard, audit.
  - **Host `isolated_workspace_lifecycle.py` ADAPTED** to drive the Rust lifecycle (preserving enter-gate-on-active-bg-work, exit-drain, `lifecycle_operation` audit).
  - **BLOCKER fix — shell-free isolated-ns networking (item-1 wrinkle).** `scripts/ns_holder.py` shells out to `ip -6 route flush` + `sysctl -w` to purge IPv6 default routes / disable RA — an **in-image-tool dependency inside the exact feature being un-deferred for minimal images** (its own docstring notes "some images strip `ip -6` or the sysctl write path"). **DECISION: port shell-free** — RA-disable + route-flush via **rtnetlink** (`RTM_DELROUTE`) and the sysctl writes via direct `write()` to `/proc/sys/net/ipv6/...`, no `ip`/`sysctl` binaries. This is consistent with the plan's syscall-not-shell ethos (overlay already uses `fsopen`/`fsmount`, not the `mount` binary). The current best-effort-degrade semantic is preserved (tolerate `/proc` write failure on kernels lacking the path) but is now tool-free. See pre-mortem #4.
- **Replaces:** `isolated_workspace/` (incl. `scripts/ns_holder.py`, `setns_exec.py`, `setns_overlay_mount.py`, `_setns_libc.py`, `_control_plane/namespace_runtime.py`).
- **EXIT GATE:** AV-9 (isolated lifecycle parity incl. enter-gate/exit-drain + existing IWS concurrency/phase-budget suites pass against Rust); CP-1b setns-path validation green on the full BYO matrix; AV-3 (cancellation/teardown of the holder process group).

### Phase 5 — Cutover
- **Deliverables:** make `EOS_SANDBOX_RUNTIME=rust` default after the canary; remove the Python bundle, `daemon/scripts/launch_daemon.sh`, `thin_client.py`, the `_PYTHON_CANDIDATES` probe, `chunked_upload.py` runtime-bundle path, `runtime_bundle.py` tar finalize, **and the Python `isolated_workspace/` `setns` scripts** (no Python `setns` fallback survives — iteration 4); resolve `install_git.sh` (drop or shell-free replacement); land the shell-free `put_archive` dest-dir creation (M5).
- **EXIT GATE:** AV-5a (read shadow) + AV-5b (per-sandbox write A/B, N≥1,000/op-class, zero outcome-class divergence, ≥1 full traffic cycle) pass; AV-8 (signature fail-closed) + AV-9 (isolated parity) green; CP-1b viability green on the full BYO matrix incl. setns path; full Definition of Done (§9). Rollback path (AV-6/AV-7) verified one last time before deletion.

---

## 6. Pre-mortem (4 concrete failure scenarios)

1. **OCC/LayerStack concurrency-invariant divergence** — *likelihood MED, impact HIGH (data corruption / lost writes).* **Corrected mechanism (PV-3):** the Python locking is ALREADY explicit, not GIL-implicit — a reentrant `threading.RLock`, a dedicated `occ-commit-queue` OS thread, and `run_sync_in_executor` offload (`occ/commit_queue.py`, `occ/service.py`, confirmed). The real risk is the **reentrant→non-reentrant restructuring**: a naive 1:1 port to Rust `std::sync::Mutex` (non-reentrant) **deadlocks** wherever the Python `RLock` is re-acquired on the same thread. The port must restructure those re-entrant sections AND reproduce the commit-queue serialization params (single-writer, `batch_window_s=0.002`, `max_batch_size=64`, CAS budget `3`). **Mitigation (mechanism-agnostic — unchanged):** §7 differential + property tests under parallel contention as the Phase 3 exit gate (not fixtures), plus CP-4's final-workspace-state hash; AV-5b per-sandbox A/B before flipping default; rollback path per M4.
2. **Kernel-feature / musl-aarch64 variance across BYO images breaks overlay mount** — *likelihood MED, impact HIGH (tool calls fail on real customer images).* Raw `fsopen/fsmount/move_mount` and unprivileged user-ns overlay are kernel-version and config dependent; an arm64 musl build may behave differently. **Mitigation:** a Docker capability matrix (mirror E0 in `sandbox_perf_experiments_PLAN.md`) run in CI across kernel/arch images; `eosd` emits a structured capability-probe on startup; fall back to `EOS_SANDBOX_RUNTIME=python` on probe failure during coexistence.
3. **Protocol drift between the external repo and the backend** — *likelihood MED, impact MED (silent wire incompatibility post-deploy).* Two repos evolving "the same" protocol. **Mitigation:** `eos-protocol` is the single fixture source of truth; backend vendors a pinned hash; both CIs assert the pin; protocol-version bump requires the coordinated release procedure in `CONTRACT.md`; the daemon rejects mismatched `_eos_daemon_protocol_version` at handshake.
4. **Isolated-ns networking depends on in-image tools on the exact minimal images that justify the un-defer (iteration 4)** — *likelihood MED-HIGH if ported naively, impact HIGH (isolated-workspace IPv6 egress hardening silently broken on minimal BYO images).* `scripts/ns_holder.py` shells out to `ip -6 route flush` + `sysctl -w` (confirmed; its docstring admits images strip these). A 1:1 port reintroduces the exact in-image-tool dependency the migration removes. **Mitigation:** the Phase 3.5 BLOCKER-fix decision — port the route-flush/RA-disable to **rtnetlink + direct `/proc/sys` writes**, no `ip`/`sysctl` binaries; CP-1b's setns path on the minimal/read-only-rootfs matrix images verifies isolated egress hardening works tool-free; keep the best-effort-degrade semantic where the kernel lacks the `/proc` path.

---

## 7. Expanded test plan (unit / integration / e2e / observability)

- **Unit (Rust, `/sandbox` CI):** envelope (de)serialization round-trips; verb logic; OCC publish/conflict resolution; LayerStack lease/squash/GC state transitions; **property tests** (proptest) over OCC operation sequences asserting invariants (no lost write, monotonic version). → AV-1, CP-4.
- **Integration (Rust):** daemon ↔ ns-runner over the real socket; overlay mount inside a privileged-enough test container across the kernel/arch matrix; cancel/timeout process-group teardown. **Isolated-workspace lane (Change 1):** `eosd daemon` spawns `eosd ns-holder` → handshake → `eosd ns-runner` setns-enter → exec → exit teardown; assert holder process-group dies on exit and the shell-free (rtnetlink + `/proc/sys`) IPv6 hardening runs with `ip`/`sysctl` absent. → AV-2, AV-3, AV-9, pre-mortem #2 + #4.
- **Differential (cross-runtime, the HIGH-risk gate):** drive identical operation sequences through Python and Rust **against separate state** under N-way parallel contention; assert **canonically-equal** typed results (AV-1), **byte-identical CAS digest** (AV-1c), and equal final-workspace-state hash (CP-4). Plus the M4 forward+backward parity check (AV-7): Python reads Rust-published on-disk state and vice versa. **Isolated-lifecycle differential (Change 1):** enter/run/exit + snapshot lease + phase timing + discard + audit canonically-equal Python↔Rust; the existing IWS concurrency/phase-budget suites run against Rust. → pre-mortem #1, Phase 3 + 3.5 exits.
- **E2E (backend, Python CI):** `bench_sandbox_e2e.py`-driven full tool calls through the host proxy with `EOS_SANDBOX_RUNTIME=rust`; CP-1b put_archive viability + setns-path validation across the locked BYO-image matrix (5.11+/LTS × amd64/arm64 × non-root × read-only-rootfs); host signature-verify fail-closed cases (AV-8). → CP-1b, CP-2b, CP-4, AV-4, AV-8.
- **Observability:** structured daemon diagnostics + startup capability probe (kernel/userns/overlay support, S2); AV-5a/AV-5b mismatch counters (per op-class); audit drop counter under load; **CP-5 cache-lock wait/contention metrics under LRU-eviction churn (`bench/cache-lock-{arch}.json`)**; signature-verify reject counter (AV-8); CP metrics emitted as `bench/*.json` artifacts per CI run for trend tracking.

---

## 8. RALPLAN-DR summary

**Principles**
1. Measure the in-sandbox baseline before gating; ratios against CP-0, never quoted absolutes.
2. Preserve the `api.v1.*` contract and v1 wire protocol; parity is **canonicalized-equal** for envelopes and **byte-identical** only for the CAS digest payload (AV-1/AV-1c).
3. Coexist (Python default) until parity is proven; cutover is last. **Rollback is one env-var for read phases (1–2) once the net-new `EOS_SANDBOX_RUNTIME` flag is built; write-phase rollback is bounded by M4 (forward+backward on-disk parity, AV-7).**
4. Smallest dependency surface — no in-image runtime/shell/tar; conservative Rust deps.
5. Highest-risk modules (OCC/LayerStack) are gated by contention/differential tests + final-state hashing, not single-shot fixtures.

**Decision Drivers (top 3)**
1. Image-agnostic packaging (BYO images may lack Python ≥3.10 + sh/tar/gzip/base64/unshare).
2. Safe, reversible cutover (no big-bang; dual-run diff + flag + rollback).
3. Decoupling without drift (external repo, pinned artifact + single-source fixtures).

**Viable Options (open axis = delivery/decoupling mechanism; binary-split and Rust-for-both are SETTLED, see §0)**

- **Option A — external Cargo workspace + pinned released binary artifact (RECOMMENDED).** *Pros:* clean repo separation; backend never builds Rust; explicit version+SHA pin; CI ownership clear. *Cons:* cross-repo changes are two PRs; protocol bumps need a release dance (mitigated by `CONTRACT.md` + dual-CI pin assert).
- **Option B — git submodule, vendored/built in backend CI.** *Pros:* atomic cross-repo commits; one source tree. *Cons:* backend CI must own a Rust+musl toolchain (reintroduces build coupling the rewrite is trying to shed); submodule ergonomics are error-prone.
- **Option C — monorepo subdir (`backend/../sandbox` built together).** *Pros:* simplest single-PR workflow. *Cons:* violates the "external project" requirement; couples build/release cadence; tempts shared deps.

→ **SETTLED: Option A** (OQ#1 resolved — reviewer confirmed no atomic cross-repo need justifies B). Invalidation: B reintroduces a build-time toolchain dependency in the backend (counters Driver 3 and the image-agnostic build story); C is excluded by the explicit "external project" requirement.

**Mode:** DELIBERATE — pre-mortem (§6) + expanded test plan (§7) included.

**ADR**
- **Decision:** port the in-sandbox runtime (INCL. isolated-workspace, iteration 4) to a Rust external Cargo workspace at `/sandbox`, delivered as a pinned + **minisign-signed** released `eosd` binary (3 subcommands: `daemon`/`ns-runner`/`ns-holder`), behind `EOS_SANDBOX_RUNTIME` with measure-first parametric perf gates.
- **Drivers:** the three above.
- **Alternatives considered:** delivery A/B/C (this doc); Go-for-both and Python-freeze (prior REVIEW — invalidated); ns-holder as in-daemon thread (rejected — kernel requires single-threaded caller for `unshare(CLONE_NEWUSER)`/`setns`, so the holder MUST be a dedicated single-threaded child — PND, §0); cosign signing (rejected for minisign — no PKI/OCI surface).
- **Why chosen:** A is the only option satisfying all three drivers; Rust-for-both and single-binary are inherited settled decisions. The PND subprocess topology is forced by the kernel, not chosen.
- **Consequences (blast radius grew with iteration 4):** two repos + coordinated protocol-bump procedure (now incl. on-disk `schema_version` — item 5); backend gains `put_archive` + the net-new `EOS_SANDBOX_RUNTIME` flag/dispatch fork + a host-side AF_UNIX local-fallback connector + **minisign signature verification (now an in-scope fail-closed gate, AV-8, not a follow-up)**; upload stops scaling with artifact size. **Isolated-workspace is now in-scope (+2,871 LOC → 19,474 total) with a daemon-owned `eosd ns-holder` persistent-namespace subprocess and a shell-free (rtnetlink + `/proc/sys`) IPv6-hardening rewrite (pre-mortem #4).** `host/isolated_workspace_lifecycle.py` is ADAPTED. **The no-Python-in-image payoff lands ONLY at Phase 5 — full migration risk (incl. HIGH-risk OCC/LayerStack AND isolated-workspace) is carried through Phases 1–3.5 with both runtimes maintained.** Write-phase rollback is constrained by M4 (AV-7 on-disk parity).
- **Follow-ups:** none of iteration 4's items remain deferred (isolated-workspace, signing, OCC cache-lock profiling, CP-1b matrix are all now in-scope). Residual: minisign **key rotation** procedure for `runtime_artifact/` (operational, post-GA).

---

## 9. Definition of Done

- `eosd` starts in a Docker image with no Python/Node/Rust/Go/bash/tar/gzip/base64 and no external `unshare` — verified on the CP-1b BYO-image matrix (incl. non-root + read-only-rootfs).
- CP-0 baseline (incl. kernel version + userns/overlay config) committed; CP-1, CP-1b (incl. setns-path validation), CP-2a, CP-2b, CP-3, CP-4, CP-5 pass against it; CP-2b shows no end-to-end regression; CP-5 Rust cache-lock wait ≤ Python under LRU-eviction churn.
- AV-1 (canonical) + AV-1c (CAS byte-identity, both hashes) + AV-2…AV-9 pass; OCC/LayerStack differential+property tests green under contention; AV-7 forward+backward on-disk parity proven; AV-8 signature fail-closed proven (unsigned/mis-signed/SHA-mismatch all rejected); AV-9 isolated-lifecycle parity + IWS concurrency/phase-budget suites green against Rust.
- `put_archive` is the upload path (shell-free dest-dir creation); base64-over-exec runtime-bundle path, `launch_daemon.sh`, `thin_client.py`, `_PYTHON_CANDIDATES` probe, the Python bundle, AND the Python `isolated_workspace/` `setns` scripts removed; `install_git.sh` dropped or shell-free; isolated-ns IPv6 hardening is shell-free (rtnetlink + `/proc/sys`).
- Host AF_UNIX local-fallback connector reproduces the 97/98 exit-code contract and TCP-endpoint-cache invalidation; readiness envelope emitted; host verifies the minisign signature + SHA256 fail-closed before exec.
- `EOS_SANDBOX_RUNTIME=rust` default after a clean per-sandbox canary (AV-5b: N≥1,000/op-class, zero outcome-class divergence, ≥1 full traffic cycle); rollback path verified.
- **`isolated_workspace` runs on `eosd` with NO Python in image** (iteration 4 — the Python `setns` exception is REMOVED): `eosd ns-holder` + `eosd ns-runner` setns-mode + adapted `host/isolated_workspace_lifecycle.py`, verified on the CP-1b BYO matrix incl. read-only-rootfs.

---

## 10. Open questions — RESOLVED (iterations 2–4)

Iteration-2 resolutions (folded in):
1. **Delivery mechanism → Option A (pinned released artifact).** SETTLED (§0, §8).
2. **Parametric gates → ACCEPTED as a strength.** Thresholds lock at CP-0; no host-proxy number quoted as a gate (§3).
3. **Fixture ownership → `eos-protocol/fixtures` canonical + backend-vendored pinned copy + dual-CI assert,** recovery contract (97/98 + readiness) in the canonical set (§2).
4. **Phase 4 (isolated-workspace / `setns`) → ~~DEFERRED~~ → NOW IN-SCOPE (iteration 4).** User confirmed isolated mode is used on BYO/minimal images lacking Python, so it cannot stay on the Python path. Ported in **Phase 3.5**; Python `setns` exception removed from DoD §9.
5. **Canary → per-sandbox A/B, ≥1 full traffic cycle, N≥1,000/op-class, zero outcome-class divergence** (AV-5b / M3, §4).

Iteration-4 resolutions (the four previously-residual/deferred items, now in-scope):
- **Isolated-workspace** → in-scope, Phase 3.5, PND topology (§0/§5).
- **Artifact signing/provenance** → in-scope minisign fail-closed gate AV-8 (§1/§2/§4/§8), no longer S5 follow-up.
- **CP-1b BYO-image matrix** → locked concretely (5.11+/LTS × amd64/arm64 × non-root × read-only-rootfs × setns-path) (§3), no longer residual.
- **OCC cache-lock contention** → in-scope perf gate CP-5 (§3/§7), no longer REVIEW §5.1 follow-up.

**Residual (operational, post-GA, not blocking):** minisign key-rotation procedure for `runtime_artifact/`.
