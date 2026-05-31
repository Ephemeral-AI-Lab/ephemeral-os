# Sandbox In-Sandbox Runtime → Rust External Project — Migration Plan

**Mode:** RALPLAN-DR DELIBERATE. **Status:** APPROVED WITH CONDITIONS (final arch-coverage review — `docs/plans/sandbox-rust-external-migration-FINAL-REVIEW.md`; MF-1 gate text applied; SF-1/3/4/5/6 folded). Prior consensus: iteration 3 Architect+Critic APPROVE (PV-1/2/3 + M1–M5 closed against source); iterations 4–5 added isolated-workspace + plugin-PPC scope.
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
| `ephemeral_workspace/` | 3,147 | MED-HIGH | overlay pipeline / publish / **plugin dispatch — CONTRACT CHANGE (iteration 5)**: the `plugin/` layer (`op_registry`, `runtime_api`, `overlay_child`, `overlay_dispatch`, `op_context`, `projection`) runs plugins today via `importlib.import_module("plugins.catalog.{name}.runtime.server")` (`overlay_child.py:129`, confirmed) — a Rust daemon cannot importlib Python. Ported to an **out-of-process plugin protocol** (see PPC, §0). The plugin-agnostic dispatch layer is rewritten in Rust; the plugin IMPLEMENTATIONS in `plugins/catalog/*` (outside `sandbox/`) are NOT rewritten — see scope option (b). |
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

**Plugin scope boundary — option (b), payloads bring their own runtime (iteration 5).** Core `eosd` stays Python/Node-free; language runtimes (Node for Pyright) are **optional plugin payloads uploaded via `put_archive` only when that plugin is enabled** — same BYO tension as isolated_workspace, but a plugin feature ships its own runtime to the minimal image rather than assuming it. Today only ONE plugin exists: `lsp`/Pyright (`plugins/catalog/lsp/`, confirmed; `install.py:421` downloads from `nodejs.org`). Decision: the plugin **logic + PyrightSession lifecycle is KEPT** (not rewritten in Rust — that would be over-build for one plugin); only the importlib-registration glue (`op_registry`/`overlay_child`/`runtime_api` *inside `sandbox/`*) is REPLACED — on the plugin side by a small **net-new protocol-server harness** that speaks the PPC channel. **Open sub-point for the Architect:** a Python wrapper means the LSP plugin needs BOTH a Node payload AND a Python payload; alternatively fold the harness into the already-required Node runtime (no Python payload). Smallest-coherent pick: keep the Python wrapper for now (least churn to working Pyright logic), flag the Node-only fold as a follow-up optimization.

**NOT migrating (stays Python — out of scope except the named edits):** `backend/src/sandbox/api/` (`api.v1.*` contract), `backend/src/sandbox/provider/` (Docker adapter — gains `put_archive` + signature-verify), rest of `backend/src/sandbox/host/` (gains binary upload + launch, the `EOS_SANDBOX_RUNTIME` dispatch fork, the local-fallback connector, and signature verification; drops the Python-candidate probe at Phase 5). Engine, TaskCenter, `tools/_framework` entirely out of scope.

**Settled decisions (one-line rationale; not reopened):**
- **Rust for both** daemon and ns-runner — smallest static-musl artifact + smallest dependency surface (REVIEW §6). Per-call/RSS wins are Python-vs-compiled, shared with Go; they justify *leaving Python*, not Rust-over-Go.
- **One binary, THREE subcommands** `eosd daemon` / `eosd ns-runner` / `eosd ns-holder` (the third added by iteration 4 for the isolated-workspace persistent namespace — see PND below) — one artifact per arch, clean internal boundary, later split is protocol-free.
- **PND — persistent-namespace-holder design (iteration 4 DESIGN DECISION).** Isolated-workspace needs a user+mount(+pid+net) namespace held open across calls. **Topology: the daemon orchestrates but NEVER enters a namespace; two single-threaded child roles do all ns syscalls.** (1) `eosd ns-holder` — daemon spawns it on `enter`; while still single-threaded it does `unshare(CLONE_NEWUSER|NEWNS|NEWPID|NEWNET)`, holds the ns FDs open, runs the readiness/control pipe handshake (1:1 with `scripts/ns_holder.py`'s `ns-up`→`net-ready`→`ready`, confirmed), then `pause()`s until SIGTERM on `exit`. (2) `eosd ns-runner` gains a **setns mode**: per isolated call it `setns`-es (single-threaded — kernel requirement) into the holder's pre-opened FDs, then execs. **Rationale:** the kernel forces namespace syscalls into single-threaded callers — `unshare(CLONE_NEWUSER)` AND `setns()` into a userns both require a single thread — so neither the create NOR the per-call entry can run inline in a multithreaded tokio daemon; both must live in dedicated single-threaded children. This mirrors the existing Python topology (`ns_holder.py` is already a daemon-spawned long-lived subprocess), so it is the minimal-surprise shape, not a new abstraction. The holder is NOT folded into `eos-runner` — it holds, it does not exec tools.
- **PPC — plugin protocol contract (iteration 5 DESIGN DECISION).** A Rust `eosd` cannot `importlib.import_module` a Python plugin (`overlay_child.py:129`, confirmed). Today's dispatch has THREE intent modes (`op_registry.py:16-20`, confirmed): `READ_ONLY` runs **in the daemon process** via importlib (the path that fundamentally cannot survive a Rust daemon); `WRITE_ALLOWED` already runs as `create_subprocess_exec(unshare,…,python -m overlay_child)` (`overlay_dispatch.py:159`, confirmed) with eosd owning the overlay+OCC wrapper; `auto_workspace_overlay=False` is **self-managed** (LSP `apply.py` manages its own overlay+OCC to keep the publish path unchanged — `op_registry.py:227`, confirmed). **Two orthogonal axes, not one:** (A) process lifetime, (B) overlay/OCC ownership.
  - **Axis A → LONG-LIVED plugin server, NOT one-shot exec.** Pyright cold-starts by indexing the project (multi-second); a one-shot-exec-per-op would re-pay that on every hover/completion. `eosd` spawns the plugin server **once per workspace session**, keeps it warm, tears it down on session end / killed process group (AV-3, keyed per workspace like the OCC services cache).
  - **Axis B → BIDIRECTIONAL channel, message-id'd (not request→response).** A separate-process self-managed plugin cannot touch eosd's OCC/LayerStack by import — it must RPC back. So the channel multiplexes: op-dispatch + `runtime_api` callbacks (overlay/OCC ops) flow *out from the plugin* with responses back, each tagged with a message id. **Transport = reuse the daemon's AF_UNIX newline-delimited JSON-envelope framing** (carries the message ids the callbacks need; consistent with the main protocol; no bespoke stdio multiplexing). Protocol body anchors 1:1 on the payload `overlay_dispatch.py` already builds — `caller` + `intent` + `metadata` + `op_context` + `projection` — no new fields, matching the plugin-agnostic contract `plugin/__init__.py:5` already promises. The Intent enum is carried as protocol metadata 1:1.
  - **Three modes mapped:** READ_ONLY → out-of-process to the warm plugin server (net-new; was in-daemon importlib); WRITE_ALLOWED → re-point the existing `overlay_child` subprocess at the plugin server, eosd still owns the overlay+OCC around it; self-managed → the bidirectional callback channel (preserve the opt-out; do NOT fold `apply.py` into the standard wrapper — that is a behavior change to the publish path needing its own parity justification).
  - **MF-1 — self-managed callback is a SECOND OCC entry point that MUST share the ONE single writer (REQUIRED before execution).** Today the self-managed plugin (`apply.py`, `auto_workspace_overlay=False`) publishes through the same per-`layer_stack_root` `occ-commit-queue` *by construction* — `overlay_dispatch.py:72` `publish_cycle()` is keyed by `get_occ_runtime_services(layer_stack_root)`, the singleton owner (`daemon/occ_runtime_services.py:44-90`). In the Rust PPC the callback arrives over the bidirectional channel as a STRUCTURALLY SEPARATE entry point, so it could silently bypass the single writer while byte-identical-results tests still pass. **Requirement:** the self-managed plugin OCC callback MUST route through the **same** per-`layer_stack_root` single `occ-commit-queue` writer AND the same `storage_lock` flock+RLock lease (PV-1/PV-3) as the primary publish path — never a second writer instance. (Gated by AV-10 + CP-4 interleave, below.)
  - **No single-threaded constraint** (unlike PND): the plugin host is a normal child + pipe, lives naturally in the tokio daemon. **Isolated-mode interaction:** plugin/LSP ops are blocked while isolated mode is active (existing invariant) — the Rust dispatcher preserves this gate (Phase 3 ↔ 3.5).
- **Docker only.** Daytona out of scope.
- **Test/dev environment + launch contract (codebase-verified — full detail in §12).** `eosd` is uploaded via `put_archive` and launched **under the Docker provider's existing default flags**, which are **non-privileged**: `--cap-add=SYS_ADMIN --cap-add=NET_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined` plus a `--tmpfs /eos-mount-scratch:rw,size=2g,mode=1777` overlay-writable root (`provider/docker/client.py:25-41`, confirmed; sufficiency checked by `preflight_docker_a2_caps.sh`). The Rust overlay/ns-runner/isolated crates therefore target **this cap envelope, not `--privileged`**. The concrete local test image is **`sweevo-dask__dask-10042`** (kernel `6.10.14-linuxkit` ≥5.11; Ubuntu 22.04; Python 3.10.14; no in-image cargo/rustc — `eosd` is a uploaded static binary). Probed result: unprivileged overlay-in-userns works there **only with these flags AND an overlay storage root on a non-overlay fs (tmpfs)** — overlay-on-overlay2 (container rootfs/`/tmp`) fails. This is the local instantiation of CP-0/CP-1b/§7.
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
    eos-layerstack/               # durable truth: manifest CAS (single linearization point), layers/leases, squash + deferred-GC (leased_layers vs lease_head_layers), MergedView, storage_lock; OWNS the snapshot/lease port adapter (moved out of occ — see HINGE below) so eos-isolated links layerstack, never occ
    eos-overlay/                  # OverlayHandle (upper/work + newest-first layer_paths), fsopen/fsmount mount, upperdir-only capture (capture+publish = 1 atomic unit)
    eos-occ/                      # publish DECISION gate: DROP/DIRECT/GATED/REJECT routes, single-worker commit-queue (N disjoint writes = 1 CAS), transaction, statuses
    eos-ephemeral/                # per-op COLLABORATIVE pipeline: fast path (direct OCC) + shell/plugin overlay → capture → PUBLISH
    eos-isolated/                 # persistent private SESSION (enter/exit, _control_plane, ns-holder orchestration, network); depends on overlay/runner/ns-holder/layerstack but NOT eos-occ → structurally CANNOT publish (audit-only)
    eos-plugin/                   # plugin dispatch: op_registry/intent model + warm per-session plugin server + bidirectional PPC channel (replaces the importlib path)
  xtask/                          # musl cross-build, release packaging, fixture export
  .github/workflows/ci.yml        # build + test + bench + release (or local CI equiv)
  CONTRACT.md                     # protocol version policy + fixture pin procedure
```

**Crate boundaries:** `eos-protocol` depends on nothing project-internal (so both `eosd` and the Python backend pin against the *same* fixture set). `eos-runner` and `eos-ns-holder` are single-threaded, syscall-only (`rustix`/`nix` + `libc` gaps), no `tokio` (the single-threaded constraint is a kernel requirement for `unshare(CLONE_NEWUSER)`/`setns`, not just a style choice — PND). `eos-daemon` may use `tokio` only if the async port justifies it (REVIEW: conservative dep set — `serde`, `serde_json`, `rustix`/`nix`, `libc`; `tokio`/`tracing`/`thiserror` only if justified).

**Explicit subsystem crates (make-folders-explicit).** The former single `eos-workspace` blob is split so each first-class architecture subsystem (`docs/architecture/sandbox/`) is its own crate and its ownership/invariants are compiler-visible. Acyclic dependency edges: `eos-layerstack` ← protocol; `eos-overlay` + `eos-occ` ← layerstack; `eos-ephemeral` ← overlay + occ + runner; `eos-isolated` ← overlay + runner + ns-holder + layerstack **but NOT `eos-occ`**. That missing edge makes the architecture's sharpest invariant — *isolated captures writes for audit but NEVER publishes* (workspaces §5.4, space-model §9.2) — a **build-time guarantee**, not a convention. Verb routing stays in `eos-daemon` dispatch: `read`/`write`/`edit` fast-path → `eos-occ`; `shell`/`glob`/`grep` → `eos-ephemeral` via `eos-runner` (the shared search/replace primitive lives in `eos-protocol`).

**Captured architecture invariants the crates MUST preserve** (from `docs/architecture/sandbox/`; each becomes a differential/parity gate, not just prose):
- **O(1) snapshot / space model** (space-model §9.1, layerstack §2.3): `acquire_snapshot()` returns lease + existing `layer_paths`, NEVER a rendered tree → lowerdir = O(1) repo bytes/op, writable = O(n × changed bytes), publish = one delta layer. Gated by the existing `test_*_lowerdir_disk_is_o1*` and `test_o1_memory_bound` suites run against the Rust runtime.
- **Manifest CAS = single linearization point** (layerstack §2.4): one mutable `manifest.json` over immutable content-addressed layers; atomic pointer-swap (Git-HEAD/Iceberg pattern).
- **Squash + deferred GC** (layerstack §2.5): `leased_layers()` (on-disk retention) vs `lease_head_layers()` (squash-keep) are distinct sets; squash is non-destructive until the retaining lease releases; OCC-safe via manifest-prefix compare + a synthetic squash lease. Depth bounded (~16-layer `mount(8)` ceiling + read amplification).
- **Capture + publish = one atomic unit per op** (overlay §3.3): walk the upperdir only; other agents never see a partial shell write set.
- **OCC batching + routes** (occ §4.3/§4.4): N disjoint file-API writes batch into ONE manifest CAS; shell captures are atomic (one publish each); `DROP`(.git)/`DIRECT`(gitignored)/`GATED`(tracked, base-hash)/`REJECT` route semantics and `ABORTED_VERSION` on stale base.
- **Isolated never publishes** (workspaces §5.4): `WRITE_ALLOWED` captures `changed_paths` for audit only; exit discards the upperdir. Enforced structurally by the absent `eos-occ` dependency above, and behaviorally by the `test_full_cycle_never_calls_occ` parity gate.

**HINGE — placement that makes the `eos-isolated ⊥ eos-occ` guarantee actually hold.** `LayerStackPortAdapter` lives today at `backend/src/sandbox/occ/layer_stack_adapter.py` but is semantically a **layer-stack forwarder** (imports only `sandbox.layer_stack.*`; its lone `occ` reference is the `LayerCommitTransaction` type annotation). It is the **single** point where `isolated_workspace` touches `occ/` (`isolated_workspace/_control_plane/pipeline_registry.py:22`), and it does so for **snapshot/lease only, never publish**. REQUIRED in the Rust split: place the snapshot/lease port in **`eos-layerstack`** (split the publish-transaction methods — needed only by ephemeral/occ — away from the snapshot/lease methods isolated needs). If this adapter stays in `eos-occ`, `eos-isolated` is forced to link `eos-occ` and the build-time no-publish guarantee silently breaks. (Verified by the iteration-5 ephemeral/isolated decomposition workflow.)

**Acyclic-graph severing (keep the crate edges one-way).** Four current upward Python edges must be severed so the crate graph stays leaf→root: (1) move the audit event-type **schema** (`daemon/audit_schema.py` — confirmed pure-`dataclass`/`typing`) into `eos-protocol`; (2)–(4) invert the daemon-side accessors (`occ_runtime_services`, `layer_stack_runtime`, `changeset_projection`/dispatch drain-gate) into **port traits** that the lower crates define and `eos-daemon` implements + injects. Confirmed one-way: `occ → overlay` only (`occ/overlay_change_conversion.py → overlay.path_change`); `overlay` has zero `occ` imports (no back-edge).

**Artifacts:** static-musl build for `x86_64-unknown-linux-musl` and `aarch64-unknown-linux-musl`. The local Phase 0 path uses `xtask package --builder rust-lld` (Cargo with `RUSTFLAGS=-C linker=rust-lld`) so the artifact is built outside the sandbox and uploaded with `put_archive`; the sandbox/container needs no Rust toolchain and no `apt`/`pkg` installs. `cross` remains an optional builder for environments that prefer containerized cross-builds; avoid it for target-sandbox setup. Outputs **`eosd-linux-amd64`** and **`eosd-linux-arm64`**, stripped, plus a `SHA256SUMS` and a `protocol_version` manifest. **Release later SIGNS each binary (Change 2):** a detached **minisign** signature (`eosd-linux-{amd64,arm64}.minisig`) is produced with the release signing key. Minisign chosen over cosign — single Ed25519 key, no PKI/OCI/sigstore machinery — consistent with the smallest-dependency-surface principle.

**Decoupling + consumption (delivery = Option A, settled §0):**
- The backend does **not** build Rust in normal operation. Phase 0 consumes a local pinned artifact by version + SHA256, plus a pinned copy of the `eos-protocol` fixtures; release-grade consumption later adds minisign.
- A `backend/src/sandbox/host/runtime_artifact/` (new, small) records the pinned `eosd` version, per-arch SHA256, the protocol version it speaks, and later the **pinned minisign public key (trust anchor)**. Host verifies SHA256 now and signature+SHA256 before release-grade upload/exec (fail-closed — see §2/AV-8).
- Versioned-protocol contract is the only coupling: bump `_eos_daemon_protocol_version` requires a coordinated release + backend pin bump (procedure in `CONTRACT.md`).

---

## 2. Integration contract (Rust ⇄ Python backend)

**Frozen, versioned JSON protocol.** Already exists at version 1 (`DAEMON_PROTOCOL_VERSION`, `DAEMON_PROTOCOL_FIELD = "_eos_daemon_protocol_version"`, `DAEMON_AUTH_FIELD`). Phase 0 freezes the *current* Python-emitted envelopes as golden fixtures; Rust must reproduce them to the **canonicalized-equal** bar (see AV-1, §4), with a separate **byte-identity** bar for the CAS digest payload only.

**Single source of truth for fixtures (mitigates protocol-drift pre-mortem #3):** the `eos-protocol` crate `fixtures/` directory is canonical. The Python backend **vendors a pinned copy** under `backend/src/sandbox/_contract_fixtures/` recording the upstream commit/tag. Both sides assert the pin matches (Rust fixtures match emitted envelopes; Python vendored-copy hash == pinned upstream hash). A drift fails the local verification path, not silently. (OQ#3 resolved: this canonical+pinned+dual-assert layout is settled.)

**The canonical fixture set MUST include the recovery contract (PV-2):** the thin-client exit codes **97 (CONNECT_FAILED)** and **98 (IO_FAILED)** as wire-observable outcomes, and the daemon **readiness envelope** `api.runtime.ready{layer_stack_root}` (the daemon keys OCC services by `layer_stack_root` — confirmed `daemon/occ_runtime_services.py:48`). Rust must emit the readiness envelope and produce the 97/98 outcomes on the matching failure conditions.

**Artifact naming + delivery:** `eosd-linux-{amd64,arm64}`, pinned by local/release version + SHA256 in `runtime_artifact/`. Host selects by container arch.

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
| **CP-4** | Throughput / OCC publish under parallel contention | CP-0 throughput at same concurrency | N parallel agents driving the **defined op set** (read_file/write_file/edit_file/shell/search verbs × OCC publish + LayerStack squash/GC ops) in N parallel sequences. **MF-1 interleave:** include **concurrent self-managed plugin writes (`apply.py` path) interleaved with primary-path publishes** to the same `layer_stack_root` under N-way contention — proving both entry points share the one writer, not just single-shot parity. | **Divergence detector (M2):** (a) canonicalized result equality per AV-1 across runtimes AND (b) equal **final-workspace-state hash** = `manifest_root_hash` + the **per-layer `layer_digest` byte-stream parity** (item 1 — not just the set of digests, the digests themselves must be byte-identical across runtimes) compared after each parallel batch, including the plugin-interleave batches; p95 publish latency ≤ CP-0 p95 | Phase 3 exit |

CP-2b is deliberately a no-regression gate, not a speedup claim: the REVIEW's guardrail is that end-to-end is bounded below by the unmeasured syscall floor, so the realized win = `runtime_saved / (syscall_floor + <1ms exec)` and cannot be quoted until CP-0 measures the floor.

**Parametric gates are accepted as a strength (OQ#2 resolved):** thresholds lock at CP-0 against the measured in-sandbox baseline. No host-proxy number is ever quoted as a gate.

**Shell-free dest-dir creation (M5, required for Phase 5).** Today the upload dest is created via in-image `mkdir -p` shell (`runtime_bundle.py:335`, confirmed). For a truly Python/shell-free image, `put_archive` must create the destination directory itself (tar entries can carry the parent dir) rather than relying on a preceding `exec("mkdir -p ...")`. CP-1b's read-only-rootfs case validates that the chosen writable dest (e.g. `/tmp`-class path) works without shell.

**Concrete CP-0/CP-1b environment (codebase-verified — §12).** CP-0/CP-1b/§7 local runs use the `sweevo-dask__dask-10042` image launched under the provider's default flags + `/eos-mount-scratch` tmpfs (recipe in §12.2). Two empirically-verified refinements fold into the gates: (i) **CP-1b item (ii) EXDEV/single-fs is sharpened** — the layer-stack storage filesystem must not itself be overlayfs (overlay-on-overlay2 fails regardless of kernel); the provider's tmpfs root satisfies this. (ii) The `EOS_DOCKER_NO_PRIVILEGE=1` escape hatch (`provider/docker/__init__.py:20`) is the ready-made driver for the pre-mortem-#2 / AV-2 capability-negative degrade test (zero caps → overlay precondition fails → fall back to `EOS_SANDBOX_RUNTIME=python`).

---

## 4. Availability checkpoints

| ID | Property | Method | Gate |
| --- | --- | --- | --- |
| **AV-1** | **Protocol parity (canonicalized-equal — M1)** | Golden fixtures (`eos-protocol/fixtures`): Rust output equals frozen Python envelopes under the **canonical form** below. A drift fails. **SF-3:** the frozen set MUST include `api.layer_metrics` (storage bytes / active leases / **manifest depth** — `daemon/builtin_operations.py:131,152`, `manifest.depth`; route `rpc/dispatcher.py:432`) — the observability surface for the manifest-depth invariant (daemon.html §6.4). | Every phase touching new verbs |
| **AV-1c** | **CAS byte-identity (narrow) — TWO persisted hashes** | **(1) `manifest_root_hash`:** Rust reproduces `json.dumps(payload, sort_keys=True, separators=(",",":"))` over `{"layers":[layer.to_dict()...]}` so SHA256 matches (`manifest.py:137`, confirmed). Achievability verified: payload is string-only — `LayerRef.to_dict() -> dict[str, str]` = `layer_id` + `path` only (`manifest.py:64`); no float/`repr` divergence. **(2) `layer_digest` (per-layer, correctness-bearing — item 1):** Rust reproduces the `update_digest` byte-stream exactly (`changes.py:145-157`): per change `kind\0` + `path\0` + (`write`→`write_content` \| `symlink`→`source_path`) + `\0`, over `aggregate_layer_changes` output. **Ordering binding:** `aggregate_layer_changes` (`changes.py:159`) is last-write-wins per path then emits in **`sorted(path)` order** (confirmed deterministic by sorted path, NOT dict-insertion order); the Rust port MUST sort identically (no Python-side ordering nondeterminism to inherit). This digest drives head-layer dedup (`publisher.py:76`), is persisted via `write_layer_digest_atomic` (`:106`), and is read back across publishes (`:171-175`). On-disk manifest uses a *different* serialization (`indent=2, sort_keys=True`, line 156) — parsed, not hashed → AV-1 canonical, NOT byte-identity. | Phases 2–3 (any path computing either CAS hash) |
| **AV-2** | **Crash containment + respawn + readiness (S4)** | Kill daemon mid-flight; host CONNECT_FAILED retry/respawn (`_CONNECT_RETRY_DELAYS_S`) reconnects; no orphaned mounts. Rust must emit `api.runtime.ready{layer_stack_root}` and the host must invalidate the cached TCP endpoint on CONNECT_FAILED then re-resolve (`daemon_client.py:445-449`, confirmed). | Phase 2 exit |
| **AV-3** | **Cancellation** | Cancel kills full process group (`start_new_session=True`, confirmed in `namespace_runner.py`); timeout cleanup unmounts overlay | Phase 1 + Phase 3 exits |
| **AV-4** | **Audit drop-free** | Audit pull under CP-4 load loses zero records | Phase 3 exit |
| **AV-5a** | **Read/idempotent shadow diff** | For **reads/idempotent ops only**, per-op sampled shadow against the *other* runtime on a separate read-only view; diff canonicalized results; alert on mismatch | Cutover gate (Phase 5) |
| **AV-5b** | **Write/publish A/B (per-sandbox, NOT per-op — M3)** | One runtime per sandbox for its lifetime (enforced by singleton socket/PID + storage flock lease). Gate = §7 differential/property tests under contention against **separate state** + production per-sandbox A/B. **Stopping rule (item 2 — outcome-class, NOT per-op byte equality, since per-op write byte-diff is forbidden by PV-1):** across N ≥ 1,000 publish ops per op-class through the Rust runtime over ≥ 1 full traffic cycle (canary = per-sandbox fraction ramped 1%→10%→50%), the Rust runtime's **outcome-class distribution** (success / conflict / error) and **error rate** must not diverge from the Python baseline beyond ε = 0 (any outcome-class divergence is a hard stop). Byte-level write equality is proven separately and offline by §7 differential tests against separate state, not by comparing two co-publishing runtimes. | Cutover gate (Phase 5) |
| **AV-6** | **Rollback** | **"Pre-write" = pre-Rust-*durable-publish*, not "no mutation".** Phase 1's ns-runner does mutate the overlay (`write_file`/`edit_file`/`shell`), but durable OCC/LayerStack **publish** (the on-disk CAS format M4 cares about) stays Python through Phase 2 — so no Rust-written on-disk state exists to roll back across yet. Phases 1–2: flip `EOS_SANDBOX_RUNTIME=python` once the flag is built; zero redeploy. **Phase 3+ (Rust durable publish):** see M4 — rollback requires forward+backward on-disk format parity (AV-7) or is forbidden. | Phases 1–2 continuous; Phase 3+ gated by M4 |
| **AV-7** | **Forward+backward on-disk format parity (M4 — enables write rollback)** | After a **Rust** publish (`os.replace(staging,layer_dir)`-equivalent, `publisher.py:104`; manifest write `manifest.py:156`; layer digest), a **Python** runtime reads the resulting layer stack and yields **canonically-equal** results to a Python-published baseline — and vice versa. **DETECTOR MUST also compare the persisted `layer_digest` stream across the round-trip (item 1), not just results.** A `layer_digest` divergence is SILENT under a results-only check: after rollback, a Python write of identical content computes the Python digest, mismatches the Rust-persisted head digest (`publisher.py:76`), and silently publishes a DUPLICATE layer instead of deduping — results stay correct, dedup invariant + layer count silently diverge. So AV-7 explicitly asserts: identical input change-sets → identical persisted `layer_digest` AND identical head-dedup decision, both Python↔Rust directions. | Phase 3 exit (REQUIRED — see M4) |
| **AV-8** | **Binary signature fail-closed (Change 2)** | Host verifies the `eosd` minisign signature against the pinned trust-anchor public key (`runtime_artifact/`) AND the SHA256 before upload/exec. **Assert REJECTION (no exec) for: unsigned, mis-signed (wrong key), and SHA-mismatched binaries.** A passing case (correctly signed + matching SHA) execs; all three failure cases fail-closed. | Phase 0 (host verify path) + every launch |
| **AV-9** | **Isolated-workspace lifecycle parity (Change 1)** | `enter/run/exit` + snapshot lease, phase timing, discard, audit semantics canonically-equal to Python; **plus the host concurrency semantics** (`isolated_workspace_lifecycle.py`, confirmed): **enter REJECTS when active background work is in-flight for the agent** (local + daemon counts), **exit DRAINS/cancels per-agent background work**, and the `lifecycle_operation` audit wrapper fires. **SF-1 — resource-cap parity:** reproduce the `_control_plane/types.py:162-183` `from_env()` defaults as SoT (`TTL_S=1800`, `TOTAL_CAP=5`, `UPPERDIR_BYTES=1 GiB`, `MEMAVAIL_FRACTION=0.5`, all confirmed); gate the `TOTAL_CAP` quota (`quota_exceeded`) and `host_ram_pressure` outcomes at parity with Python. The existing IWS concurrency + phase-budget test suites must pass against Rust. Verified on the CP-1b BYO matrix (setns path). | Phase 3.5 exit |
| **AV-10** | **Plugin dispatch parity (Change 5)** | The representative plugin (LSP/Pyright) dispatches through `eosd`'s PPC executable protocol with results canonically-equal to the Python importlib path. Asserts: (i) **all three intent modes** — `READ_ONLY` (out-of-process to warm server), `WRITE_ALLOWED` (eosd owns overlay+OCC around the plugin), self-managed (`auto_workspace_overlay=False`, `op_registry.py:227`) honored, READ_ONLY does NOT publish; (ii) the **self-managed bidirectional callback channel** works — the plugin RPCs overlay/OCC ops back to eosd and the publish path is byte-identical to today's `apply.py`; **MF-1: that callback routes through the SAME per-`layer_stack_root` single `occ-commit-queue` writer + `storage_lock` lease as the primary path (no second writer instance), verified by the CP-4 interleave under contention, not just single-shot**; (iii) the warm plugin server is spawned once per session and torn down on session end / killed process group (AV-3); (iv) plugin/LSP ops are **blocked while isolated mode is active** (existing invariant, Phase 3 ↔ 3.5). Verified on the CP-1b BYO matrix **with the Node payload present**. | Phase 3 exit |

**Canonical form (AV-1):** JSON objects compared with **keys sorted recursively**; integers exact; floats normalized and compared within tolerance **1e-9 relative** (timing/latency fields excluded from the diff by an allowlist); strings compared as decoded UTF-8 (escaping style — `\uXXXX` vs literal — is normalized away before compare). This is the parity bar for all verbs. Byte-identity is NOT required here; it is required only at AV-1c.

**PV-1 — why dual-run is split.** Two runtimes cannot share one sandbox: the daemon socket/PID are singletons with idempotent spawn (`daemon_client.py:32-33`, confirmed), `layer_stack/storage_lock.py:71` holds `fcntl.flock(LOCK_EX|LOCK_NB)` as a **single-owner lease per storage root** (confirmed), and `occ/commit_queue.py` makes CAS races impossible only *within one runtime's single `occ-commit-queue` thread*. Per-op write shadowing would double-apply and corrupt. So AV-5b is per-sandbox A/B, never per-op. **Requirement (item 4 — the lease is TWO layers, reproduce BOTH):** the single-owner guarantee is OS-flock **PLUS** an in-process refcounted mutex — `storage_lock.py:63-66,78` keeps a process-wide registry that does `refcount += 1` and shares a per-root `threading.RLock()` (confirmed). A second `flock(LOCK_EX)` from the *same* process succeeds on Linux, so the OS lease alone does NOT serialize intra-process writers; the in-process refcount/RLock does. The Rust port MUST reproduce both: the `flock(LOCK_EX|LOCK_NB)` cross-process lease on the identical lock path AND the intra-process refcounted shared-mutex serialization.

**OCC/LayerStack note (HIGH risk).** Fixtures (AV-1/AV-1c) verify single-shot parity only — they do **not** catch a concurrency-invariant divergence. The real exit gate for these two modules is the §7 differential/property test under contention + CP-4's final-workspace-state hash + AV-5b's per-sandbox A/B, NOT fixtures.

**M4 DECISION — write-phase rollback is SUPPORTED, gated on AV-7.** Phase 3 lands Rust writes; if a sandbox running Rust must roll back to Python, the Python runtime has to read Rust-published manifests/layers (`manifest.py:137/156`, `publisher.py:104` `os.replace`, all confirmed) and vice versa. We **support** rollback rather than forbid it, because forbidding it would strand every sandbox that took a Rust write with no safe exit — the worse data-safety posture. Support is **conditional on AV-7 passing** (forward+backward on-disk format parity at Phase 3 exit). *Rationale:* the on-disk format is plain JSON manifests + content-addressed layer dirs produced by `os.replace`; reproducing the exact `manifest_root_hash` AND `layer_digest` serializations (AV-1c) makes the formats interchangeable, so the parity is achievable and testable rather than a leap of faith. If AV-7 cannot be made to pass, the fallback is to **forbid** write-phase rollback and narrow AV-6 to read-only phases — but that is the explicit non-goal, surfaced as a stop-and-escalate, not a silent default.

**schema_version binding (item 5).** `manifest.py:108-112` hard-rejects `schema_version > MANIFEST_SCHEMA_VERSION` with `ManifestConflictError` (confirmed) — so a Rust write that bumps the on-disk `schema_version` makes a rolled-back Python reader **hard-fail exactly when rollback is needed**. Therefore an on-disk `schema_version` change is a **protocol-version-class coordinated event**: it is bound to the same `CONTRACT.md` coordinated-bump procedure as the wire protocol and is NEVER a unilateral Rust change. While both runtimes coexist (Phases 1–4), the Rust runtime writes the current `MANIFEST_SCHEMA_VERSION` and does not bump it.

---

## 5. Phased migration

Each phase: deliverables → anchors replaced → EXIT GATE. Python stays default until Phase 5.

### Phase 0 — Bootstrap (no behavior change)
- **Deliverables:** Cargo workspace + crates skeleton; `eos-protocol` with frozen v1 fixtures; local musl package of `eosd-linux-amd64` (arm64 package later on matching host/runner); `put_archive` on `ProviderAdapter` + Docker adapter; `runtime_artifact/` local amd64 pin; **CP-0 baseline + local artifact upload report captured + checked in**.
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

### Phase 3 — Write/publish + shell/search + background + plugin dispatch (HIGH-risk core)
- **Deliverables:** OCC write/edit **publish**; overlay shell/search; background in-flight tracking, heartbeat, cancellation, TTL cleanup. Reproduce the commit-queue serialization params (PV-3): single `occ-commit-queue` writer thread, `batch_window_s = 0.002`, `max_batch_size = 64`, `MAX_OCC_CAS_RETRIES = 3` (all confirmed `occ/commit_queue.py`); and the `storage_lock.py` `flock(LOCK_EX|LOCK_NB)` single-owner lease.
  - **SF-4 — background-lifecycle params reproduced exactly (PV-3-style; SoT = daemon.html §6.8):** `EOS_BACKGROUND_HEARTBEAT_INTERVAL_S` (heartbeat cadence) and the `ttl_sweep()` semantics — the sweep selects only entries where `idle > TTL AND active_calls == 0`; `active_calls` is incremented *before* the runtime call and decremented in a `finally`. The Rust port reproduces the same guard so an in-flight call can never be TTL-reaped.
  - **SF-5 — squash auto-trigger + second-writer guard (verify-then-name):** the squash split is `occ/maintenance.py` `AutoSquashMaintenancePolicy` (+ `_LayerSquashPort` Protocol, confirmed :21/:29) for the *trigger policy*, and `layer_stack/squash.py` `SquashPlan` / `LayerCheckpointSquasher` for the *mechanics* (confirmed) — there is no `AutoSquashMaintenancePolicy` in `squash.py`. Phase-3 task: VERIFY the squash worker publishes through the **same** `storage_lock` single-owner lease + `occ-commit-queue` (it must, given the lease is process-wide single-owner) and NAME the exact guard alongside PV-3 once located; the Rust port reproduces both the trigger policy and that single-writer guard.
  - **Plugin dispatch via PPC (Change 5 — intrinsic to the `ephemeral_workspace` port, not optional):** `eos-plugin` crate implements the op_registry/intent model + the warm per-session plugin server + the bidirectional message-id'd PPC channel (§0 PPC), replacing the `importlib` path. Node-for-Pyright ships as an optional `put_archive` payload, not a core dep. Isolated-mode-blocks-plugins gate preserved. **MF-1:** self-managed plugin OCC callbacks route through the same single writer (§0 PPC / AV-10).
- **Replaces:** write-side `occ/` + `layer_stack/` (squash/GC/lease), `ephemeral_workspace/` publish + the `plugin/` dispatch layer (`op_registry`/`runtime_api`/`overlay_child`/`overlay_dispatch`/`op_context`/`projection`; `overlay_child.py:129` importlib path retired), write verbs in `shared/`, daemon in-flight/TTL. NOT the `plugins/catalog/*` impls (scope option (b), §0).
- **EXIT GATE:** CP-4 passes (canonical equality + final-workspace-state hash); AV-1, AV-1c (CAS byte-identity), AV-3, AV-4, **AV-10 (plugin dispatch parity, all 3 intent modes + self-managed callback)** pass; **OCC/LayerStack differential+property tests under contention pass** (§7); **AV-7 forward+backward on-disk format parity passes** (gates write rollback per M4). Note: AV-5a/AV-5b are Phase-5 cutover gates, not Phase-3 exit gates (per-op write shadow is unsafe — PV-1).
- **Drain-then-swap (S3):** flipping `EOS_SANDBOX_RUNTIME` or swapping the daemon MUST drain in-flight requests first (reuse the existing per-agent inflight counter + drain event from `workspace_tool/dispatch.py`); no in-flight request crosses a runtime swap mid-call.

### Phase 3.5 — Isolated workspace (NOW IN-SCOPE — iteration 4; HIGH risk)
Placed after Phase 3 because it builds on the OCC/LayerStack core + the runner. Used on BYO/minimal images that lack Python, so it cannot stay on the Python path.
- **Deliverables:**
  - **`eosd ns-holder`** subcommand — single-threaded `unshare(CLONE_NEWUSER|NEWNS|NEWPID|NEWNET)`, hold the ns FDs, readiness/control pipe handshake (1:1 with `scripts/ns_holder.py` `ns-up`→`net-ready`→`ready`), `pause()` until SIGTERM (PND topology, §0).
  - **`eosd ns-runner` setns mode** — per isolated call, single-threaded `setns` into the holder's pre-opened FDs then exec (analog of fresh-ns mode). Ports `setns_exec.py`, `setns_overlay_mount.py`, `_setns_libc.py`.
  - **Daemon-side isolated lifecycle** (`enter`/`exit` RPC + `_control_plane/namespace_runtime.py`): spawn/teardown the holder, snapshot lease, phase timing, discard, audit.
  - **Host `isolated_workspace_lifecycle.py` ADAPTED** to drive the Rust lifecycle (preserving enter-gate-on-active-bg-work, exit-drain, `lifecycle_operation` audit).
  - **BLOCKER fix — shell-free isolated-ns networking (item-1 wrinkle).** `scripts/ns_holder.py` shells out to `ip -6 route flush` + `sysctl -w` to purge IPv6 default routes / disable RA — an **in-image-tool dependency inside the exact feature being un-deferred for minimal images** (its own docstring notes "some images strip `ip -6` or the sysctl write path"). **DECISION: port shell-free** — RA-disable + route-flush via **rtnetlink** (`RTM_DELROUTE`) and the sysctl writes via direct `write()` to `/proc/sys/net/ipv6/...`, no `ip`/`sysctl` binaries. This is consistent with the plan's syscall-not-shell ethos (overlay already uses `fsopen`/`fsmount`, not the `mount` binary). The current best-effort-degrade semantic is preserved (tolerate `/proc` write failure on kernels lacking the path) but is now tool-free. See pre-mortem #4.
  - **SF-6 — network + audit SoT (reference, do NOT inline):** the Rust port reproduces the isolated-network constants from `_control_plane/types.py` (the `10.244.0.x/24` subnet, `eos-shared0` bridge, `accept_ra=0`, veth naming `eos-iws-<short>n` per `HANDLE_PREFIX="eos-iws-"` :19, `FALLBACK_DNS=1.1.1.1` :180 — all confirmed) AND the audit-event JSONL schema (`EOS_ISOLATED_WORKSPACE_AUDIT_PATH`, `pipeline_registry.py:104`, confirmed) byte/canonically-equal. These files are the single source of truth; the plan does not duplicate the values.
- **Replaces:** `isolated_workspace/` (incl. `scripts/ns_holder.py`, `setns_exec.py`, `setns_overlay_mount.py`, `_setns_libc.py`, `_control_plane/namespace_runtime.py`, `_control_plane/types.py` constants, `pipeline_registry.py` audit sink).
- **EXIT GATE:** AV-9 (isolated lifecycle parity incl. enter-gate/exit-drain + resource-cap quota/RAM-pressure parity (SF-1) + existing IWS concurrency/phase-budget suites pass against Rust); CP-1b setns-path validation green on the full BYO matrix; AV-3 (cancellation/teardown of the holder process group).

### Phase 5 — Cutover
- **Deliverables:** make `EOS_SANDBOX_RUNTIME=rust` default after the canary; remove the Python bundle, `daemon/scripts/launch_daemon.sh`, `thin_client.py`, the `_PYTHON_CANDIDATES` probe, `chunked_upload.py` runtime-bundle path, `runtime_bundle.py` tar finalize, the Python `isolated_workspace/` `setns` scripts (no Python `setns` fallback survives — iteration 4), **and the Python `ephemeral_workspace/plugin/` dispatch layer** (the importlib path; the `plugins/catalog/*` impls remain as payloads per scope option (b) — iteration 5); resolve `install_git.sh` (drop or shell-free replacement); land the shell-free `put_archive` dest-dir creation (M5).
- **EXIT GATE:** AV-5a (read shadow) + AV-5b (per-sandbox write A/B, N≥1,000/op-class, zero outcome-class divergence, ≥1 full traffic cycle) pass; AV-8 (signature fail-closed) + AV-9 (isolated parity) + AV-10 (plugin parity) green; CP-1b viability green on the full BYO matrix incl. setns + plugin-payload paths; full Definition of Done (§9). Rollback path (AV-6/AV-7) verified one last time before deletion.

---

## 6. Pre-mortem (4 concrete failure scenarios)

1. **OCC/LayerStack concurrency-invariant divergence** — *likelihood MED, impact HIGH (data corruption / lost writes).* **Corrected mechanism (PV-3):** the Python locking is ALREADY explicit, not GIL-implicit — a reentrant `threading.RLock`, a dedicated `occ-commit-queue` OS thread, and `run_sync_in_executor` offload (`occ/commit_queue.py`, `occ/service.py`, confirmed). The real risk is the **reentrant→non-reentrant restructuring**: a naive 1:1 port to Rust `std::sync::Mutex` (non-reentrant) **deadlocks** wherever the Python `RLock` is re-acquired on the same thread. The port must restructure those re-entrant sections AND reproduce the commit-queue serialization params (single-writer, `batch_window_s=0.002`, `max_batch_size=64`, CAS budget `3`). **Mitigation (mechanism-agnostic — unchanged):** §7 differential + property tests under parallel contention as the Phase 3 exit gate (not fixtures), plus CP-4's final-workspace-state hash; AV-5b per-sandbox A/B before flipping default; rollback path per M4.
2. **Kernel-feature / musl-aarch64 variance across BYO images breaks overlay mount** — *likelihood MED, impact HIGH (tool calls fail on real customer images).* Raw `fsopen/fsmount/move_mount` and unprivileged user-ns overlay are kernel-version and config dependent; an arm64 musl build may behave differently. **Mitigation:** a Docker capability matrix (mirror E0 in `sandbox_perf_experiments_PLAN.md`) run in CI across kernel/arch images; `eosd` emits a structured capability-probe on startup; fall back to `EOS_SANDBOX_RUNTIME=python` on probe failure during coexistence.
3. **Protocol drift between the external repo and the backend** — *likelihood MED, impact MED (silent wire incompatibility post-deploy).* Two repos evolving "the same" protocol. **Mitigation:** `eos-protocol` is the single fixture source of truth; backend vendors a pinned hash; both sides assert the pin in local/release verification; protocol-version bump requires the coordinated release procedure in `CONTRACT.md`; the daemon rejects mismatched `_eos_daemon_protocol_version` at handshake.
4. **Isolated-ns networking depends on in-image tools on the exact minimal images that justify the un-defer (iteration 4)** — *likelihood MED-HIGH if ported naively, impact HIGH (isolated-workspace IPv6 egress hardening silently broken on minimal BYO images).* `scripts/ns_holder.py` shells out to `ip -6 route flush` + `sysctl -w` (confirmed; its docstring admits images strip these). A 1:1 port reintroduces the exact in-image-tool dependency the migration removes. **Mitigation:** the Phase 3.5 BLOCKER-fix decision — port the route-flush/RA-disable to **rtnetlink + direct `/proc/sys` writes**, no `ip`/`sysctl` binaries; CP-1b's setns path on the minimal/read-only-rootfs matrix images verifies isolated egress hardening works tool-free; keep the best-effort-degrade semantic where the kernel lacks the `/proc` path.

---

## 7. Expanded test plan (unit / integration / e2e / observability)

- **Unit (Rust, `/sandbox` CI):** envelope (de)serialization round-trips; verb logic; OCC publish/conflict resolution; LayerStack lease/squash/GC state transitions; **property tests** (proptest) over OCC operation sequences asserting invariants (no lost write, monotonic version). → AV-1, CP-4.
- **Integration (Rust):** daemon ↔ ns-runner over the real socket; overlay mount inside a privileged-enough test container across the kernel/arch matrix; cancel/timeout process-group teardown. **Isolated-workspace lane (Change 1):** `eosd daemon` spawns `eosd ns-holder` → handshake → `eosd ns-runner` setns-enter → exec → exit teardown; assert holder process-group dies on exit and the shell-free (rtnetlink + `/proc/sys`) IPv6 hardening runs with `ip`/`sysctl` absent. → AV-2, AV-3, AV-9, pre-mortem #2 + #4.
- **Differential (cross-runtime, the HIGH-risk gate):** drive identical operation sequences through Python and Rust **against separate state** under N-way parallel contention; assert **canonically-equal** typed results (AV-1), **byte-identical CAS digest** (AV-1c), and equal final-workspace-state hash (CP-4). Plus the M4 forward+backward parity check (AV-7): Python reads Rust-published on-disk state and vice versa. **Isolated-lifecycle differential (Change 1):** enter/run/exit + snapshot lease + phase timing + discard + audit canonically-equal Python↔Rust; the existing IWS concurrency/phase-budget suites run against Rust. **Plugin differential (Change 5):** the same LSP/Pyright ops (hover READ_ONLY, an apply WRITE/self-managed) through both the Python importlib path and the Rust PPC path → canonically-equal results + identical publish-path bytes for self-managed `apply.py`. → pre-mortem #1, Phase 3 + 3.5 exits.
- **E2E (backend automation/local runs):** `bench_sandbox_e2e.py`-driven full tool calls through the host proxy with `EOS_SANDBOX_RUNTIME=rust`; CP-1b put_archive viability + setns-path validation across the locked BYO-image matrix (5.11+/LTS × amd64/arm64 × non-root × read-only-rootfs); host signature-verify fail-closed cases (AV-8); **plugin dispatch through PPC with the Node payload uploaded via `put_archive`, including isolated-mode-blocks-plugins (AV-10)**. → CP-1b, CP-2b, CP-4, AV-4, AV-8, AV-10.
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

- **Option A — external Cargo workspace + pinned released binary artifact (RECOMMENDED).** *Pros:* clean repo separation; backend never builds Rust; explicit version+SHA pin; release ownership clear. *Cons:* cross-repo changes are two PRs; protocol bumps need a release dance (mitigated by `CONTRACT.md` + dual-side pin assert).
- **Option B — git submodule, vendored/built in backend CI.** *Pros:* atomic cross-repo commits; one source tree. *Cons:* backend CI must own a Rust+musl toolchain (reintroduces build coupling the rewrite is trying to shed); submodule ergonomics are error-prone.
- **Option C — monorepo subdir (`backend/../sandbox` built together).** *Pros:* simplest single-PR workflow. *Cons:* violates the "external project" requirement; couples build/release cadence; tempts shared deps.

→ **SETTLED: Option A** (OQ#1 resolved — reviewer confirmed no atomic cross-repo need justifies B). Invalidation: B reintroduces a build-time toolchain dependency in the backend (counters Driver 3 and the image-agnostic build story); C is excluded by the explicit "external project" requirement.

**Mode:** DELIBERATE — pre-mortem (§6) + expanded test plan (§7) included.

**ADR**
- **Decision:** port the in-sandbox runtime (INCL. isolated-workspace, iteration 4; INCL. plugin dispatch via the PPC executable protocol, iteration 5) to a Rust external Cargo workspace at `/sandbox`, delivered as a pinned + **minisign-signed** released `eosd` binary (3 subcommands: `daemon`/`ns-runner`/`ns-holder`), behind `EOS_SANDBOX_RUNTIME` with measure-first parametric perf gates.
- **Drivers:** the three above.
- **Alternatives considered:** delivery A/B/C (this doc); Go-for-both and Python-freeze (prior REVIEW — invalidated); ns-holder as in-daemon thread (rejected — kernel requires single-threaded caller for `unshare(CLONE_NEWUSER)`/`setns`, so the holder MUST be a dedicated single-threaded child — PND, §0); cosign signing (rejected for minisign — no PKI/OCI surface); **one-shot-exec plugin protocol (rejected — Pyright cold-starts by indexing the project, so per-op exec re-pays multi-second startup; chose a long-lived warm per-session plugin server, PPC §0)**; **rewriting `plugins/catalog/lsp` Pyright wrapper in Rust (rejected — over-build for one plugin; keep the Python wrapper as a payload)**.
- **Why chosen:** A is the only option satisfying all three drivers; Rust-for-both and single-binary are inherited settled decisions. The PND subprocess topology is forced by the kernel, not chosen. The PPC long-lived bidirectional channel is forced by Pyright's persistent-server nature + self-managed plugins' need to RPC OCC ops back.
- **Consequences (blast radius grew with iterations 4 + 5):** two repos + coordinated protocol-bump procedure (now incl. on-disk `schema_version` — item 5); backend gains `put_archive` + the net-new `EOS_SANDBOX_RUNTIME` flag/dispatch fork + a host-side AF_UNIX local-fallback connector + **minisign signature verification (in-scope fail-closed gate, AV-8)**; upload stops scaling with artifact size. **Isolated-workspace is in-scope (+2,871 LOC → 19,474 total) with a daemon-owned `eosd ns-holder` subprocess + shell-free (rtnetlink + `/proc/sys`) IPv6-hardening rewrite (pre-mortem #4).** **Plugin dispatch is rewritten as the PPC executable protocol (`eos-plugin` crate): the importlib path is retired, READ_ONLY moves out-of-process, self-managed plugins get a bidirectional callback channel; language runtimes (Node-for-Pyright) become optional `put_archive` payloads, NOT core deps (AV-10).** `host/isolated_workspace_lifecycle.py` is ADAPTED. **The no-Python-in-image payoff lands ONLY at Phase 5 — full migration risk (incl. HIGH-risk OCC/LayerStack, isolated-workspace, AND plugin dispatch) is carried through Phases 1–3.5 with both runtimes maintained.** Write-phase rollback is constrained by M4 (AV-7 on-disk parity).
- **Follow-ups:** none of iterations 4–5's items remain deferred (isolated-workspace, signing, OCC cache-lock profiling, CP-1b matrix, plugin dispatch are all now in-scope). Residual: minisign **key rotation** for `runtime_artifact/` (operational, post-GA); **folding the LSP plugin's protocol-server harness into the Node runtime to drop the Python payload** (optimization — currently the LSP plugin payload is Node + a thin Python wrapper).

---

## 9. Definition of Done

- **Core `eosd` starts in a Docker image with no Python/Node/Rust/Go/bash/tar/gzip/base64 in the CORE and no external `unshare`** — verified on the CP-1b BYO-image matrix (incl. non-root + read-only-rootfs). **Plugin payloads bring their own runtime** (Node-for-Pyright uploaded via `put_archive` only when the LSP plugin is enabled — iteration 5); the core stays runtime-free.
- CP-0 baseline (incl. kernel version + userns/overlay config) committed; CP-1, CP-1b (incl. setns-path validation), CP-2a, CP-2b, CP-3, CP-4, CP-5 pass against it; CP-2b shows no end-to-end regression; CP-5 Rust cache-lock wait ≤ Python under LRU-eviction churn.
- AV-1 (canonical) + AV-1c (CAS byte-identity, both hashes) + AV-2…AV-10 pass; OCC/LayerStack differential+property tests green under contention; AV-7 forward+backward on-disk parity proven; AV-8 signature fail-closed proven (unsigned/mis-signed/SHA-mismatch all rejected); AV-9 isolated-lifecycle parity + IWS suites green against Rust; AV-10 plugin dispatch parity (3 intent modes + self-managed callback + Node payload) green.
- `put_archive` is the upload path (shell-free dest-dir creation); base64-over-exec runtime-bundle path, `launch_daemon.sh`, `thin_client.py`, `_PYTHON_CANDIDATES` probe, the Python bundle, the Python `isolated_workspace/` `setns` scripts, AND the Python `ephemeral_workspace/plugin/` importlib dispatch layer removed; `install_git.sh` dropped or shell-free; isolated-ns IPv6 hardening is shell-free (rtnetlink + `/proc/sys`).
- Host AF_UNIX local-fallback connector reproduces the 97/98 exit-code contract and TCP-endpoint-cache invalidation; readiness envelope emitted; host verifies the minisign signature + SHA256 fail-closed before exec.
- `EOS_SANDBOX_RUNTIME=rust` default after a clean per-sandbox canary (AV-5b: N≥1,000/op-class, zero outcome-class divergence, ≥1 full traffic cycle); rollback path verified.
- **`isolated_workspace` runs on `eosd` with NO Python in image** (iteration 4 — the Python `setns` exception is REMOVED): `eosd ns-holder` + `eosd ns-runner` setns-mode + adapted `host/isolated_workspace_lifecycle.py`, verified on the CP-1b BYO matrix incl. read-only-rootfs.
- **Plugins dispatch via the PPC executable protocol** (iteration 5 — the `importlib` path is REMOVED): `eos-plugin` runs a warm per-session plugin server over the bidirectional AF_UNIX channel; all 3 intent modes + self-managed `apply.py` callback path verified byte-identical; the `plugins/catalog/*` impls (only `lsp`/Pyright today) ship as `put_archive` payloads that carry their own runtime; isolated-mode-blocks-plugins invariant preserved.

---

## 10. Open questions — RESOLVED (iterations 2–4)

Iteration-2 resolutions (folded in):
1. **Delivery mechanism → Option A (pinned released artifact).** SETTLED (§0, §8).
2. **Parametric gates → ACCEPTED as a strength.** Thresholds lock at CP-0; no host-proxy number quoted as a gate (§3).
3. **Fixture ownership → `eos-protocol/fixtures` canonical + backend-vendored pinned copy + dual-side assert,** recovery contract (97/98 + readiness) in the canonical set (§2).
4. **Phase 4 (isolated-workspace / `setns`) → ~~DEFERRED~~ → NOW IN-SCOPE (iteration 4).** User confirmed isolated mode is used on BYO/minimal images lacking Python, so it cannot stay on the Python path. Ported in **Phase 3.5**; Python `setns` exception removed from DoD §9.
5. **Canary → per-sandbox A/B, ≥1 full traffic cycle, N≥1,000/op-class, zero outcome-class divergence** (AV-5b / M3, §4).

Iteration-4 resolutions (the four previously-residual/deferred items, now in-scope):
- **Isolated-workspace** → in-scope, Phase 3.5, PND topology (§0/§5).
- **Artifact signing/provenance** → in-scope minisign fail-closed gate AV-8 (§1/§2/§4/§8), no longer S5 follow-up.
- **CP-1b BYO-image matrix** → locked concretely (5.11+/LTS × amd64/arm64 × non-root × read-only-rootfs × setns-path) (§3), no longer residual.
- **OCC cache-lock contention** → in-scope perf gate CP-5 (§3/§7), no longer REVIEW §5.1 follow-up.

Iteration-5 resolution (plugin runtime model — was silently folded into "plugin dispatch", now explicit):
- **Plugin runtime contract** → in-scope **PPC executable protocol** (§0 PPC, `eos-plugin` crate §1, Phase 3 §5, AV-10 §4, §7, DoD §9). The `importlib` path (`overlay_child.py:129`) is replaced; READ_ONLY moves out-of-process; self-managed plugins (`op_registry.py:227`) get a bidirectional callback channel; the warm per-session server handles Pyright's persistent nature. Scope option (b): `plugins/catalog/*` impls kept as runtime-carrying `put_archive` payloads; core stays Node-free.

**Open sub-point for the Architect (iteration 5):** the LSP plugin payload currently = Node (Pyright) + a thin Python protocol-server wrapper, so an enabled LSP plugin needs both a Node and a Python payload. Smallest pick taken: keep the Python wrapper now (least churn to working Pyright logic); fold it into the Node runtime as a follow-up to drop the Python payload entirely.

**Residual (operational, post-GA, not blocking):** minisign key-rotation procedure for `runtime_artifact/`; LSP-payload Node-only fold (drop the Python wrapper).

---

## 11. Resulting structure & loose-coupling contract

### Top-level layout — two siblings, one-way dependency

```
EphemeralOS/
├─ backend/                                 # Python — the HOST control plane (keeps its posture)
│  └─ src/
│     ├─ engine/  task_center/  tools/      # OUT OF SCOPE; reach the sandbox ONLY via sandbox.api
│     └─ sandbox/                           # host-side package (MANAGES the sandbox; never runs IN it)
│        ├─ api/                            # api.v1.* operation contract — UNCHANGED
│        ├─ provider/                       # Docker adapter + put_archive + minisign+SHA verify
│        ├─ host/
│        │  ├─ daemon_client.py             # ADAPTED: protocol client + EOS_SANDBOX_RUNTIME fork + AF_UNIX fallback (97/98)
│        │  ├─ isolated_workspace_lifecycle.py  # ADAPTED: drives the Rust IWS lifecycle
│        │  ├─ bootstrap.py                 # ADAPTED
│        │  └─ runtime_artifact/            # NEW — the entire consumer-side coupling surface
│        │     ├─ __init__.py               #   pinned eosd version, per-arch SHA256, minisign pubkey, protocol_version
│        │     └─ (eosd-linux-{amd64,arm64} fetched+verified at deploy)
│        ├─ shared/                         # models.py etc. — the PYTHON SIDE of the data contract (STAYS)
│        ├─ _contract_fixtures/             # NEW — vendored, pinned copy of eos-protocol/fixtures
│        └─ (occ/ layer_stack/ overlay/ daemon/ ephemeral_workspace/ audit/  → REMOVED at Phase 5)
│
└─ sandbox/                                 # NEW standalone Cargo workspace — the IMPLEMENTATION
   └─ crates/
      ├─ eos-protocol/                      # ← THE CONTRACT (single source of truth) + fixtures/
      ├─ eosd/                              # binary: daemon | ns-runner | ns-holder
      ├─ eos-daemon/  eos-runner/  eos-ns-holder/  eos-plugin/
      ├─ eos-layerstack/  eos-overlay/  eos-occ/  eos-ephemeral/  eos-isolated/   # 5 explicit subsystem crates (§1); eos-isolated ⊥ eos-occ
      └─ xtask/  tests/  benches/           # (full tree in §1)
```

### The coupling boundary — EXACTLY three things cross `backend ↔ /sandbox`

1. **Wire protocol** — versioned newline-delimited JSON envelopes (`_eos_daemon_protocol_version`), the `api.v1.*` operations, the readiness envelope (`api.runtime.ready{layer_stack_root}`), and the 97/98 exit codes. SoT: `eos-protocol`.
2. **Data-type contract** — `shared/models.py` (Python) ↔ `eos-protocol` (Rust): two representations of the same types, kept **canonically-equal** by golden fixtures (AV-1), **byte-identical** for CAS payloads (AV-1c).
3. **Binary artifact** — `eosd-linux-{arch}`, consumed via `runtime_artifact/` by version + SHA256, with minisign added for release-grade provenance. Nothing else crosses.

### Loose-coupling invariants (the rules that keep it loose)

- **One-way dependency.** `backend` depends on a *released, pinned artifact* of `/sandbox`. `/sandbox` depends on **nothing** in `backend` — no import, no path, no build step. It is a standalone Cargo workspace.
- **No build coupling.** Backend never compiles Rust in normal operation; it consumes the pinned artifact. Artifact build/release verification is independent from backend runtime (Option A).
- **No internals leak (verified, must stay true).** Today: **0** imports from surviving backend (engine/task_center/tools/api/host/provider) into `occ`/`layer_stack`/`overlay` internals; `api/` touches only `shared.models` + audit schema; the sole `ephemeral_workspace` reach is the 2-site plugin host glue. The migration must **preserve** this — add a CI/lint guard that fails on any new backend import of an `eosd`-internal module. The boundary is `shared.models` + `api.v1` + audit schema, nothing deeper.
- **Versioned, dual-asserted contract.** A protocol or on-disk `schema_version` bump is a coordinated, explicit event (`CONTRACT.md`); both sides assert the fixture pin (§2), so drift fails verification, never silently.
- **Paths defined once.** Socket/mount/artifact paths that cross the boundary live in `eos-protocol` ↔ a thin Python `paths` shim, not scattered.
- **Replaceability is the test of looseness.** Because the only coupling is the protocol + the pinned binary, `eosd` is swappable — which is literally how `EOS_SANDBOX_RUNTIME=python|rust` selects an implementation per sandbox. If the flag can flip, the coupling is loose by construction.
- **Plugins sit on the far side of the same boundary.** `eosd` spawns Python/Node plugin payloads over the PPC process boundary; the backend never imports or sees them.

### What this buys
`/sandbox` is developed/versioned/released on its own cadence (the backend just bumps a pin); the Python runtime can remain a fallback indefinitely (read-phase rollback, AV-6) precisely because both sides speak one versioned contract; and a third implementation could be dropped in without touching the backend.

---

## 12. Verified environment & configuration (codebase-sourced — implementation-prep addendum)

> Every value below was verified against the live codebase or by probing the test image on **2026-05-31** (file:line anchors given). This is the single reference for the sandbox / Docker / plugin runtime configuration the Rust runtime **inherits** — the port must reproduce the *actual* launch contract, not an assumed one. §0, §2, §3 (CP-0/CP-1b), and §7 point here.

### 12.1 Docker launch contract — the flags `eosd` runs under

Source: `backend/src/sandbox/provider/docker/client.py`, `…/docker/__init__.py`, `backend/src/sandbox/overlay/writable_dirs.py`.

The Docker provider launches sandbox containers **non-privileged by default** (NOT `--privileged`). `eosd` is uploaded via `put_archive` and launched inside this same container, so the Rust overlay / ns-runner / isolated paths must work **within this cap envelope**:

- **Default run flags** (`DEFAULT_RUN_FLAGS`, `client.py:25-32`, confirmed):
  - `--cap-add=SYS_ADMIN` — `unshare -Urm` + the mount-syscall overlay mount (`overlay/kernel_mount.py`) + isolated `setns(CLONE_NEWUSER|CLONE_NEWNS)`.
  - `--cap-add=NET_ADMIN` — isolated-workspace networking (`eos-shared0` bridge, MASQUERADE/IMDS nftables, per-workspace veth, rtnetlink). `SYS_ADMIN` is **not** a superset of `NET_ADMIN` for these, so the cap is granted explicitly (`client.py:18-22`).
  - `--security-opt seccomp=unconfined` + `--security-opt apparmor=unconfined`.
  - Sufficiency is verified by `backend/scripts/preflight_docker_a2_caps.sh` or the local Docker-backed benchmark probes (5 probes: `unshare -Urm`, private-mount-ns detect, overlay mount/umount, bridge create/delete, nftables create/delete; Linux-only, no-ops elsewhere).
- **Overlay writable tmpfs** (`client.py:40-41,61-68`): the provider also mounts `--tmpfs /eos-mount-scratch:rw,size=2g,mode=1777` by default, because Docker Desktop's overlay2 storage driver cannot host an overlay mount whose upper/work dirs live on the container rootfs (`__init__.py:43-54`). `overlay_writable_root()` → **`/eos-mount-scratch/eos-sandbox-runtime`** (`writable_dirs.py:13`); ephemeral overlay upper/work (`ephemeral_workspace/pipeline_registry.py:155`) and isolated scratch (`isolated_workspace/_control_plane/pipeline_registry.py:101`) allocate under it. Healthy runs report `mount_mode=private_namespace` (`daemon/builtin_operations.py:215`).
- **Escape hatches** (`__init__.py:15-32`, `client.py:44-68`): `EOS_DOCKER_PRIVILEGED=1` → `--privileged` (escape only); `EOS_DOCKER_NO_PRIVILEGE=1` → zero added caps (capability-negative tests — the ready-made AV-2/pre-mortem-#2 degrade driver); `EOS_DOCKER_DISABLE_OVERLAY_WRITABLE_TMPFS=1`; `EOS_DOCKER_OVERLAY_WRITABLE_TMPFS_OPTIONS`.

**Port implications:** `eos-runner`/`eos-overlay`/`eos-isolated` target the default `SYS_ADMIN`+`NET_ADMIN`+unconfined cap envelope with overlay upper/work on the `/eos-mount-scratch` tmpfs. CP-1b item (ii) "storage root + staging on ONE filesystem" is satisfied structurally — `layer_stack` keeps `staging/` and `layers/` under one `storage_root` (`layer_stack/paths.py:107-108`, confirmed) — and is **sharpened** by 12.2: that filesystem must not itself be overlayfs.

### 12.2 Test/dev sandbox image — the concrete Linux env

Image: **`sweevo-dask__dask-10042`** (= `xingyaoww/sweb.eval.x86_64.dask_s_dask-10042`, id `6e8faf434f2f`, 8.69 GB) — a SWE-bench eval image for `dask`, used as the representative BYO sandbox for local Rust-runtime testing and as the concrete image behind CP-0/CP-1b/§7. Verified facts (probed 2026-05-31):

| Property | Value | Bearing on the plan |
|---|---|---|
| Kernel | `6.10.14-linuxkit` (Docker Desktop VM) | ≥ CP-1b 5.11+ floor ✓ (unprivileged overlayfs-in-userns supported) |
| OS / arch | Ubuntu 22.04.4 LTS, **linux/amd64** | amd64 leg of CP-1b; on Apple-silicon dev hosts runs **emulated** (platform warning) → arm64-native coverage still requires an arm64-native Docker host or explicit local runner (CP-1b both-arches) |
| Python | 3.10.14 (`/opt/miniconda3/envs/testbed`) | runs the Python baseline + the cross-runtime differential side (project floor ≥3.10) |
| In-image tooling | gcc 11.4, GNU tar 1.34, `/usr/bin/unshare` present; **no cargo/rustc** | consistent with the "no in-image runtime" goal: `eosd` ships as a uploaded static binary, never built in-image |
| Kernel knobs | `overlay` in `/proc/filesystems`; `max_user_namespaces=15655`; no `unprivileged_userns_clone` knob (mainline, not the Debian/Ubuntu patch) | overlay + userns available at kernel level |

**Verified capability ladder (the empirical reason the provider flags + tmpfs exist):**
1. **Default `docker run`** → `unshare -Urm` = `Operation not permitted` (default Docker seccomp blocks unprivileged userns/mount).
2. **+ provider default flags** (`seccomp=unconfined` + `--cap-add=SYS_ADMIN`) → `unshare -Urm` **succeeds**.
3. Under (2), `mount -t overlay` with upper/work on the **container overlay2 rootfs / `/tmp`** → **fails** (`wrong fs type, bad option, bad superblock` — overlayfs refuses an overlay upperdir).
4. Under (2), the same overlay with upper/work on a **tmpfs** (`/dev/shm` or a mounted tmpfs, i.e. the provider's `/eos-mount-scratch`) → **succeeds**, content correct.

⇒ the Rust overlay/ns-runner path is fully testable in this image **iff** launched with the provider's default flag set **and** the overlay storage root is a non-overlay fs (tmpfs). The real runtime uses the raw `fsopen`/`fsmount`/`move_mount` API (not the `mount(8)` binary probed here); the fs-type constraint is identical.

**Local test recipe (CP-0 / CP-1b / §7):**
```
docker run --rm \
  --cap-add=SYS_ADMIN --cap-add=NET_ADMIN \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  --tmpfs /eos-mount-scratch:rw,size=2g,mode=1777 \
  sweevo-dask__dask-10042 <eosd ...>
```
Equivalently, launch through the Docker provider with default env so the flags + tmpfs are applied automatically. Build `eosd` on the host (musl static) and `put_archive` it in — the image has no Rust toolchain and should not need `apt install`/`pkg install`.

### 12.3 Plugin runtime configuration — LSP/Pyright payload

Source: `backend/src/plugins/catalog/lsp/setup.sh`, `…/runtime/*`, `backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py`.

- **Payload runtime = Node 22 + Pyright** (`setup.sh:2,8`, confirmed): `NODE_VERSION=${EOS_NODE_VERSION:-22.13.1}`, downloaded from `registry.npmmirror.com` → `nodejs.org` fallback (`EOS_NODE_DOWNLOAD_URLS`, `:27`); arch map `x86_64→x64`, `aarch64|arm64→arm64` (`:21-22`); Pyright via `npm install -g --omit=optional pyright@${PYRIGHT_VERSION}` (`:51`). **The installer is a shell script** today — so the LSP *payload* install needs `sh`+`npm`+`curl`+`xz`. This does not violate the **core's** no-shell goal (payloads bring their own runtime — scope option (b), §0), but sharpens §0's open sub-point: the PPC harness must either keep `setup.sh` at plugin-enable time or ship a prebuilt Node tarball via `put_archive` (the corrected installer reference — the plan's earlier `install.py` mention does not exist; the real installer is `setup.sh`).
- **Intent dispatch** (`op_registry.py:16-24`, confirmed): `Intent.READ_ONLY` → **in-process in the daemon** (no per-call overlay, no namespace child, no `publish_cycle`; must query a long-lived `PluginService` like `PyrightSession`); `Intent.WRITE_ALLOWED` → overlay+OCC publish via `run_plugin_op_with_workspace_overlay`; `Intent.LIFECYCLE` → rejected at registration (not plugin tool dispatch). Orthogonal axis `auto_workspace_overlay=False` (`op_registry.py:73,226`) = self-managed publish (LSP `apply.py`). `overlay_child.py:129` is the `importlib.import_module("plugins.catalog.{name}.runtime.server")` path the PPC replaces.
- **Warm-server keying** confirmed: a long-lived Pyright child **per layer-stack root** (`plugins/catalog/lsp/plugin.md:34`; `runtime/session_manager.py:1` — "Layer-stack-root keyed cache of Pyright sessions") — matches PPC's warm per-session server + AV-3 teardown and MF-1's per-`layer_stack_root` single-writer keying.

---

## 13. Phase 0 implementation status & source-verified corrections (landed)

> Phase 0 (Bootstrap) code/tooling is **implemented and locally verified** at `/sandbox`; `bench/baseline-amd64.json` captures the dask-image CP-0/CP-1 baseline, and `bench/local-eosd-{amd64,arm64}-upload.json` capture the local build/package/upload handoff. The current closeout path intentionally does **not** use GitHub CI: build/package on the host, upload the static `eosd` into the sandbox/container with `put_archive`, verify SHA/readback/mode/version. Release-grade minisign provenance and the broader CP-1b image matrix remain later gates. The HIGH-risk Linux-syscall + concurrency crates are faithful **skeletons** (module-doc invariants + `// PORT backend/...:line` anchors + `todo!()`), not yet ported logic — Phases 1–3.5 fill them, gated on the dask Linux container + the Python differential harness (§12). The corrections below were found by *executing* the live Python during extraction and supersede the cited spots earlier in this plan.

### 13.1 What landed (verified `2026-05-31`)
- **`/sandbox` Cargo workspace, 11 crates + `xtask`.** `cargo check --workspace` green (all 12); `cargo clippy --workspace` green at the workspace deny-level gate (`correctness`/`suspicious` = deny) — only warn-level `unused_variable`/dead-code/doc warnings remain in the `todo!()` skeletons; `cargo fmt --all --check` clean. Toolchain `cargo 1.96`. `xtask package` now builds/packages `eosd-linux-{amd64,arm64}`, writes binary-only `SHA256SUMS`, `protocol_version`, per-artifact JSON manifests, and optional minisign signatures. Default builder is local `rust-lld`, which produced `sandbox/dist/eosd-linux-amd64` as a static PIE and `sandbox/dist/eosd-linux-arm64` as a static aarch64 ELF on macOS without installing packages in the target sandbox/container; both artifacts were runtime-upload verified by `backend/scripts/build_upload_eosd_docker.py`.
- **`eos-protocol` fully implemented + tested**: `version`/`envelope`/`cas`/`audit`/`models`/`canonical` modules; **29 tests green incl the 18 executed CAS golden fixtures** (`fixtures/cas/cases.json`) and envelope round-trip/canonical fixtures. This is the only crate fully verifiable on macOS.
- **Skeletons** for layerstack/overlay/occ/ephemeral/isolated/plugin/runner/ns-holder/daemon/eosd: 546 `// PORT` anchors + 19 `todo!()` bodies; `eos-daemon` is the only tokio crate and `impl`s the inverted port traits the lower crates define — `LayerStackRuntimePort` + `ChangesetProjectionPort`, and `OccServicesInjector` implements **both** `eos_occ::OccRuntimeServicesPort` and `eos_ephemeral::OccRuntimeServicesPort` (severing #2; the OCC injector returns the per-`layer_stack_root` single-writer, MF-1-aware).
- **Golden fixtures + contract specs** live at `sandbox/crates/eos-protocol/fixtures/` and `sandbox/docs/contract/01-06`; the Rust standard handed to all builders is `sandbox/docs/RUST-GUIDANCE.md`.
- **Python-side Phase 0 (surgical)**: `put_archive` on the `ProviderAdapter` Protocol + Docker adapter (async, `asyncio.to_thread` → `container.put_archive`) + Daytona `NotImplementedError` stub; `host/runtime_artifact/__init__.py` pins both local artifacts (`0.1.0-local.20260531`, amd64 SHA256 `c81993538d4cfb6425e1a00f91d38d0a85dd07a1706907c3b07db6faf5a5629e`, arm64 SHA256 `6edbe7bdc7bb4d6414b2b331d58857b1ce55bcf61bd391f34f34b36bdba716c6`, protocol `1`; minisign empty until AV-8); `EOS_SANDBOX_RUNTIME=python|rust` validated no-op host read (dispatch fork remains Phase 2); `_contract_fixtures/` vendored from `eos-protocol/fixtures` with hard-pinned `pin.json` (`fixtures_sha256=3d62ff3017bf1b1a76e36de08ea4a3185d9640cb9ca98f7e4a1796b153aab221`) and a hard-fail pin assert. `bench_sandbox_e2e.py --phase0` captures CP-0 and CP-1 metrics in Docker mode, and `build_upload_eosd_docker.py` verifies local artifact upload without installing packages in the sandbox image. `bench/baseline-amd64.json` captured on `sweevo-dask__dask-10042:latest`: upload `3957.262 ms`, daemon cold-start `816.200 ms`, idle RSS `36,796 KiB`, Python process-start p50 `398.149 ms`, heartbeat p50/p95 `1.235/2.341 ms`; CP-1 passed for 1.5/3.0 MiB with SHA parity and `put_archive` at `19.310/31.074 ms` vs base64 `22,075.007/44,192.962 ms`. Upload reports: amd64 dask image uploaded in `8.121 ms`; arm64 `python:3.11-slim` image uploaded in `8.444 ms`; both targets report missing `rustc`/`cargo`, SHA readback matched, mode `0755`, direct exec returned `eosd 0.1.0`.

### 13.2 Build-time guarantee — what actually holds
- **`eos-isolated ⊥ eos-occ` HOLDS as a true build-time guarantee** (`cargo tree -p eos-isolated` has **no** `eos-occ` edge, direct or transitive) → isolated structurally cannot publish (workspaces §5.4).
- **`eos-plugin` has no *direct* `eos-occ` edge but reaches it *transitively* via `eos-ephemeral`** — this is correct and intended: plugins *do* publish (WRITE_ALLOWED + self-managed), so the no-publish guarantee is **isolated-only**; for plugin, MF-1 (route the self-managed OCC callback through the *same* single `occ-commit-queue` writer) is a **runtime gate**, not a compiler-enforced edge. The `eos-plugin` crate doc states MF-1 explicitly.

### 13.3 Source-verified corrections to fold into the body of this plan
1. **No `ping` op exists.** Liveness = `api.v1.heartbeat` (`{invocation_ids:[str]}` → `{success,touched}`); readiness = `api.runtime.ready{layer_stack_root}`. Replace every "ready/ping" mention (e.g. §5 Phase 2) with heartbeat+ready. (`builtin_operations.py:113-189`.)
2. **Protocol-version field is nested in `args`, and the daemon never validates it.** `_eos_daemon_protocol_version=1` lives inside `args` (set only on the `api/transport.py` path), an inert versioning hook — not a top-level envelope sibling. A top-level placement would diverge every envelope at the AV-1 bar. (`daemon_client.py`, `daemon/rpc/server.py`.)
3. **Wire-framing/auth/limits live in `daemon/rpc/server.py`, not `dispatcher.py`.** `MAX_REQUEST_BYTES = 16 MiB`, `REQUEST_READ_TIMEOUT_S = 30.0`; `dispatcher.py` is op-routing + `_error_envelope` only. `api.layer_metrics` route is `dispatcher.py:432`.
4. **`manifest_root_hash` uses Python `json.dumps(ensure_ascii=True)`** — non-ASCII escapes to `\uXXXX` (surrogate pairs for non-BMP). serde_json emits raw UTF-8 and **silently diverges** on non-ASCII. Reproduced via a hand-built escaper (fixtures `manifest_unicode_bmp`/`_nonbmp`). `layer_digest` is the opposite — raw UTF-8 path bytes; `write` hashes content only (not `source_path`), `symlink` hashes `source_path`. (AV-1c sharpened.)
5. **`eos-runner` is NOT a leaf** — it depends on `eos-overlay` (imports `overlay.kernel_mount`); corrects §1's "runner … no internal deps" reading. Only `eos-ns-holder` is a near-leaf. The "single-threaded, no-tokio" property is about the threading model (kernel requirement), not dependency-freedom.
6. **Audit ring buffer is `daemon/audit_buffer.py`** (`SCHEMA_VERSION="sandbox.daemon.audit.pull.v1"`, `max_events=50000`, `max_bytes=8388608`, lanes critical/normal/sample, pressure 0.8), distinct from the host-side `audit/` translation bus. The `audit_schema` severing (→ `eos-protocol`) is **partial**: the pure dataclasses move; `safe_emit`/`safe_record_phase` stay in `eos-daemon`.
7. **Net constants are split**: `network.py` (`eos-shared0`, `10.244.0.0/24`, gateway `10.244.0.1`, IMDS `169.254.169.254`, `VETH_PREFIX=eos-iws-`) vs `_control_plane/types.py` (`FALLBACK_DNS=1.1.1.1`, `HANDLE_PREFIX=eos-iws-`) vs `ns_holder.py` (`accept_ra=0`, route flush) — corrects §3.5/SF-6 which implied all live in `types.py`. New constant found: `AUTO_SQUASH_MAX_DEPTH=100` (`occ/service.py:34`). The reentrant-RLock deadlock trap is in `layer_stack/storage_lock.py` (not `occ/service.py`).
8. **Two internal path conflicts — resolved**: `runtime_artifact/` → `backend/src/sandbox/host/runtime_artifact/` (§11, not §1's `sandbox/runtime_artifact/`); vendored fixtures → `backend/src/sandbox/_contract_fixtures/` (§11, not §2's `backend/tests/.../sandbox_protocol_fixtures/`). The plan's `install.py:421` LSP installer reference is wrong — the real installer is `plugins/catalog/lsp/setup.sh` (Node 22.13.1 + Pyright via npm).
