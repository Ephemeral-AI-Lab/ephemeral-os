# Plan: `eos-e2e-test` — protocol-only sandbox correctness/pressure suite

Status: DRAFT (planning only — no implementation in this document).
Owner workspace: `sandbox/` (Rust). Provider: Docker only. Image: default dask.
Operation interface: **`eos-protocol` ops against a live `eosd` only**. No other sandbox
operation is permitted in the path-under-test or as a verification oracle.

This plan is derived from a read-only recon of `backend/scripts`, `backend/tests`,
`sandbox/crates/eos-protocol`, `sandbox/crates/eos-daemon` (dispatcher/audit), the
internal mechanic crates (`eos-layerstack`, `eos-occ`, `eos-overlay`, `eos-runner`,
`eos-isolated`), the in-flight audit work in `eos-obs-collector`, and the Docker bench
harness in `backend/scripts/bench_rust_*.py`. Evidence file:line anchors are inline.

---

## 1. Objective & scope

Build a Rust integration crate that pressure-tests **sandbox correctness** by speaking
the `eosd` wire protocol and nothing else, covering these surfaces:

setup · tool calls · command sessions · layerstack squash · lease/unlease ·
`commit_to_workspace` · OCC merge · overlay mount.

In scope
- A reusable Docker container fixture (fast spin-up of the default dask image + `eosd`).
- A thin `eos-protocol` client (envelope encode/decode over a socket) — the *only* way
  the suite issues operations.
- Correctness tests per surface, asserting on **protocol responses + CAS hashes + audit
  events** only.
- A pressure tier (concurrency, deep layer stacks, conflict races) converted from the
  load-bearing agent-wired Python tests.
- **Configurable multi-node execution** (§5.1): selectable Docker node modes
  (shared / pool / per-file / per-test) with a configurable parallel-node count, so the suite
  scales from a laptop (1 node) to CI (N parallel daemons).
- Minimal **audit emission additions** in `eos-daemon` where a surface has no
  protocol-visible signal today ("add audit if need better observability").

Out of scope
- Anything that needs an agent loop / LLM provider / engine / workflow (those tests stay
  in `backend/tests` or move to agent-core test surfaces, not here).
- Daytona or any non-Docker provider (Rust sandbox config is Docker-only).
- Plugin/PPC/LSP correctness as a *primary* target (the dispatcher exposes
  `api.plugin.ensure/status`; treat plugin coverage as a later, optional tier).

---

## 2. Key decisions (surfaced, with recommendation)

### D1 — Crate placement & transport  ← **the structural fork**

The constraints pull in one direction:
- "module **for sandbox**" → belongs in the `sandbox/` workspace.
- "use `eos-protocol` as the operation interface; **no other sandbox operation** is
  allowed" → issue raw `eos-protocol` envelopes, not a higher-level host client.
- Architecture rule: `sandbox/` **cannot** depend on `agent-core` (back-edge forbidden),
  so it cannot reuse `eos-sandbox-host`'s `DaemonClient`/`DockerProviderAdapter`.

Three options:

| Opt | Where | Op transport | Container lifecycle | Cost | Fit to stated intent |
|-----|-------|--------------|---------------------|------|----------------------|
| **C (recommended)** | `sandbox/crates/eos-e2e-test` | thin tokio socket client over `eos_protocol::{encode,decode,Request}` | `docker` **CLI** via `std::process::Command` (the E4 recipe is already pure docker CLI) | ~150–250 LOC harness | **Best** — sandbox-owned, raw protocol, no back-edge, no bollard |
| B | `agent-core/crates/eos-e2e-test` | reuse `eos-sandbox-host::DaemonClient` | reuse `DockerProviderAdapter` (bollard) | Lowest — `write_stdin_live.rs` already proves it | Weaker — inserts the host client + recovery state machine between test and wire; not "sandbox" |
| A | `sandbox/crates/eos-e2e-test` | thin socket client | add `bollard` to the sandbox workspace | Medium — new heavy dep in sandbox | Duplicates `eos-sandbox-host`'s docker driver |

**Recommendation: Option C.** It is the only option that matches all three constraints:
sandbox-owned, raw `eos-protocol` as the operation interface, no architectural back-edge.
The container lifecycle (create/upload/spawn/teardown) is *infrastructure*, not a "sandbox
operation", so driving it through the `docker` CLI is allowed and mirrors the bench recipe
(`backend/scripts/bench_sandbox_e2e.py`, `preflight_docker_a2_caps.sh`). Option B is the
fallback if the team prefers to consolidate with the existing
`agent-core/crates/eos-sandbox-host/tests/write_stdin_live.rs` work and accepts the
agent-core placement.

Transport detail (Option C): connect host→container over **TCP** to the daemon's
`37657` port (mapped by `-p 127.0.0.1::37657`), auth via the `DAEMON_AUTH_FIELD`
top-level envelope key. Unix-socket-via-`docker exec` (mirroring
`eosd/src/main.rs:300-329` thin client) is the fallback if TCP listen is not enabled in
the test image. Confirm the eosd TCP-enable flag during Phase 0.

### D2 — "Move" means **port Python→Rust**, not a file move
The Python sandbox-only scripts/tests are reimplemented as Rust tests in the new crate.
The fate of the Python originals is a separate decision (D3).

### D3 — Fate of ported Python originals
Per project guidance (`backend/src` is legacy, deprecated post-migration), the default is
**delete-after-parity**: port to Rust, confirm green, then remove the Python original in a
follow-up commit. Alternative: keep Python as a parity oracle during migration. Surfaced;
recommend delete-after-parity to avoid double maintenance.

### D4 — Verification oracle discipline
The suite asserts **only** on: (a) `eos-protocol` response payloads, (b) CAS hashes
recomputed in-process (`manifest_root_hash`, `layer_digest`), and (c) `api.audit.pull`
events. It must **not** use `docker exec`/adapter `exec()` to peek at the container
filesystem as an oracle (that is "another sandbox operation"). `write_stdin_live.rs` peeks
via `adapter.exec()`; this suite deliberately does not. This discipline is what makes the
"add audit if need better observability" requirement load-bearing.

---

## 3. Two-tier coverage model (the crux)

The eight target surfaces split into two tiers against the registered dispatcher op set
(`sandbox/crates/eos-daemon/src/dispatcher.rs:105-146`):

**Tier 1 — directly invokable ops** (assert on response + CAS):
- setup → `api.runtime.ready`, `api.ensure_workspace_base`, `api.build_workspace_base`,
  `api.workspace_binding`
- tool calls → `api.v1.read_file` / `write_file` / `edit_file` / `glob` / `grep`
- command sessions → `api.v1.exec_command` / `write_stdin` / `command.cancel` /
  `command.collect_completed` / `command_session_count`
- `commit_to_workspace` → `api.commit_to_workspace`

**Tier 2 — internal mechanics with NO direct op** (only observable via audit):
- **layerstack squash** — triggered by accumulating writes past auto-squash depth
  (`eos-layerstack/src/stack.rs:399-467`, fired from `occ_writer.rs:189-233`)
- **lease / unlease** — acquired/released by `api.isolated_workspace.enter|exit` and by
  the transient overlay-snapshot path of read ops (`eos-layerstack/src/lease.rs:92-128`)
- **OCC merge** — per-path during `write_file`/`edit_file`/`exec_command`
  (`eos-occ/src/commit_queue.rs:35-110`)
- **overlay mount** — by the overlay pipeline / `isolated_workspace.enter`
  (`eos-runner/src/setns.rs:94-126`, `eos-overlay/src/kernel_mount.rs`)

Tier 2 is the reason audit is mandatory: under the "eos-protocol only" rule, a black-box
test cannot invoke or inspect these directly; it triggers them via a side-effect op and
asserts on the emitted audit event.

### Surface → op → invariant → observable → audit status

| Surface | Trigger op(s) | Key invariant to assert | Protocol-visible observable | Audit status |
|---|---|---|---|---|
| Setup (base) | `ensure/build_workspace_base`, `runtime.ready` | base layer L000000 created & idempotent; daemon ready | response timings (`api.workspace_base.total_s`), `layer_metrics` layer_count=1 | **MISSING** event → add `workspace_base.ensured/built` |
| Tool calls | `read_file`/`write_file`/`edit_file`/`glob`/`grep` | read = merged-view fast path; write/edit publish a versioned LayerChange; glob/grep iterate merged view | response (`content`, `changed_paths`, `applied_edits`), `manifest_root_hash` via audit | **COVERED** (`tool_call.completed`, `occ.publish`) |
| Command sessions | `exec_command`→`write_stdin`→`collect_completed`/`cancel` | `command_session_id` persists; cancel unblocks; collect drains without loss; `command_session_count` accurate | `ExecCommandResult` (status, exit_code, output), `command_session_count` | **COVERED** (`background_tool.started/input/cancelled/completed`) |
| Layerstack squash | 101+ sequential `write_file`/`edit_file` | depth ≤ AUTO_SQUASH_MAX_DEPTH(100) after publish; checkpoint id `B{ver:06}-`; head layers still readable | publish-response timings (`layer_stack.auto_squash.depth_before/after`) | **COVERED** (Critical lane: `layer_stack.squash_triggered/failed/completed`) |
| Lease / unlease | `isolated_workspace.enter`/`exit` (also read ops) | active lease pins manifest version; squash-while-leased does not GC pinned layers; refcount decrements on release | `layer_metrics` (lease hold count) | **COVERED** (`layer_stack.lease_acquired/released`) |
| `commit_to_workspace` | `api.commit_to_workspace` | manifest collapsed to workspace root; version incremented; base regenerated | response (`manifest_version`, project/replace/rebuild timings) | **MISSING** event → add `layer_stack.commit_completed` |
| OCC merge | concurrent `write_file`/`edit_file` on same path | atomic per-path Route; CAS base-hash match; conflict → `ConflictInfo`(reason, conflict_file) + per-path AbortedVersion/Overlap | response `conflict` + `changed_paths` | **COVERED** (`occ.publish` / `occ.conflict`) |
| Overlay mount | overlay ops (`exec_command`/`glob`/`grep`) + `isolated_workspace.enter` | lowers mounted newest-first read-only; upper/work writable; no cross-layer leakage | ephemeral: response (`workspace.mount_s`, `changed_path_kinds`) + ring (`lease_*`, `overlay_workspace.cleanup`); isolated: `enter`/`status`/`exit` responses (manifest pin, `inspection`, `evicted_upperdir_bytes`) | **COVERED today** (see §10). Optional: ring-level `mount_ms` |

Op names verified against `dispatcher.rs:105-146`. (Recon note: the `eos-protocol`
`models.rs` doc-comments suggest names like `api.v1.file.read`; those are **not** the
registered ops — the dispatcher strings above are authoritative.)

---

## 4. Audit observability additions (minimal, schema-aligned)

Three gaps must be closed in `sandbox/crates/eos-daemon/src/audit_events.rs`, each reusing
an **existing** `eos-protocol` audit section and the existing "emit-on-timing-key" idiom
(emit when a known timing key is present in the op response):

1. Overlay mount — see the dedicated **§10 (full overlay-mount coverage)**. Verified against the
   live handlers: overlay mount is **fully coverable today with no new instrumentation**. The
   only optional add is populating `OverlayWorkspaceSection.mount_ms` (field already exists,
   `audit.rs:98-145`) by threading the runner's `workspace.mount_s` into `overlay_workspace.cleanup`
   — a ring-level latency convenience, not a correctness requirement (the value is already in the
   op response). Full detail in §10.
2. `layer_stack.commit_completed` — reuse `LayerStackSection` (`manifest_version`,
   `manifest_root_hash`, `layer_count`, `total_ms`). Emit when response carries
   `api.commit_to_workspace.total_s` (already set at `dispatcher.rs:428`). Closes the
   `commit_to_workspace` gap.
3. `workspace_base.ensured` / `workspace_base.built` — reuse `LayerStackSection`. Emit when
   response carries `api.workspace_base.total_s` (already set at `dispatcher.rs:407`).
   Closes the setup gap.

**This is an independent, parallel workstream — NOT on the suite's critical path.** Per the
§3 gap table, squash, lease/unlease, OCC merge, tool calls, and command sessions are already
COVERED by existing daemon-ring events, and setup/`commit_to_workspace` have **response-payload**
observables; overlay mount has `overlay_workspace.cleanup` as a fallback. So the suite can be
built and pass on day one without these additions; they upgrade observability for the three
weak surfaces and can land last or in parallel.

Constraints on this work:
- `sandbox/crates/eos-protocol/src/audit.rs` and `agent-core/crates/eos-obs-collector/`
  are **currently dirty** (active audit work by another agent; last commit "Add Rust audit
  observability gates"). This plan's recon read a snapshot. The implementer MUST re-read
  `audit.rs` + `audit_events.rs` **live** and coordinate with the audit owner before editing
  — do not trust the field list here as current.
- These are net-small emission additions, not schema changes (the section fields already
  exist), so they need only focused daemon tests + a golden audit-shape check.
- Align with the **in-flight** audit consumption work in
  `agent-core/crates/eos-obs-collector/src/{lib.rs,gates.rs}` (currently dirty:
  `ExpectedToolUse`, `tool_use_checked_count`, `RunnerGateInput`). The e2e suite asserts on
  the daemon ring directly via `api.audit.pull`; it does **not** route through the
  collector gates (that is agent-core). But the *same* daemon events feed both, so adding
  the missing emissions benefits the collector too. Do not duplicate the collector layer.
- `api.audit.reset_floor` is currently a **stub** that does not actually reset loss state
  (`audit_ops.rs:47-60`, gated by `EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true`). The suite
  must use the `pull(after_seq)` cursor model and `snapshot().next_seq` baselining instead
  of relying on floor reset.

---

## 5. Crate architecture & resulting file/folder structure (Option C)

```
sandbox/
├── Cargo.toml                          # MODIFIED: + "crates/eos-e2e-test" in members;
│                                       #           + eos-e2e-test path-dep entry
├── .cargo/config.toml                  # OPTIONAL NEW: [env] to pin EOS_E2E_PROFILE per checkout
│                                       #   (e2e.toml already holds the committed defaults)
└── crates/
    └── eos-e2e-test/                   # NEW crate (test-support lib + integration tests)
        ├── Cargo.toml                  # deps: eos-protocol (workspace=true),
        │                               #   tokio[net,process,macros,rt-multi-thread],
        │                               #   serde_json, anyhow (test-edge), sha2 (CAS recompute)
        │                               # [features] e2e = []   # gates docker tests off macOS
        ├── e2e.toml                    # CONFIG FILE (source of truth, §5.2): image,
        │                               #   concurrent sandboxes, mode, caps, timeouts, profiles
        ├── src/                        # harness LIBRARY (tests `use eos_e2e_test::…`)
        │   ├── lib.rs                  # thin re-export surface (<100 LOC)
        │   ├── config.rs               # loads e2e.toml + EOS_E2E_* env overrides + profile select
        │   ├── client.rs               # ProtocolClient: TCP connect, encode()/decode(),
        │   │                           #   auth-field injection, invocation_id, 30s timeout
        │   ├── container.rs            # DaemonContainer: docker CLI create/start/cp eosd/
        │   │                           #   spawn/resolve `docker port`/teardown + ready poll
        │   ├── pool.rs                  # NodePool: ≤N containers, semaphore checkout, pre-warm,
        │   │                           #   per-test fresh layer_stack_root; node-mode selection (§5.1)
        │   ├── audit.rs                # AuditTap: api.audit.pull cursor + snapshot baseline
        │   ├── cas.rs                  # recompute manifest_root_hash / layer_digest
        │   ├── fixtures.rs             # workspace seeding + load generators
        │   └── bin/e2e-reap.rs         # reaper: remove all `eos.e2e.pool`-labeled containers
        │                               #   (keep_container defaults true → kept pool needs reaping)
        └── tests/                      # one file per surface (cases in §6)
            ├── protocol_contract.rs    # envelope/error surface (foundational)
            ├── setup.rs                # Tier 1
            ├── tool_calls.rs           # Tier 1
            ├── command_sessions.rs     # Tier 1
            ├── commit_to_workspace.rs  # Tier 1 (new authoring)
            ├── squash.rs               # Tier 2
            ├── lease.rs                # Tier 2
            ├── occ_merge.rs            # Tier 2
            ├── overlay_ephemeral.rs    # Tier 2 — §10
            ├── overlay_isolated.rs     # Tier 2 — §10
            ├── pressure_concurrency.rs # Pressure
            ├── pressure_squash_deep.rs # Pressure
            ├── pressure_failure_recovery.rs # Pressure
            └── overlay_failure.rs      # Pressure — §10 (fault injection)
```

Existing files touched **only if** opting into the optional/§4 instrumentation (all in the
sandbox workspace — no agent-core back-edge; all currently dirty → re-read live first):

```
sandbox/crates/eos-daemon/src/audit_events.rs   # §4: workspace_base.* + layer_stack.commit_completed
                                                 # OPT-2: overlay_workspace.cleanup.mount_ms
sandbox/crates/eos-daemon/src/isolated.rs        # OPT-1: echo lease_id/layer_count on enter/status
sandbox/crates/eos-isolated/src/session.rs       # OPT-1/3: extra teardown fields (optional)
docs/plans/eos_e2e_test_sandbox_PLAN.md          # this plan
```

Under **Option B** (agent-core placement) the identical tree lands at
`agent-core/crates/eos-e2e-test/` and reuses `eos-sandbox-host::DaemonClient`, collapsing
`src/client.rs` + `src/container.rs`.

Summary: **1 new crate**, 8 harness modules (incl. `config.rs`), a committed `e2e.toml` config
file, 14 test files, a 2-line `sandbox/Cargo.toml` edit; optional instrumentation touches 3
existing daemon/isolated files.

Harness invariants
- `ProtocolClient` is the only egress to eosd. All ops go through `call(op, args)`.
- Containers are obtained from a `NodePool` (§5.1), never constructed ad hoc. A test acquires
  a node lease + a fresh `layer_stack_root` under `/eos/e2e/<id>/stack`; `/testbed` remains
  the canonical workload workspace path for isolated and non-isolated calls and is reset on
  each lease. Warm spin-up (dominated by `build_workspace_base`, 10–180s) is paid **once per
  node** at pool init, then amortized across that node's tests.
- Container recipe (from `backend/scripts/bench_sandbox_e2e.py:46`,
  `provider/docker/client.py:25-69`, `preflight_docker_a2_caps.sh:40-81`):
  - image: default dask (e.g. `sweevo-dask__dask-*`), `sleep infinity`, `--init`
  - caps: `--cap-add=SYS_ADMIN --cap-add=NET_ADMIN`
    `--security-opt seccomp=unconfined --security-opt apparmor=unconfined`
  - tmpfs: `--tmpfs /eos/e2e:rw,exec,size=2g,mode=1777` plus
    `--tmpfs /eos/mount:rw,exec,size=2g,mode=1777`; keep `/eos/daemon` on the
    container rootfs so Docker Engine `put_archive` can see it
  - eosd: Docker Engine `put_archive` host Linux binary → `/eos/daemon/eosd`
  - TCP: env `EOS_DOCKER_DAEMON_TCP=1` + auth token, `-p 127.0.0.1::37657`, resolve via
    `docker port`; **caps are mandatory** — without SYS_ADMIN/NET_ADMIN the overlay/ns/OCC
    tier fails at runtime.
- Linux-only: `setns_overlay_mount` is `cfg(target_os="linux")`; the suite runs against the
  Linux container, but `cargo test` host is macOS — tests must gate on a `docker`/`e2e`
  feature so the workspace still builds on dev hosts.

### 5.1 Execution topology — node modes & parallel size

A **node** = one Docker container running one `eosd`. Two independent axes of parallelism,
grounded in how the daemon holds state:

- **Within-node (cheap):** `layer_stack_root` is a **per-call arg** (`workspace_ops.rs:43,112,…`)
  and layer-stack state is **keyed by root** — lease registry is
  `OnceLock<Mutex<HashMap<String, SharedLeaseRegistry>>>` (`lease.rs:191`), storage lock is a
  per-root registry (`storage_lock.rs:10`). So **one daemon multiplexes many independent
  `layer_stack_root`s concurrently.** A test gets isolation by using a fresh root — no new
  container, no rebuild.
- **Cross-node (expensive, but sometimes required):** `/eos/e2e` + `/eos/mount` tmpfs scratch, ns-holder PIDs,
  cgroups, and the network bridge are **container-global, not per-root**. Overlay/isolated
  tests therefore can interfere across roots on the same daemon → they need separate nodes.

**Node modes** (`EOS_E2E_NODE_MODE`):
| Mode | Containers | Isolation per test | Use for | Cost |
|---|---|---|---|---|
| `shared` | 1 | fresh `layer_stack_root` | layerstack/OCC/tool/command/setup tiers | lowest |
| `pool` *(default)* | ≤ N (`EOS_E2E_SANDBOXES`) | node lease + fresh root | full parallel runs / CI | scales with N |
| `per-file` | 1 / test file | fresh root | moderate isolation | medium |
| `per-test` | 1 / test | whole container | overlay/isolated correctness, debugging, max isolation | highest |

**`NodePool` (`src/pool.rs`):** owns ≤ N containers behind a `tokio::Semaphore(N)`;
`acquire().await -> NodeLease` blocks when all nodes are busy; each lease yields a
`ProtocolClient` + a `fresh_root()` (UUID subdir → `ensure_workspace_base`); nodes pre-warm
their base once and are reused; recycle a node after K checkouts to bound scratch/orphan
accumulation.

**Lifecycle with `keep_container = true` (default):** containers are tagged with a stable label
(e.g. `eos.e2e.pool=<image-digest>`). On startup the pool **adopts** any healthy labeled
containers (revalidating `runtime.ready`) and only creates the shortfall up to `sandboxes`, so
the expensive base build is paid once and reused across `cargo test` runs. Nothing is torn down
on pool drop. Because of this, the module ships a **reaper** — `cargo run -p eos-e2e-test --bin
e2e-reap` (and a `--reap` test-harness flag) — that removes all `eos.e2e.pool`-labeled
containers. **CI sets `EOS_E2E_KEEP_CONTAINER=false`** (ephemeral runners → teardown on drop)
so build agents never leak containers. `keep=true` + fresh-root-per-test keeps reuse safe; if a
test needs a guaranteed-clean container it requests `mode=per-test` (which always tears down).

### Configuration file (`e2e.toml`) — §5.2

The module ships a **committed config file** `sandbox/crates/eos-e2e-test/e2e.toml`, loaded by
`src/config.rs` at harness startup (located via `CARGO_MANIFEST_DIR`, or `EOS_E2E_CONFIG=<path>`
to point elsewhere). It is the **source of truth** for the testing image, the number of
concurrent sandboxes, and the other run options. **Every field is overridable by an `EOS_E2E_*`
env var.** Precedence: **env > selected `[profile.*]` > `[default]` table > built-in**.

```toml
# sandbox/crates/eos-e2e-test/e2e.toml   — any field overridable by EOS_E2E_* env.

[docker]
image          = "sweevo-dask__dask-10042" # testing image (default dask)   [env EOS_E2E_IMAGE]
eosd           = "dist/eosd-linux-amd64"   # host binary to `put_archive`, or "build"
privileged     = false                     # else use cap_add below
cap_add        = ["SYS_ADMIN", "NET_ADMIN"]
tmpfs          = [
  "/eos/e2e:rw,exec,size=2g,mode=1777",
  "/eos/mount:rw,exec,size=2g,mode=1777",
]
tcp_port       = 37657

[concurrency]
sandboxes      = 4        # number of concurrent sandboxes (node-pool cap)  [env EOS_E2E_SANDBOXES]
mode           = "pool"   # shared | pool | per-file | per-test            [env EOS_E2E_NODE_MODE]
recycle_after  = 50       # recycle a node after K checkouts (bound scratch/orphan growth)

[timeouts]                # seconds
ready          = 30
request        = 30
base_build     = 180

[workspace]
reset_base     = false    # true = full base rebuild per node (slow); false = fresh-root isolation

[run]
keep_container = true     # DEFAULT: keep warm sandboxes alive for reuse    [env EOS_E2E_KEEP_CONTAINER]
                          #   across runs (amortizes 10–180s base build). CI sets false.
audit_pull_limit = 1000

# Profiles override the tables above for an environment; select with EOS_E2E_PROFILE.
[profile.laptop]
concurrency = { sandboxes = 1, mode = "shared" }
[profile.ci]
concurrency = { sandboxes = 8, mode = "pool" }
```

| Setting | File key | Env override | Default |
|---|---|---|---|
| testing image | `docker.image` | `EOS_E2E_IMAGE` | dask default |
| **concurrent sandboxes** | `concurrency.sandboxes` | `EOS_E2E_SANDBOXES` | `min(num_cpus/4, 4)` |
| node mode | `concurrency.mode` | `EOS_E2E_NODE_MODE` | `pool` |
| profile select | — | `EOS_E2E_PROFILE` | none |
| keep container | `run.keep_container` | `EOS_E2E_KEEP_CONTAINER` | **true** (CI overrides to false) |
| alt config path | — | `EOS_E2E_CONFIG` | crate `e2e.toml` |

Relationship to the existing config: `backend/tests/live_e2e_test/_tools/tiers.toml` is
**pytest-oriented** (`pytest_args`, `kind="pytest"`) and stays the *tier list*; `e2e.toml` is
this module's *topology/environment* config. They compose — add a `kind="cargo"` tier to
`tiers.toml` that exports `EOS_E2E_PROFILE`/`EOS_E2E_SANDBOXES` per tier when it invokes
`cargo test`, so the progressive runner drives sandbox count per tier. The optional
`sandbox/.cargo/config.toml` `[env]` block is only a convenience to pin `EOS_E2E_PROFILE` for a
checkout; `e2e.toml` already carries the committed default.

**`cargo test` interaction:** the **pool semaphore is the real concurrency bound**, not the
test-thread count — test fns block on `acquire()` until a node frees, so `RUST_TEST_THREADS`
can exceed N safely. The overlay/isolated tiers should run under `per-test` (or pin
`RUST_TEST_THREADS` ≤ N) because their interference is container-global.

**Tier → recommended mode:** setup/tool_calls/command_sessions/commit/squash/lease/occ →
`shared` or `pool`; overlay_ephemeral/overlay_isolated → `per-test`; pressure tiers → `pool`
with explicit N (and a single-node, many-client shape for OCC-conflict / concurrency tests —
those acquire **one** node exclusively and spawn their own clients; never split one logical
concurrency test across nodes).

---

## 6. Test catalog (per-module cases + coverage)

~70 cases across 14 files. Day-one = passes with no daemon changes (response/CAS/already-COVERED
events); §4 = gains an extra audit assertion after the §4 additions land. Overlay modules:
case-level detail is in **§10** (single source of truth) to avoid drift.

### Foundational — `protocol_contract.rs` (envelope/error, from `envelope.rs`)
| Test | Input | Covers | Oracle |
|---|---|---|---|
| `unknown_op_rejected` | bogus `op` | dispatch gate | `ErrorKind::UnknownOp` |
| `bad_json_rejected` | malformed frame | envelope decode | `ErrorKind::BadJson` |
| `oversized_request_rejected` | >16 MiB args | `MAX_REQUEST_BYTES` | `ErrorKind::RequestTooLarge` |
| `unauthorized_tcp_rejected` | wrong/missing auth field | TCP auth | `ErrorKind::Unauthorized` |
| `forbidden_in_isolated` | mutating op while isolated active | isolation gate | `ErrorKind::ForbiddenInIsolatedWorkspace` |

### Tier 1 — `setup.rs` (Surface: setup)
| Test | Ops | Covers | Oracle |
|---|---|---|---|
| `runtime_ready_handshake` | `runtime.ready` | liveness probes | response `ready` |
| `ensure_base_creates_L000000` | `ensure_workspace_base`,`layer_metrics` | base creation | `layer_count==1` |
| `ensure_base_idempotent` | `ensure_workspace_base`×2 | no double-create | `layer_count==1` (§4: `workspace_base.ensured`) |
| `build_base_reset_rebuilds` | `build_workspace_base` reset | rebuild + version bump | response `manifest_version`/timings |
| `workspace_binding_roundtrip` | `workspace_binding` | binding set/read | response |
| `heartbeat_inflight_idle_zero` | `heartbeat`,`inflight_count` | idle liveness | `inflight_count==0` |

### Tier 1 — `tool_calls.rs` (Surface: tool calls)
| Test | Ops | Covers | Oracle |
|---|---|---|---|
| `write_read_roundtrip` | `write_file`→`read_file` | merged-view read | `ReadFileResult.content/exists/encoding` |
| `write_publishes_changed_paths` | `write_file` | OCC publish on write | `changed_paths/status/mutation_source` + `occ.publish` |
| `edit_search_replace_applied` | `edit_file` | anchor edit | `applied_edits` |
| `edit_replace_all` | `edit_file` replace_all | multi-occurrence | `applied_edits` |
| `edit_anchor_not_found` | `edit_file` | error catalog | `SearchReplaceError::NotFound` |
| `edit_count_mismatch` | `edit_file` | error catalog | `CountMismatch` |
| `read_nonexistent` | `read_file` | missing-file path | `exists=false` |
| `glob_matches` | `glob` | pattern match | `GlobResult.filenames/num_files` |
| `glob_limit_truncation` | `glob` | `DEFAULT_GLOB_LIMIT=100` | `truncated=true` |
| `grep_content_mode` | `grep` | line matches | `GrepResult.content/num_matches` |
| `grep_files_with_matches` | `grep` | file-list mode | `filenames` |
| `grep_count_mode` | `grep` | count mode | `num_matches` |
| `read_max_bytes_guard` | `read_file` | `MAX_READ_BYTES=16MiB` | response guard |
| `write_max_file_bytes_guard` | `write_file` | `MAX_FILE_BYTES=2MiB` | response/error |

*CAS hash is not the oracle here — `occ.publish` lacks `manifest_root_hash`; assert via
`changed_paths`/`applied_edits`. (Confirm per-write hash observability in Phase 1.)*

### Tier 1 — `command_sessions.rs` (Surface: command sessions)
| Test | Ops | Covers | Oracle |
|---|---|---|---|
| `exec_simple` | `exec_command` | one-shot exec | `exit_code/output` |
| `exec_returns_session_id` | `exec_command` | session creation | `command_session_id` + `background_tool.started` |
| `write_stdin_echo` | `exec_command`→`write_stdin` | stdin streaming | output + `background_tool.input` |
| `collect_completed_drains` | `command.collect_completed` | drain without loss | output + `background_tool.completed` |
| `cancel_unblocks` | `command.cancel` | cancel blocked session | `background_tool.cancelled` |
| `session_count_accuracy` | `command_session_count` | count up/down | `command_session_count` |
| `exec_timeout` | `exec_command` timeout | timeout path | `status=timeout` |
| `output_token_cap` | `exec_command` max_output_tokens | truncation | output cap |
| `cancel_by_invocation_id` | `api.v1.cancel` | in-flight cancel | `inflight_count` |

### Tier 1 — `commit_to_workspace.rs` (Surface: commit_to_workspace — new authoring)
| Test | Ops | Covers | Oracle |
|---|---|---|---|
| `commit_collapses_layers` | seed→`commit_to_workspace` | manifest collapse | `manifest_version` + timings |
| `commit_materializes_merged_view` | `commit`→`read_file` | merged content + base regen | read response; `layer_metrics` |
| `commit_version_monotonic` | repeated commit | monotonicity | `manifest_version` |
| `commit_emits_audit` (§4) | `commit_to_workspace` | observability | `layer_stack.commit_completed` |

### Tier 2 — `squash.rs` (Surface: layerstack squash — COVERED, Critical lane)
| Test | Ops | Covers | Oracle |
|---|---|---|---|
| `auto_squash_triggers_past_depth` | 101+ `write_file` | trigger at depth 100 | `auto_squash.depth_before/after` + `squash_triggered`/`squash_completed` |
| `checkpoint_layer_id_prefix` | post-squash | checkpoint naming | layer id `B{ver:06}-` |
| `head_readable_after_squash` | `read_file` | head survives | latest content |
| `squash_cas_byte_identity` | recompute in-proc | **CAS oracle** | `manifest_root_hash`==`squash_completed.manifest_root_hash` |
| `squash_not_raced_single_client` | — | race-free isolation | `squash_failed` absent |

### Tier 2 — `lease.rs` (Surface: lease/unlease — COVERED)
| Test | Ops | Covers | Oracle |
|---|---|---|---|
| `enter_acquires_lease` | `isolated_workspace.enter` | acquire | `lease_acquired` + active count>0 |
| `exit_releases_lease` | `isolated_workspace.exit` | release | `lease_released` (hold_ms) |
| `lease_pins_layers_vs_squash` | enter→101 writes→squash→exit | **GC-safety** (`leased ⊇ lease_head`) | `layer_metrics` retention |
| `lease_hold_time_ordering` | enter…exit | timing | `lease_hold_ms ≥ elapsed` |
| `read_op_transient_lease` | `glob`/`grep` | transient snapshot lease | `lease_released` via overlay path |

### Tier 2 — `occ_merge.rs` (Surface: OCC merge — COVERED)
| Test | Ops | Covers | Oracle |
|---|---|---|---|
| `concurrent_conflicting_writes` | 2× `write_file` same path | version conflict | one `occ.publish`, one `occ.conflict` + `ConflictInfo` |
| `concurrent_disjoint_writes` | 2× `write_file` diff paths | independent commit | both `changed_paths`/`occ.publish` |
| `edit_overlap_conflict` | overlapping `edit_file` | overlap conflict | `conflict_kind=aborted_overlap` |
| `retry_budget_3x` | CAS base mismatch | retry then surface | per-path result (single-client isolation) |
| `publish_accounting` | `write_file` | publish metrics | `occ.publish` count + prepare/apply/commit ms |
| `route_fileresult_catalog` | mixed writes | per-path Route → status | `Committed/Dropped/Rejected/AbortedVersion/AbortedOverlap` |

### Tier 2 — `overlay_ephemeral.rs` + `overlay_isolated.rs` (Surface: overlay mount)
Full case list and invariant→oracle mapping in **§10.3** (both pass today — no instrumentation
dependency). Ephemeral asserts on response (`workspace.mount_s`, `changed_path_kinds`) + ring
(`lease_*`, `overlay_workspace.cleanup`); isolated asserts on `enter`/`status`/`exit` responses
(manifest pin, `inspection`, `evicted_upperdir_bytes`).

### Pressure tier (converted from agent-wired Python; load cranked)
| File | Cases | From |
|---|---|---|
| `pressure_concurrency.rs` | `n_concurrent_mixed_ops`, `write_storm_squash_under_load` | `test_concurrent_agents.py`, `test_codegen_race.py` |
| `pressure_squash_deep.rs` | `deep_stack_repeated_squash`, `squash_storage_no_orphan` | `test_auto_squash_edge_cases.py` (strip `ToolExecutionContextService`) |
| `pressure_failure_recovery.rs` | `daemon_respawn_midflight`, `cancel_storm`, `iws_same_port_discard` | `test_failure_recovery.py`, `test_iws_same_port_discard_live.py` |
| `overlay_failure.rs` | `mount_failure_no_partial_result`, `cleanup_failure_kind_surfaced` | new (fault injection + OPT-3) |

### Coverage matrix (surface → modules → day-one?)
| Surface | Module(s) | Day-one |
|---|---|---|
| Setup | `setup.rs`, `protocol_contract.rs` | ✅ (full audit after §4) |
| Tool calls | `tool_calls.rs` | ✅ |
| Command sessions | `command_sessions.rs` | ✅ |
| Layerstack squash | `squash.rs`, `pressure_squash_deep.rs` | ✅ |
| Lease / unlease | `lease.rs` | ✅ |
| commit_to_workspace | `commit_to_workspace.rs` | ✅ (audit after §4) |
| OCC merge | `occ_merge.rs`, `pressure_concurrency.rs` | ✅ |
| Overlay mount | `overlay_ephemeral.rs`, `overlay_isolated.rs`, `overlay_failure.rs` | ✅ (failure tail needs OPT-3 + fault injection) |

---

## 7. Source migration map

PORT (Python sandbox-only → Rust e2e). Strongest correctness sources:
- `backend/tests/live_e2e_test/sandbox/.../test_workspace_base_shell_lease_squash.py`
  → `lease.rs` + `squash.rs` (lease survival across mutation bursts; natural squash).
- `.../test_phase00_smoke.py` → `setup.rs` (socket, capture pipeline, OCC smoke).
- `.../test_phase05_public_file_ops_correctness.py` → `tool_calls.rs` + `overlay_ephemeral.rs`.
- `.../test_edit_replace_all_multi_edit_scenarios.py` → `tool_calls.rs` (edit/OCC).
- `.../test_phase09_complex_e2e.py`, `test_phase06_large_capture_scaling.py` → pressure.
- Reference harness (do not port wholesale; mine for recipe + load gens):
  `bench_sandbox_e2e.py`, `bench_rust_daemon_phase3.py`,
  `bench_rust_daemon_phase3t_command_session.py`.

CONVERT (agent-wired sandbox → protocol-only pressure). These are the "load-bearing tests
wired with agent" to strip down:
- `.../test_auto_squash_edge_cases.py` (imports `ToolExecutionContextService` +
  `call_plugin`) → `pressure_squash_deep.rs` + `squash.rs`. Highest value: only test that
  forces the post-publish auto-squash path.
- `unit_test/test_tools/test_sandbox_toolkit/{test_exec_command,test_edit_file,test_write_file,test_multi_edit}.py`
  (inject mock `sandbox_api` via `ToolExecutionContextService`) → fold into
  `command_sessions.rs` / `tool_calls.rs` as real daemon ops.
- `unit_test/test_sandbox/test_api/{test_command,test_edit,test_read,test_write,test_grep_glob,test_audit_emission}.py`
  (use `recording_transport_factory`) → become real `eos-protocol` round-trips; also the
  contract source for envelope shapes.

LEAVE (true agent-workflow — do not move here):
- `test_sandbox_toolkit/test_write_stdin.py` (imports
  `engine.background.task_supervisor.BackgroundTaskSupervisor`).
- `backend/scripts/smoke_two_user_message.py`, `diagnose_p2p_failures.py`.
- `bench_rust_daemon_isolated_inspection.py` only *touches* `task_supervisor` as harness
  scaffolding — port the protocol assertions, drop the supervisor glue.

TOOLING (not tests — leave in `backend/scripts`): `build_upload_eosd_docker.py`,
`codemod_sandbox_imports.py`, `vulture_whitelist.py`, `analyze_complex_build_perf.py`,
`perf/`, `perf_experiments/`.

---

## 8. Phased execution plan (each phase has a verification gate)

- **Phase 0 — Decide D1; scaffold.** Confirm eosd TCP-enable flag + auth in the test image.
  Create `sandbox/crates/eos-e2e-test` with `client.rs` + `container.rs`.
  *Verify:* one smoke test green — spawn dask container, `runtime.ready`, `exec_command true` returns exit 0.
- **Phase 1 — Harness hardening.** `audit.rs` tap (cursor pull + snapshot baseline),
  `cas.rs` recompute, `fixtures.rs` load gens, container reuse + teardown. Confirm per-write
  `manifest_root_hash` observability here (see §6). Add `config.rs` + the committed `e2e.toml`
  (§5.2: image, concurrent sandboxes, mode, options, profiles; `EOS_E2E_*` env overrides), and
  build `NodePool` (§5.1) over it with the four node modes + per-test fresh-root isolation.
  *Verify:* config loads from `e2e.toml`, an `EOS_E2E_SANDBOXES`/`EOS_E2E_PROFILE` env override
  wins, and the same 3 trivial tests pass under `mode=shared` (1 node, multi-root) AND
  `mode=pool` with `sandboxes=2` (concurrent); no leaked containers; pool semaphore bounds load.
- **Phase 2 — Tier 1 surfaces.** `setup.rs`, `tool_calls.rs`, `command_sessions.rs`,
  `commit_to_workspace.rs` — asserted on **response payloads + already-COVERED events**.
  *Verify:* each surface's invariants assert green (no new daemon events required).
- **Phase 3 — Tier 2 mechanics.** `squash.rs`, `lease.rs`, `occ_merge.rs`,
  `overlay_ephemeral.rs`, `overlay_isolated.rs` via side-effect ops + existing COVERED events
  (ephemeral: `lease_*`/`overlay_workspace.cleanup`; isolated: `enter`/`status`/`exit`
  responses — see §10). *Verify:* squash depth drop, lease-pin, conflict surfacing, overlay
  mount (both paths) all asserted.
- **Phase 4 — Pressure/conversion tier.** Convert agent-wired tests; crank concurrency,
  deep stacks, conflict races. *Verify:* stable under repeated runs; no audit loss masking failures (account for ring pressure).
- **Phase 5 — Retire Python originals (D3) + docs.** Delete ported originals after parity;
  refresh `docs/architecture/sandbox/*` evidence paths. *Verify:* no dangling imports (`codemod`/grep), architecture pages cite Rust evidence.
- **Workstream A (parallel / off critical path) — Audit additions (§4).** Add the 3
  emission points in `eos-daemon` after re-reading the live (dirty) `audit.rs`; coordinate
  with the audit owner. Then upgrade `setup.rs`/`commit_to_workspace.rs`/`overlay_ephemeral.rs`
  to assert the new events (OPT-2 `mount_ms`, §4 `workspace_base.*`/`commit_completed`). *Verify:* `api.audit.pull` shows new events; `cargo test -p eos-daemon` + `cargo clippy -p eos-daemon --all-targets -- -D warnings` green; golden audit-shape check.

Verification ladder per Rust phase: `cargo check -p eos-e2e-test --all-targets` →
`cargo test -p eos-e2e-test --features e2e <targeted>` → `cargo clippy ... -D warnings`.
Broaden to `eos-daemon`/workspace only when Phase 2 crosses crates.

---

## 9. Risks & open questions

**Two confirmations needed before Phase 0:** (D1) crate placement, and (Q0) the conversion
target set.

0. **Q0 — `backend/tests/mocked/sandbox` does not exist.** The conversion instruction was
   anchored on that path. I mapped "load-bearing tests wired with agent" to
   `live_e2e_test/.../test_auto_squash_edge_cases.py`, the `test_sandbox_toolkit/*` toolkit
   tests, and the `test_api/*` recording-transport tests (§7 CONVERT). **Confirm these are
   the intended targets** — a different set reshapes the pressure tier.
1. **D1 placement** — recommend Option C (sandbox-owned, raw protocol). This is *my
   interpretation* of "eos-protocol as the operation interface" (strict reading = raw
   envelope client); it is the more expensive path because **no Rust client exists in the
   sandbox workspace today** (only `eos-sandbox-host` in agent-core). If the team prefers to
   build on `eos-sandbox-host/tests/write_stdin_live.rs`, switch to B (agent-core) —
   reshapes §5. Decide before Phase 0.
2. **TCP listen confirmation** — E4's exact Rust TCP wire/auth header was inferred from the
   Python adapter; confirm `eosd`'s TCP-enable flag in Phase 0 or fall back to
   Unix-socket-via-`docker exec`.
3. **No lease/unlease direct op** — driven only via `isolated_workspace.enter/exit` and the
   transient read-op snapshot. The "lease/unlease" surface is therefore tested *through*
   isolated mode; confirm that satisfies the intent (vs. wanting a standalone lease op).
4. **Isolated-workspace events are JSONL, not ring** (`eos-isolated/src/session.rs:543`) —
   `sandbox_isolated_workspace_*` cannot be pulled via `api.audit.pull`. Lease/overlay
   assertions must use the **daemon-ring** `layer_stack.*` / `overlay_workspace.*` events,
   which are pullable.
5. **`reset_floor` stub** — cannot clear loss mid-run; rely on cursor + `next_seq`. Under
   heavy pressure (Sample lane evicted first) keep ops bounded or raise ring caps for e2e.
6. **macOS dev host** — overlay/ns is Linux-only; gate the live tests behind a feature so
   `sandbox/` still builds on dev machines; CI must run on Linux with the cap set.
7. **Python parity scope** — `commit_to_workspace` currently has **no** dedicated Python
   test; that surface is **new test authoring**, not a port.
8. **Multi-node resource scaling (§5.1)** — each node is a privileged container
   (SYS_ADMIN/NET_ADMIN + `/eos/e2e` and `/eos/mount` tmpfs + daemon RSS + base seed). N parallel nodes
   multiply host RAM/CPU/disk and the per-node warm-up cost; CI must size
   `EOS_E2E_SANDBOXES` to the runner. Long-lived pool nodes accumulate scratch/orphan state
   across many roots → recycle a node after K checkouts and rely on the orphan reaper.
   Because **`keep_container` defaults to `true`** (warm-pool reuse), kept containers persist
   across runs — local dev reaps with `e2e-reap`, and **CI must export
   `EOS_E2E_KEEP_CONTAINER=false`** so ephemeral agents tear down on drop and never leak.
9. **Container-global interference** — `/eos/e2e`/`/eos/mount` tmpfs, ns-holder PIDs, cgroups, and the bridge
   are not per-root, so overlay/isolated tests must run `per-test` (or one-node-at-a-time),
   not multiplexed on a `shared` node. Layerstack/OCC/tool/command tiers are safe to multiplex
   roots on one node.
10. **One-daemon-many-clients tests** — OCC-conflict and concurrency-pressure tests need a
    single daemon with multiple concurrent clients; they acquire **one** node exclusively and
    spawn their own clients. The pool's `EOS_E2E_SANDBOXES` parallelism is orthogonal and
    must not split a single logical concurrency test across nodes.

---

## 10. Full overlay-mount coverage (expanded)

Overlay mount has **no direct op** and splits into **two trigger paths with different
observability surfaces** (verified against the live handlers, not just emission code):

| Path | Trigger ops | Mount executed by | Correctness observable via protocol? |
|---|---|---|---|
| **Ephemeral** | `glob`, `grep`, `exec_command` (non-bg), plugin overlay ops | `run_ns_runner_child` FreshNs (`overlay_runner.rs:40-78`) | **YES — response + ring.** Runner `RunResult` merged into response: `timings["workspace.mount_s"]`, `changed_paths`, `changed_path_kinds`, `status`, `exit_code` (`workspace_ops.rs:449-467`, `tool_primitives.rs:35-118`). Ring: `layer_stack.lease_acquired/released`, `overlay_workspace.cleanup` (`audit_events.rs:155-207`, gate `uses_overlay_or_lease` `:336-347`). |
| **Isolated** | `isolated_workspace.enter`/`status`/`exit` | `run_ns_runner_mount_overlay_child` SetNs (`isolated.rs:248,796-825`) | **YES — op responses (NOT the ring).** `enter` returns `{manifest_version, manifest_root_hash, workspace_handle_id}` (`isolated.rs:387-392`); `status` returns `{open, manifest_version, manifest_root_hash, created_at, last_activity}` (`:463-471`); `exit` returns `{evicted_upperdir_bytes, phases_ms{kill_holder,teardown_veth,release_snapshot,cgroup_rmdir,rmtree_scratch}, inspection{lease_released, active_leases_after, holder_kill_error, cgroup_exists_after, handle_registered_after, …}}` (`session.rs:454-461,961-969`). The richer `sandbox_isolated_workspace_*` JSONL events (`session.rs:385-543`) are NOT pullable, but they are **redundant** — the same teardown facts are on the `exit` response. |

**Conclusion (corrected after reading the handlers):** *both* paths are fully observable for
**correctness** through `eos-protocol` today. The isolated path is observed via its own
op responses (`enter`/`status`/`exit`), not via `api.audit.pull`. **No new instrumentation is
required to fully cover overlay-mount correctness.** The only gaps are *observability
ergonomics / perf* (ring-level mount latency, one-call assertions), all optional. Note: gate
`uses_overlay_or_lease` (`audit_events.rs:336-347`) confirms `write_file`/`edit_file` take the
OCC fast path and do **not** mount an overlay — they are not part of this surface.

### 10.1 Consolidated invariant → trigger → oracle → status

Mount construction & ordering (`eos-overlay/kernel_mount.rs`, `eos-runner/{fresh_ns,setns,mount}.rs`):
| # | Invariant | Trigger | Protocol-only oracle | Status |
|---|---|---|---|---|
| M1 | lowerdirs **newest-first** (merge precedence) | write fileX in base→commit→write fileX again→commit→`glob`/`exec cat` in overlay | merged read returns newest content (behavioral) | COVERED |
| M2 | lowerdirs **read-only**, copy-up to upper | `exec` writes to an existing lower path | response `changed_paths` has it; `manifest_version` unchanged in `lease_acquired`==`lease_released` | COVERED |
| M3 | upper/work writable & validated (forbidden-char/symlink reject) | enter with crafted bad path | response `error.kind` (`InvalidMountInput`) | COVERED |
| M4 | layer_count == manifest depth | any overlay op | `lease_acquired.layer_count` == depth from `layer_metrics` | COVERED |
| M5 | mount atomic (fsopen→fsconfig→fsmount→move_mount) | mount failure injection | response `error` (no partial `tool_result`); `OverlayPipeline`/`SetupFailed` | COVERED (ephemeral) / **GAP (isolated → response only via SetupFailed, no detail)** |
| M6 | private propagation, fresh uid/gid map, no host leakage | `exec mount`/`exec cat /proc/self/uid_map` inside overlay | response stdout (behavioral) | COVERED (behavioral) |
| M7 | Linux-only; mount at real path | n/a (platform) | feature-gated; runs in Linux container | N/A |

Change capture (`eos-overlay/path_change.rs`):
| # | Invariant | Trigger | Oracle | Status |
|---|---|---|---|---|
| C1 | capture walks **only upperdir** | `exec` reads a lower (unchanged) + writes a new file | only the new file in `changed_paths` | COVERED |
| C2 | write/delete/symlink/opaque_dir mapped | `exec` create/rm/ln/opaque | `changed_path_kinds[path]` ∈ {write,delete,symlink,opaque_dir} | COVERED |
| C3 | whiteout duality (`.wh.` or xattr) → single Delete | `exec rm` a lower file | `changed_path_kinds[path]=="delete"` (one entry) | COVERED (style not distinguishable — internal, fine) |
| C4 | opaque dir → single OpaqueDir | `exec` opaque-mark a dir | `changed_path_kinds[path]=="opaque_dir"` | COVERED |
| C5 | copy-up content captured from upper | `exec` modify lower file | OCC-published content matches (plugin/write path) → `occ.publish` | COVERED |
| C6 | path normalization (reject abs/`..`/NUL) | `exec` write `../x` | rejected → `error` | COVERED |
| C7 | `changed_path_count` == len(changed_paths) | any write overlay op | `cleanup.changed_path_count` == `len(response.changed_paths)` | COVERED |

Lifecycle / lease / isolation:
| # | Invariant | Trigger | Oracle | Status |
|---|---|---|---|---|
| L1 | acquire→mount→tool→release symmetry, no leak | any overlay op | paired `lease_acquired`+`lease_released` same `operation_id` | COVERED |
| L2 | manifest immutable during lease hold | overlay op while no concurrent write | `manifest_version` equal in acquire/release | COVERED |
| L3 | cleanup unmounts + scratch removed | any overlay op | `cleanup.cleanup_ms>0` + `scratch_removed==true` | COVERED |
| L4 | mount latency measurable | any ephemeral overlay op | response `timings["workspace.mount_s"]>0` | COVERED (response); optional `mount_ms`→ring |
| I1 | isolated writes captured, **never OCC-published** | enter→`write_file`→exit | response `mutation_source=="isolated_workspace"`, `status=="committed"`; **no `occ.publish` in ring** | COVERED |
| I2 | isolated exit discards upperdir + scratch | enter→write→exit | exit `evicted_upperdir_bytes>0` + `inspection.handle_registered_after==false` + `phases_ms.rmtree_scratch` (`session.rs:454-461`) | COVERED |
| I3 | ns-holder reaped + lease released | exit | exit `inspection.holder_kill_error==null` + `inspection.lease_released==true` + `inspection.active_leases_after` (`session.rs:961-969`) | COVERED |
| I4 | cgroup removed on exit | exit | exit `inspection.cgroup_exists_after==false` (`session.rs:960`) | COVERED |
| I5 | isolated mount succeeded + pinned | enter→`status`→write→read | `enter` success + `{manifest_version,manifest_root_hash}`; `status.open==true`; behavioral write/read; depth via cross-ref `layer_metrics` at pinned version | COVERED (no explicit `mounted`/`lease_id` field — derived) |
| E1 | mount/cleanup failure surfaced | fault injection | ephemeral: response `error.kind`; isolated: `enter` returns `SetupFailed` error; `cleanup_failure_kind` never populated | PARTIAL — failure surfaced via error; `cleanup_failure_kind` is optional OPT-3 |

### 10.2 Instrumentation — all OPTIONAL (correctness needs none)

Verified against the live handlers, **full overlay-mount correctness coverage needs zero new
instrumentation**. The items below are observability ergonomics / perf only; ship the suite
without them and add later if desired:

- **OPT-1 (ergonomics): echo extra fields on `enter`/`status`.** `enter` could additionally
  return `lease_id`, `lowerdir_layer_count`, and `phases_ms` (incl. `mount_overlay`); `status`
  could add `mounted: bool` + `lease_id`. Today the test *derives* these (depth via
  `layer_metrics` at the pinned `manifest_version`; "mounted" via `enter` success + a working
  write/read). Echoing them turns a 2-call derivation into a 1-call assert. Not required.
- **OPT-2 (perf): populate `OverlayWorkspaceSection.mount_ms`.** Thread the runner's
  `workspace.mount_s` (already in merged response timings) into the `overlay_workspace.cleanup`
  emission (`audit_events.rs:187-207`); field already exists. Gives ring-level ephemeral mount
  latency. The value is already on the op response, so this is convenience only.
- **OPT-3 (failure tail): populate `cleanup_failure_kind` / ephemeral `upperdir_bytes`.** For
  cleanup-failure + write-volume coverage (E1, write-size). Only assertable with fault
  injection; schedule with the failure-recovery pressure tier.

Any of these touch the dirty `audit.rs`/`workspace_ops.rs` — re-read live and coordinate first.

### 10.3 Test modules for full overlay coverage

`tests/overlay_ephemeral.rs` (passes day-one; OPT-2 upgrades L4 to a ring assert):
- `mount_succeeds_timed` (L4) — `glob` → response `workspace.mount_s>0`, `status==ok`.
- `lower_readonly_copyup` (M2,C1) — write existing lower path via `exec` → in `changed_paths`; `manifest_version` stable across `lease_acquired`/`lease_released`.
- `newest_first_precedence` (M1) — two layers same path → merged read returns newest.
- `change_kinds_captured` (C2,C3,C4) — create/rm/ln/opaque → `changed_path_kinds` values.
- `changed_count_matches` (C7) — `cleanup.changed_path_count == len(changed_paths)`.
- `lease_pair_and_cleanup` (L1,L3) — paired lease events + `cleanup.scratch_removed==true`.
- `read_overlay_no_publish` (M2) — `glob`/`grep` → **no** `occ.publish` in ring.
- `mount_input_rejected` (M3,C6) — bad path/`..` → `error.kind`.

`tests/overlay_isolated.rs` (**passes today — no instrumentation dependency**):
- `enter_mounts_pinned` (I5, M5) — enter → success + `{manifest_version, manifest_root_hash, workspace_handle_id}`; cross-ref `layer_metrics` at that version → layer depth.
- `status_reports_open` (I5) — after enter, `status.open==true` with same manifest pin.
- `isolated_write_not_published` (I1) — enter→`write_file`→ `mutation_source==isolated_workspace`, `status==committed`, no `occ.publish` in ring.
- `exit_discards_upperdir` (I2) — enter→write→exit → `evicted_upperdir_bytes>0`, `inspection.handle_registered_after==false`.
- `exit_reaps_holder_releases_lease` (I3) — exit → `inspection.holder_kill_error==null`, `inspection.lease_released==true`, `inspection.active_leases_after==0`.
- `exit_removes_cgroup` (I4) — exit → `inspection.cgroup_exists_after==false`.
- `enter_rejects_active_bg_work` — enter while sandbox-bound bg work active → `error` (per CLAUDE.md lifecycle rule).
- `plugin_lsp_blocked_in_isolated` — plugin op while isolated active → `ForbiddenInIsolatedWorkspace`.

`tests/overlay_failure.rs` (depends on **OPT-3** + fault injection; goes in the pressure/recovery tier):
- `mount_failure_no_partial_result` (M5,E1) — induced mount failure → `error` (ephemeral) / `SetupFailed` (isolated `enter`), no `tool_result`.
- `cleanup_failure_kind_surfaced` (E1) — induced umount failure → `cleanup_failure_kind` set.

### 10.4 Coverage outcome
**Every** overlay-mount correctness invariant (M1–M7, C1–C7, L1–L4, I1–I5) is assertable
through `eos-protocol` **today** — ephemeral via op response + `api.audit.pull`
(`lease_*`/`overlay_workspace.cleanup`/`occ.publish`-absence), isolated via the
`enter`/`status`/`exit` op responses (manifest pin + `inspection` + `evicted_upperdir_bytes`).
No JSONL dependency, no container-filesystem peeking, **no new instrumentation required**.
OPT-1/OPT-2 are ergonomics/perf; OPT-3 + fault injection closes the failure-mode tail (E1).
