# Three-Server Architecture — overlay-server + occ-server + layer-stack-server

**Status:** draft (proposed)
**Author:** 2026-05-06
**Predecessors:**
- `.omc/plans/per-call-snapshot-layer-stack-migration/api-latency-reduction-plan.md` (Phases 1-4 landed)
- `.omc/plans/per-call-snapshot-layer-stack-migration/two-server-architecture-plan.md` (alternative; superseded by this plan)

**Related artifacts:**
- `backend/src/sandbox/runtime/daemon.py` (current single-process daemon — to be retired)
- `backend/src/sandbox/runtime/api_handlers.py` (mixed overlay + OCC + layer-stack handlers — to be split three ways)
- `backend/src/sandbox/runtime/prepare_pool.py` (forkserver ProcessPoolExecutor — to be retired)
- `backend/src/sandbox/control/daemon/command.py` (`fork` and `daemon` transport plumbing — `fork` to be retired)
- `backend/src/sandbox/layer_stack/stack_manager.py` (`LayerStackManager` — moves into its own server)
- `backend/src/sandbox/overlay/runner/runtime_invoker.py` (`execute_request` already takes serializable inputs — clean cut for overlay-server)

## Why this exists

After Phases 1-4 of the latency plan landed, daemon-mode plus prepare_pool already sits at the `process.exec` transport floor for read/write/edit (~700 ms p99 at c=16). The remaining latency is structural and lives above the sandbox runtime. Further architectural changes inside the sandbox cannot move that floor.

**This plan is not a performance project.** Its goal is **separation of concerns**: today's resident daemon mixes three distinct authorities (overlay capture, OCC commit semantics, layer-stack state) inside one Python process. That coupling makes the daemon hard to reason about, hard to supervise, and impossible to evolve component-by-component (e.g., reimplement overlay in Rust later without touching OCC).

The three concerns have genuinely different characteristics:

| Concern | State model | Concurrency model | Failure radius |
|---|---|---|---|
| Overlay capture | none — kernel mount, ephemeral | parallel-safe (separate namespaces) | per-call |
| OCC commit | derived only — prepares + serial commit | prepare parallel; commit serial | per-call |
| Layer-stack | persistent — manifest pointer, leases, layers on disk | reads parallel; publish serial | sandbox-wide |

Splitting these into three named services makes each one ownable, testable, and replaceable on its own.

## Goal

Replace the single `sandbox.runtime.daemon` process with **three long-lived in-sandbox servers**, each owning exactly one concern. Retire the `fork`-mode runtime transport and the `prepare_pool` ProcessPoolExecutor. Keep transport simple: one Python asyncio process per server, threads for I/O parallelism via `asyncio.to_thread`, no child-process pools.

* **layer-stack-server** — single authority for layer-stack state. Owns `LayerStackManager` instances keyed by `layer_stack_root`. Handles manifest reads, durable lease records, layer storage, GC, squash, and compare-and-publish. The only writer to `<storage_root>/manifest.json` and `<storage_root>/leases/`.
* **occ-server** — mutation coordinator plus OCC policy. Owns the public mutation workflow, `OccService`, `OccSerialMerger`, the snapshot gitignore oracle, atomic policy, path-bucketed commit gate, and the revalidate→compare-publish retry loop. Stateless wrt persistent layer-stack state — talks to layer-stack-server through narrow role protocols for every manifest/content/staging/publish operation that touches disk.
* **overlay-server** — stateless mount + exec + capture. Receives a neutral `OverlaySnapshotSpec` in its envelope, mounts/copies from that prepared snapshot, runs the command, and returns the capture. It must not import `sandbox.layer_stack`, `LayerStackManager`, leases, publish, OCC, or any layer-stack state owner.

The host still talks to the sandbox through one `process.exec` per public-API call. A thin client (same shape as today's `command.py:39-49`) connects to one socket. Internal hops between servers are AF_UNIX, sub-millisecond.

## What gets retired in this plan

* `EPHEMERALOS_RUNTIME_TRANSPORT=fork` — the legacy per-call `python -m sandbox.runtime.server` boot. After three-server lands, fork-mode is dead code; remove it from `command.py` and update tests. The latch-to-fork supervision (`command.py:240-288`) generalizes to "latch one of the three servers back up" but does not fall through to fork.
* `prepare_pool` — the ProcessPoolExecutor in `runtime/prepare_pool.py`. Without it, prepare runs on occ-server's main asyncio loop, GIL-bound. Per the post-Phase-4 A/B/C report, this regresses edit p99 at c=16 from ~759 ms to ~870 ms (the "daemon, no pool" measurement). For the target workload (10 concurrent agents, edit/write/read heavy) this stays under the 700 ms transport floor for write and just above for edit — acceptable in exchange for removing 200 LoC of mp/forkserver machinery and the per-worker service-cache state-drift surface.
* The single resident daemon (`runtime/daemon.py`) — kept for one release behind a feature flag for rollback, then deleted.

## Architecture

```
┌─────── host ────────────────────────────────────────────────────────┐
│  sandbox.api.tool.{shell,write_file,edit_file,read_file,…}          │
│      │                                                               │
│      ▼  one envelope, one process.exec                               │
│  control/daemon/command.py — _call_runtime_server                    │
│      │                                                               │
│      └── exec_fn(sandbox_id, "thin_client.py '<json>'") ─────────┐   │
└──────────────────────────────────────────────────────────────────┼───┘
                                                                   │
                              ┌────────────────────────────────────┼─── sandbox ──┐
                              │ thin_client.py                     │              │
                              │   • picks socket by op prefix:     │              │
                              │     lsm.*  → /tmp/eos/lsm.sock     │              │
                              │     api.*  → /tmp/eos/occ.sock     │              │
                              │   • pipes envelope, prints reply   │              │
                              │                                    ▼              │
                              │   ┌─ overlay-server ─┐  ┌─ occ-server ─┐  ┌─ layer-stack-server ─┐
                              │   │ /tmp/eos/        │  │ /tmp/eos/    │  │ /tmp/eos/lsm.sock     │
                              │   │ ovr.sock         │  │ occ.sock     │  │  (also public-facing  │
                              │   │  (private —      │  │  (public)    │  │   for read_file etc.) │
                              │   │   only occ talks │  │              │  │                       │
                              │   │   to it)         │  │              │  │                       │
                              │   │                  │  │              │  │ LayerStackManager dict│
                              │   │ asyncio loop     │  │ asyncio loop │  │  keyed by layer_stack │
                              │   │  + to_thread     │  │  + to_thread │  │  _root                │
                              │   │ stateless        │  │ OCC state    │  │ asyncio loop +        │
                              │   │                  │  │ (no manager) │  │ to_thread for reads   │
                              │   └──────────────────┘  └──────────────┘  └───────────────────────┘
                              │           ▲                   ▲                       ▲           │
                              │           │                   │                       │           │
                              │     overlay.run        api.{shell,write,edit}    lsm.{read_text,  │
                              │           │            commit_capture,              acquire_lease,│
                              │           │            …}                           release_lease,│
                              │           │                                         compare_publish,│
                              │           │                                         get_snapshot, │
                              │           │                                         pinned_layers,│
                              │           │                                         metrics,      │
                              │           │                                         compact}      │
                              │           │                   │                       ▲           │
                              │           └─── (occ → ovr) ───┴───── (occ → lsm) ─────┘           │
                              │                                                                   │
                              │   shell flow: thin_client → occ → lsm.acquire_lease+snapshot → ovr.run │
                              │               → occ apply (prepare + revalidate; lsm CAS publish)│
                              │               → lsm.release_lease  → reply                       │
                              └───────────────────────────────────────────────────────────────────┘
```

The thin client only talks to two sockets: `occ.sock` for OCC verbs, `lsm.sock` for read-only layer-stack verbs. `ovr.sock` is private — overlay-server is never directly addressed from the host.

## Server responsibility split — the core of this plan

Detailed responsibility table. Read top-to-bottom for one server's full surface; read across rows to see how a single concern routes through the system.

### layer-stack-server (`/tmp/eos/lsm.sock`)

**Single source of truth for layer-stack state on disk.** Every read or write of `<storage_root>/manifest.json`, every final layer publish, every durable lease mutation lives here and only here. It does not decide OCC policy; it only exposes snapshot reads, lease ownership, commit staging, and a compare-and-publish primitive.

| Surface | Op | Owns |
|---|---|---|
| Manifest/content read | `lsm.get_active_manifest`, `lsm.get_snapshot`, `lsm.read_bytes`, `lsm.read_text`, `lsm.materialize_snapshot` | active manifest pointer (re-reads `manifest.json` on demand); snapshot lookup by version; read-only merged-view access |
| Overlay snapshot prep | `lsm.prepare_overlay_snapshot`, returned by lease acquisition | LSM materializes or identifies a read-only snapshot root and returns a neutral `OverlaySnapshotSpec`; overlay never sees `storage_root` or `Manifest` |
| Lease lifecycle | `lsm.acquire_lease`, `lsm.heartbeat_lease`, `lsm.release_lease`, `lsm.list_leases` | durable lease records plus in-memory refcount index; request-scoped auto-release (see §Lease lifetime below) |
| Commit staging | `lsm.allocate_commit_staging`, `lsm.drop_commit_staging` | opaque staging directories under `<storage_root>/staging/occ-*`; OCC can write staged blobs only inside the allocated directory |
| Layer-stack writes | `lsm.compare_publish_layer` | atomic CAS publish of a new layer when the active manifest still matches OCC's expected version/digest |
| Compaction | `lsm.squash`, `lsm.collect_garbage` | depth squash, orphan layer/staging GC — single-writer with publish |
| Metrics | `lsm.pinned_layers`, `lsm.metrics` | pinned-layer enumeration, storage byte counts |

**State that lives here:**
- `dict[str, LayerStackManager]` keyed by `layer_stack_root` (today's `_SERVICE_CACHE` minus OCC/gitignore)
- Durable lease files under `<storage_root>/leases/` plus an in-memory lease refcount index rebuilt on startup
- Per-root publish/squash locks, staging-directory ownership records, and prepared overlay snapshot records

**State that does NOT live here:**
- No `OccService`, no merger, no gitignore oracle
- No OCC route, conflict, atomicity, or base-hash policy. `compare_publish_layer` checks only "is the active manifest still the expected identity?" and then performs the policy-blind layer publish.

**Concurrency:**
- One asyncio loop accepts connections
- Read/materialize ops (`read_bytes`, `read_text`, `get_snapshot`, `prepare_overlay_snapshot`, `metrics`, `pinned_layers`) dispatch via `asyncio.to_thread` — file I/O releases the GIL
- Write ops (`compare_publish_layer`, `squash`, `collect_garbage`, durable lease-file mutations) serialize on a per-`layer_stack_root` `asyncio.Lock`. Single-writer is by design and matches today's behavior; layer-stack publish has never been parallel-safe.

**Files (new):**
| File | Role |
|---|---|
| `sandbox/runtime/layer_stack_server.py` | asyncio AF_UNIX listener, dispatch |
| `sandbox/runtime/lsm_handlers.py` | one async function per `lsm.*` op |
| `sandbox/runtime/lsm_state.py` | the manager dict (lifted from today's `_SERVICE_CACHE`) |

### occ-server (`/tmp/eos/occ.sock`)

**Mutation coordinator plus OCC policy.** Hosts the public mutation API and keeps the mutation workflow in one place: read snapshot identity, build changes, prepare OCC decisions, revalidate, stage accepted blobs, compare-publish, and release leases. The OCC policy code itself stays below this coordinator in `OccService` and `OccCommitTransaction`; it receives narrow LSM role protocols, not a broad layer-stack client.

| Surface | Op | Owns |
|---|---|---|
| Write/edit pipeline | `api.write_file`, `api.edit_file` | public mutation coordinator: build `Change`, compute atomic policy, prepare via `OccService.prepare_changeset`, revalidate against the live active manifest, publish via `CommitPublisher.compare_publish_layer`, retry on CAS miss |
| Read pipeline | `api.read_file` | no OCC policy; route directly to lsm by default (see §Why reads route directly to LSM) |
| Shell composite | `api.shell` | public mutation coordinator: open request-scoped lease session → acquire lease + `OverlaySnapshotSpec` → ovr.overlay.run → capture-to-changeset → prepare/revalidate/CAS publish → release lease |
| Capture apply | `api.commit_capture` | overlay-capture → OCC changeset conversion (`overlay_capture_to_occ_changes`) through the OCC client/service boundary, then prepare/revalidate/CAS publish |
| Path-bucket gate | (internal) | the 16-bucket asyncio.Lock gate from Phase 4 (`api_handlers.py:_process_commit_gate`) |
| Atomicity policy | (internal) | "single-path overlay capture → atomic=False" (Phase 4 fix); audit point for shell regression |
| Gitignore | (internal) | snapshot gitignore oracle instance per `layer_stack_root`, keyed cache by snapshot version |

**State that lives here:**
- `SnapshotGitignoreOracle` per `layer_stack_root` (OCC-owned cache of gitignore evaluators by manifest version; uses `SnapshotReader` / `SnapshotMaterializer` only to read/materialize a snapshot)
- `OccService` per `layer_stack_root` (uses narrow snapshot/content protocols instead of a direct manager handle)
- `OccSerialMerger` worker thread per `layer_stack_root` (commit ordering/batching before CAS publish, intentional)
- `_PROCESS_COMMIT_LOCK_BUCKETS` (asyncio.Lock buckets for prepare-time gate)
- `LsmGateway` instances. This is the AF_UNIX transport implementation; OCC modules consume only the narrow role protocols it implements.

**State that does NOT live here:**
- No `LayerStackManager`. Anywhere occ-server needs layer-stack state, it depends on a narrow protocol (`SnapshotReader`, `CommitPublisher`, etc.) implemented by the gateway.
- No active-manifest pointer. occ-server gets a manifest *value* via `SnapshotReader.get_active_manifest` or the lease session and treats it as immutable for the duration of the call.

**Concurrency:**
- One asyncio loop accepts connections
- Prepare runs on the main loop (CPU-bound, GIL-bound). This is the regression vs `prepare_pool`; per §"What gets retired" we accept it.
- `OccSerialMerger` worker thread coalesces concurrent prepare results into ordered commit payloads — same merger that exists today.
- Connection-per-call: 10 concurrent agents = 10 concurrent connections, each running its own coroutine.

**Files (new/changed):**
| File | Role |
|---|---|
| `sandbox/runtime/occ_server.py` (new) | asyncio AF_UNIX listener |
| `sandbox/runtime/occ_handlers.py` (new) | extracted from today's `api_handlers.py` minus the lsm-direct paths |
| `sandbox/runtime/lsm_gateway.py` (new) | AF_UNIX transport implementation for the narrow LSM role protocols |
| `backend/src/sandbox/occ/service.py` (changed) | accept narrow snapshot/content Protocols instead of a `LayerStackManager` directly |
| `backend/src/sandbox/runtime/mutation_coordinator.py` (new) | host-facing mutation workflow: write/edit/shell orchestration around `OccService` |
| `api_handlers.py` (deleted after migration) | superseded by `occ_handlers.py` |

**Naming correction:** the existing code name `LayerStackGitignoreOracle` is boundary-leaking and should not survive this migration. The future OCC-side wrapper should be named `SnapshotGitignoreOracle` (or a similarly neutral snapshot/content name), because the oracle evaluates gitignore rules for a manifest snapshot; layer-stack only supplies read/materialize primitives through a snapshot protocol.

**Narrow LSM role protocols:**

`LsmGateway` is the concrete AF_UNIX transport object, not the dependency type for every OCC component. Each OCC module receives the smallest role protocol it needs:

| Protocol | Methods | Consumed by |
|---|---|---|
| `SnapshotReader` | `get_active_manifest`, `get_snapshot`, `read_bytes`, `read_text` | `OccService`, `OccCommitTransaction`, `SnapshotGitignoreOracle` |
| `SnapshotMaterializer` | `materialize_snapshot` | git-backed `SnapshotGitignoreOracle` only |
| `CommitStagingStore` | `allocate_commit_staging`, `drop_commit_staging` | `OccCommitTransaction` / mutation coordinator |
| `CommitPublisher` | `compare_publish_layer` | `OccCommitTransaction` |
| `LeaseSessionFactory` | `open_lease_session` | shell mutation coordinator only |
| `OverlaySnapshotProvider` | `prepare_overlay_snapshot` or lease-acquire result carrying `OverlaySnapshotSpec` | shell mutation coordinator only |

The in-process `LayerStackManagerAdapter` used during migration implements these role protocols so existing daemon tests can run through the same boundaries before the servers are split.

Internal dependency shape:

```
occ_handlers.py
  └─ MutationCoordinator(
       snapshot_reader=SnapshotReader,
       commit_staging=CommitStagingStore,
       commit_publisher=CommitPublisher,
       lease_sessions=LeaseSessionFactory,
       overlay_client=OverlayClient,
       occ_service=OccService(snapshot_reader=SnapshotReader, gitignore=SnapshotGitignoreOracle)
     )

OccService never calls overlay and never opens leases.
OccCommitTransaction never sees `LsmGateway`; it sees `SnapshotReader`,
`CommitStagingStore`, and `CommitPublisher`.
```

### overlay-server (`/tmp/eos/ovr.sock`)

**Stateless kernel-side capture.** Receives a serialized request envelope plus a neutral `OverlaySnapshotSpec`, mounts/copies from the prepared read-only snapshot, executes a command, captures the upperdir, and returns the capture. It does not know how layer-stack manifests are stored, how snapshots are materialized, or how publishes work.

| Surface | Op | Owns |
|---|---|---|
| Overlay capture | `overlay.run` | wraps `runtime/overlay_shell/cli.execute_request` in `asyncio.to_thread` |

**State that lives here:** none. No caches, no tables, no active-manifest pointers.

**Inputs (in the envelope):**
- `request: dict` (already serializable via `overlay_shell_request_to_dict`, `snapshot_overlay_runner.py:46-53`)
- `snapshot: OverlaySnapshotSpec`
- `run_dir: str` (a path)

`OverlaySnapshotSpec` is the portability boundary:

```json
{
  "snapshot_id": "<lease-or-snapshot-id>",
  "manifest_version": 17,
  "lowerdir": "/tmp/eos/overlay-snapshots/<snapshot_id>/lower",
  "read_only": true
}
```

LSM owns building or locating `lowerdir` from the real layer-stack manifest. Overlay-server only consumes the read-only filesystem view named by the spec. That makes a future Rust overlay implementation depend on a filesystem contract, not Python `Manifest` / `MergedView` internals.

**Import fence:**
- Allowed: overlay-owned request/capture types and the neutral `OverlaySnapshotSpec`.
- Forbidden: every `sandbox.layer_stack` import, `LayerStackManager`, `Manifest`, `MergedView`, lease registry/budget, `LayerPublisher`, squash/GC workers, `OccService`, `OCCClient`, `OccCommitTransaction`, gitignore oracle, or any operation that reads the active manifest.

**Concurrency:**
- One asyncio loop accepts connections
- Each `overlay.run` dispatches to `asyncio.to_thread(execute_request, ...)`. The mount + bash + capture work happens in a thread; the GIL releases during mount syscalls and file I/O, so 10 concurrent overlay calls fan out on threads.
- No process pool. If shell-at-c=16 ever becomes a real workload, an overlay process pool can be added later as a swappable backend without changing the interface.

**Files (new):**
| File | Role |
|---|---|
| `sandbox/runtime/overlay_server.py` | asyncio AF_UNIX listener, one handler |

### Cross-server flow per verb

Concrete envelope sequence for each verb. Time-ordered; arrows are AF_UNIX hops:

**`api.read_file`:**
```
thin_client → lsm.read_text(layer_stack_root, path)
            ← {success, content, exists}
```
One hop. occ-server is not in the path because no OCC policy is needed.

**`api.write_file` / `api.edit_file`:**
```
thin_client → occ.write_file(args)
              occ → SnapshotReader.get_active_manifest(layer_stack_root)
                  ← {manifest, version}
              occ: build Change, prepare against snapshot
              occ: revalidate prepared paths against latest active manifest
              occ → CommitStagingStore.allocate_commit_staging(layer_stack_root, request_id)
                  ← {staging_id, staging_dir}
              occ: write accepted blobs into staging_dir
              occ → CommitPublisher.compare_publish_layer(expected={version,digest}, staged_changes)
                  ← {status: published|cas_mismatch|publish_failed, result, new_manifest?}
              occ: on cas_mismatch, drop staging, re-read active manifest, revalidate, retry
            ← {success, changed_paths, status, conflict?, timings}
```
Several internal LSM protocol calls, one host roundtrip. Correctness is the priority: the publish is never blind.

**`api.shell`:**
```
thin_client → occ.shell(args)
              occ: open request-scoped lsm lease session
              occ → lsm.acquire_lease(layer_stack_root, request_id, ttl_seconds)
                  ← {lease_id, manifest, overlay_snapshot: OverlaySnapshotSpec}
              occ → ovr.overlay.run({request, snapshot: overlay_snapshot, run_dir})
                  ← {capture}
              occ: overlay_capture_to_occ_changes(capture)
              occ: prepare, revalidate, stage blobs, compare_publish_layer
              occ → CommitPublisher.compare_publish_layer(expected={version,digest}, staged_changes)
                  ← {status: published|cas_mismatch|publish_failed, result}
              occ → lsm.release_lease(layer_stack_root, lease_id)
                  ← {released}
              occ: close request-scoped lsm lease session
            ← {success, exit_code, stdout, stderr, changed_paths, …}
```
Lease and publish add internal lsm hops; the host still sees one roundtrip.

**`api.compact`:** thin_client → lsm.squash + lsm.collect_garbage (two hops, optionally one if combined).

### Why reads route directly to LSM

`read_file` is a pure snapshot read. It does not build a `Change`, does not classify paths, does not apply gitignore routing, does not need base-hash inference, and does not publish a new layer. Routing it through occ-server would add a policy-looking hop without policy to execute.

`write_file` and `edit_file` are different. They are mutations, so they must enter through the mutation coordinator in occ-server:

1. OCC decides whether each path is OCC-gated, direct, rejected, ignored, or atomic.
2. OCC computes base hashes and validates conflicts against the active manifest.
3. OCC stages only accepted deltas.
4. LSM performs the final CAS publish as a policy-blind storage authority.

So write/edit **do** go through LSM for all durable state reads and the final publish. They do not route directly to LSM as host-facing verbs because that would force LSM to own OCC policy, violating the responsibility split.

### OCC commit transaction across servers

The critical rule: **LSM publishes, but OCC decides what is safe to publish.** Splitting processes must not turn today's `OccCommitTransaction.revalidate_and_publish(...)` into a blind `publish_layer(...)`.

The three-server equivalent is:

```
prepared = occ.prepare_changeset(changes, snapshot=leased_or_active_manifest)

for attempt in 1..MAX_CAS_RETRIES:
    active = await snapshot_reader.get_active_manifest(layer_stack_root)
    validations = occ.revalidate_prepared(
        prepared,
        active_manifest=active.manifest,
        content_reader=snapshot_reader.read_bytes,
    )

    if validations.should_skip_publish:
        return validations.result_without_publish

    staging = await commit_staging.allocate_commit_staging(layer_stack_root, request_id)
    try:
        staged_changes = occ.write_staged_blobs(staging.dir, validations.accepted_deltas)
        publish = await commit_publisher.compare_publish_layer(
            layer_stack_root,
            expected_version=active.version,
            expected_digest=active.digest,
            staged_changes=staged_changes,
            metadata=prepared.options,
        )
    finally:
        await commit_staging.drop_commit_staging(layer_stack_root, staging.id)

    if publish.status == "published":
        return occ.wrap_publish_result(validations, publish.new_manifest)
    if publish.status == "cas_mismatch":
        continue
    return occ.wrap_layer_stack_rejection(validations, publish)

return occ.cas_retry_exhausted(prepared)
```

`CommitPublisher.compare_publish_layer(...)` maps to `lsm.compare_publish_layer(...)` and runs under the layer-stack per-root lock. It re-reads `manifest.json`, compares `{version,digest}` to OCC's expected active identity, and only then calls the policy-blind publisher. On publish, lsm consumes the staging directory by copying/promoting the staged blobs into the immutable layer; `drop_commit_staging` must be safe to call afterward and should no-op for already-consumed staging. On mismatch it publishes nothing and returns `cas_mismatch`. OCC then revalidates against the new active manifest instead of reusing stale validation results.

## Wire format

Identical newline-delimited JSON framing on all three sockets, same shape as today's daemon protocol (`runtime/daemon.py:55-103`). The op namespace splits routing:

| Op prefix | Destination | Notes |
|---|---|---|
| `lsm.*` | layer-stack-server | host-facing for read-only verbs; internal-facing for everything else |
| `overlay.run` | overlay-server | only occ-server calls this |
| `api.*` | occ-server | host-facing |

**Request:**
```json
{"op": "<dotted-op-name>", "args": {...}, "request_id": "<uuid>"}\n
```

**Response:**
```json
{"success": true|false, "result": {...}, "warnings": [...], "timings": {...},
 "error": {"kind": "...", "message": "...", "details": {...}}}\n
```

The thin client picks the socket from the op prefix in <10 lines of Python — same complexity as today's `command.py:39-49` thin client, with one if/elif on the prefix.

## Lease lifetime across processes

The hardest correctness property in this plan. Today the lease lives in the same process that holds the manager; releases are guaranteed by `try/finally`. Splitting lsm out means a lease can leak if occ-server crashes, is cancelled, or times out between `acquire_lease` and `release_lease`.

**Correct contract: lease-carrying work uses a request-scoped LSM session, not a long-lived shared socket.**

For `api.shell`, occ-server opens a dedicated lsm socket for that shell request and keeps it open until release:

```python
async with lsm.open_lease_session(layer_stack_root, request_id, ttl_seconds=120) as lease_session:
    lease = await lease_session.acquire_lease()
    try:
        capture = await overlay.run(request=request, snapshot=lease.overlay_snapshot)
        result = await occ.apply_capture_with_cas_publish(capture)
        return result
    finally:
        await lease_session.release_lease(lease.lease_id)
```

The layer-stack-server still tracks connection ownership, but only as a cleanup accelerator: if the request-scoped socket closes, lsm releases every lease owned by that socket. Long-lived pooled lsm connections are allowed only for non-lease operations (`get_active_manifest`, `read_text`, metrics) and must be rejected by `lsm.acquire_lease`.

**Durable lease records:**

On `acquire_lease`, lsm writes an atomic lease record under `<storage_root>/leases/<lease_id>.json` containing:

```json
{
  "lease_id": "...",
  "owner_id": "<request_id>",
  "manifest_version": 17,
  "pinned_layers": ["..."],
  "acquired_at": 123.0,
  "expires_at": 243.0,
  "connection_id": "..."
}
```

On `release_lease`, lsm removes that file and updates the in-memory refcount index. On startup, lsm reloads every lease file and treats those layers as pinned until either the owner releases the lease or the lease expires. Long shell commands can call `heartbeat_lease` to extend `expires_at`.

If lease acquisition prepared an `OverlaySnapshotSpec`, release also cleans that prepared snapshot unless another active lease references it. Overlay cleanup remains LSM-owned because LSM created the read-only snapshot view.

**Failure behavior:**

| Failure | Required behavior |
|---|---|
| occ-server coroutine cancelled | async context manager closes the request-scoped lsm socket; lsm auto-releases the lease |
| occ-server process killed | kernel closes the request-scoped socket; lsm auto-releases the lease |
| overlay-server killed | occ catches the overlay failure and releases in `finally` |
| lsm-server killed | restarted lsm reloads durable leases before accepting squash/GC; in-flight OCC calls fail closed and must retry or return an error |
| lsm and occ both killed | durable lease files keep layers pinned until TTL expiry; lease-budget cleanup handles stale owners |

This is slightly more work than a single persistent occ→lsm socket, but it removes the leaked-lease class without relying on perfect coroutine cleanup.

## Migration

`EPHEMERALOS_RUNTIME_TRANSPORT` becomes a 2-valued knob, then disappears:

| Value | Behavior | Status |
|---|---|---|
| `daemon` | unchanged single-process daemon | retained one release for rollback |
| `three_server` | new — lsm + occ + ovr supervised separately | becomes default after gate |
| `fork` | **REMOVED** | dropped in this plan |

After the verification gate passes:
1. Default flips to `three_server`.
2. Next release: `fork` plumbing deleted from `command.py` (~80 LoC), `_runtime_server_command`, `_RUNTIME_SERVER_LAUNCHER` removed.
3. Release after that: `daemon` plumbing removed; `runtime/daemon.py` and `runtime/prepare_pool.py` deleted.

## Verification

### Unit tests

* `backend/tests/unit_test/test_sandbox/test_runtime/test_layer_stack_server.py` — handler dispatch, durable lease acquire/release, request-scoped socket auto-release, restart reload of lease files, manifest read consistency.
* `backend/tests/unit_test/test_sandbox/test_runtime/test_occ_server.py` — handler dispatch, `OccService` wired through narrow role protocols, prepare → revalidate → compare-publish round-trip, CAS-mismatch retry.
* `backend/tests/unit_test/test_sandbox/test_runtime/test_overlay_server.py` — overlay.run framing with `OverlaySnapshotSpec`, error propagation, threaded dispatch under concurrent load.
* `backend/tests/unit_test/test_sandbox/test_runtime/test_lsm_gateway.py` — connection lifecycle, request-scoped lease session lifecycle, retry on broken pipe for non-lease calls, op routing.
* `backend/tests/unit_test/test_sandbox/test_runtime/test_three_server_supervision.py` — three PIDs, kill any one and observe the fail-closed restart behavior.

### Live latency attribution sweep

Run `test_latency_attribution.py` in two modes back-to-back: `daemon` (today) and `three_server` (new). The decisive comparison is **correctness and operational-equivalence**, not latency wins.

### Pass bar (c=16 p99)

| Metric | Target | Notes |
|---|---:|---|
| read_file wall p99 | ≤ today's daemon p99 | one less hop on read; should match or beat |
| write_file wall p99 | ≤ today's daemon-no-pool p99 (~870 ms) | accept the prepare_pool removal regression |
| edit_file wall p99 | ≤ today's daemon-no-pool p99 (~870 ms) | same |
| shell wall p99 | ≤ today's daemon p99 (no perf goal) | shell regression remains an open audit item, separate from this plan |
| Drift across c=1..16 | 0 | conflict semantics preserved end-to-end |
| Three-server crash + auto-restart | recovers within 2 s | in-flight calls may fail closed; durable leases must not be forgotten |
| Lease leak after occ-server kill -9 | 0 | request-scoped socket auto-release works |
| Lease pin loss after lsm-server kill -9 | 0 | lsm reloads durable lease records before squash/GC |
| CAS publish blind-write regressions | 0 | induced active-manifest race returns `cas_mismatch`, revalidates, then publishes or rejects correctly |
| Broad LSM dependency regressions | 0 | `OccService` and `OccCommitTransaction` depend on role protocols, not `LsmGateway` or `LayerStackManager` |
| Forbidden overlay imports | 0 | no `sandbox.layer_stack`, `LayerStackManager`, `Manifest`, `MergedView`, lease, publish, OCC, or active-manifest reads in overlay-server |
| `from daytona` outside `sandbox/providers/daytona/` | 0 | adapter invariant unchanged |

### Operator checks

* `eos-runtime-status` reports three named processes with PID, socket, last-activity timestamp.
* Killing any one server logs a structured failure and triggers respawn within 2 s.
* Tail of `lsm.log` / `occ.log` / `ovr.log` shows the per-server hot ops (no cross-talk).

## Risks

* **prepare_pool removal regresses edit/write p99 by ~110 ms at c=16.** Documented and accepted per the workload assumption (10 concurrent agents, transport floor at ~700 ms, 110 ms regression on edit is below noise). If this proves unacceptable in production, prepare can be threaded inside occ-server via a small ThreadPoolExecutor — won't recover the full delta but partially closes it.
* **Lease lifetime split across processes.** Request-scoped lsm sessions plus durable lease files close the leaked-lease and lsm-restart holes, but add one small persistent lease record per active shell call. Tested explicitly via coroutine cancellation, `kill -9` on occ-server, and `kill -9` on lsm-server during an active shell lease.
* **CAS retry path is new cross-process correctness code.** OCC must never reuse validation results after `CommitPublisher.compare_publish_layer` returns `cas_mismatch`. The retry loop is tested with induced active-manifest races and with shell-captured tracked-file conflicts.
* **Manifest serialization cost on prepare and lease acquisition.** `SnapshotReader.get_active_manifest` and `lsm.acquire_lease` return the full manifest dict over AF_UNIX. For deep stacks (50+ layers) this is non-trivial — measure during verification. If it dominates, lsm can return a stable `(version, snapshot_id)` handle and occ-server can cache the manifest body keyed by version.
* **Three PIDs to supervise.** Operator complexity grows. Mitigation: one `eos-sandbox-runtime` supervisor (already exists for one daemon today) generalizes to three children with the same restart policy.
* **Internal AF_UNIX hops add latency.** Sub-millisecond in steady state, but worth measuring under load. Plan acknowledges 2-5 ms of added in-sandbox latency per write/edit; this stays well under the 700 ms transport floor.
* **OccService refactor surface.** Changing `OccService` and commit code to take narrow role protocols instead of a `LayerStackManager` is the largest single refactor in the plan. Mitigation: keep the role protocols explicit in this document; add a `LayerStackManagerAdapter` so existing callers can keep passing a manager during migration; add an import-fence test that blocks `LayerStackManager` and broad `LsmGateway` imports from OCC policy modules after the cutover.

## Effort estimate

| Component | Days |
|---|---:|
| layer-stack-server: AF_UNIX listener + handlers | 1.5 |
| Durable/request-scoped lease tracking | 1 |
| LsmGateway transport + narrow role protocols + lease sessions | 2 |
| OccService refactor to take role protocols | 1.5 |
| Cross-process CAS publish/retry loop | 1 |
| occ-server: extract handlers, port to its own listener | 1.5 |
| overlay-server: AF_UNIX listener + threaded dispatch | 0.5 |
| install/supervise: three PIDs, three sockets, latch | 1.5 |
| thin_client.py: 2-socket op routing | 0.5 |
| bundle.py changes (include three modules) | 0.5 |
| Unit tests | 2.5 |
| Live verification sweep | 1 |
| Documentation + plan-update + report | 1 |
| **Total** | **~16 working days** |

Slightly larger than the original 15-day two-server plan. We drop the prewarm-pool / overlay_worker / overlay_pool machinery, but add durable lease records, neutral overlay snapshot specs, role-protocol indirection, and the explicit cross-process CAS publish contract. Larger than the simpler 2-server variant (~6 days) because of the gateway/protocol split and restart-safe lease semantics.

## What this would unlock

* **Layer-stack becomes a swappable component.** Reimplement layer-stack-server in Rust later — overlay-server and occ-server don't change. The wire format is the contract.
* **Read path bypasses OCC entirely.** `read_file` goes directly to lsm; no OCC-related code on the read hot path.
* **Three named services with obvious failure modes.** `lsm up?` `occ up?` `ovr up?` — operationally legible. No more "why did the daemon crash" debugging across mixed concerns.
* **Multi-tenant or sharded futures are tractable.** Multiple OCC instances sharing one lsm; replicated-read lsm; per-bucket OCC sharding — none of these are forced by this plan, but each becomes a bounded change once the boundary is clean.

## What this would *not* unlock

* The `process.exec` floor stays. Verb-level batching (`write_batch` / `edit_batch` / `read_batch`) remains the next high-leverage win for latency, and is unaffected by the three-server split.
* Shell regression at c=16 vs fork remains open. This plan does not solve the overlay-capture I/O serialization issue identified in the post-Phase-4 A/B/C sweep. Whether that needs solving depends on whether shell becomes a hot verb in production.
* GIL-bound prepare returns. The prepare_pool fix is removed; if prepare CPU contention bites again at higher concurrency, a threaded executor inside occ-server is the cheap recovery path.

## Open questions

1. **Should reads ever route through occ-server for telemetry symmetry?** Default is no: `read_file` routes directly to lsm because it has no OCC policy. Add an occ pass-through only if a real consumer requires OCC-level timing keys on reads.
2. **Does the snapshot gitignore oracle stay in occ-server, or move to lsm?** It stays in occ-server. It is an OCC routing dependency keyed by manifest snapshot version; LSM only provides snapshot reads/materialization and must not own gitignore policy.
3. **Do non-lease LSM calls use pooled sockets or one socket per call?** Lease-carrying shell workflows must use request-scoped sockets. Non-lease reads/metrics/manifest calls can use short-lived sockets first, then pool later only if profiling shows connect overhead matters.

## Next steps

1. Review this plan with the team / next maintainer. Three-server is bigger conceptually than two-server even if smaller in LoC; explicit alignment on the responsibility split is the first thing to validate.
2. If approved, land in three checkpoints:
   * **Checkpoint A:** layer-stack-server + `LsmGateway` + explicit role protocols + `LayerStackManagerAdapter` + cross-process CAS publish API. Existing daemon keeps running unchanged but now exercises the protocols. ~6 days.
   * **Checkpoint B:** request-scoped durable lease sessions, neutral `OverlaySnapshotSpec`, occ-server extracted from the daemon and pointed at lsm-server, overlay-server replaces the in-daemon overlay path. Three-server topology live behind the feature flag. ~7 days.
   * **Checkpoint C:** Default flips after crash/restart/CAS gates pass, fork plumbing deleted, prepare_pool deleted, daemon.py deletion scheduled for next release. ~3 days.
3. Update `api-latency-reduction-plan.md` and `two-server-architecture-plan.md` to point at this plan as the chosen successor.
