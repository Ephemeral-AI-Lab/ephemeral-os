# Sandbox Shell Workflow

How `SandboxAPI.shell` runs a command end-to-end, what the command sees on
disk, and how `layer_stack`, `overlay`, and `occ` connect.

Source of truth: `backend/src/sandbox/`.

---

## 0. Glossary

| Term | Meaning |
|---|---|
| **Manifest** | Ordered list of layers that defines the active workspace at version `N`; swapped atomically on publish. |
| **Layer** | Immutable directory `layers/L00000K-<id>/` holding one committed delta. `L000001-base` is the full base repo; `L1..LN` is shorthand for the stack. |
| **Lease** | A pin held against a manifest version `N` so its layers cannot be GC'd while a command runs. |
| **lowerdir / upperdir / workdir** | Standard Linux overlayfs roles: read-only merged base / writable layer for new edits / overlay scratch. |
| **Whiteout / opaque dir** | Overlayfs markers for "file deleted" and "directory contents replaced wholesale." |
| **OCC** | Optimistic concurrency control: validate captured writes against the base manifest version, publish or abort. |
| **Gated vs Direct merge** | Gated = revalidate base hash under publish lock (tracked source). Direct = last-writer-wins (gitignored artifacts). |
| **`unshare -Urm`** | Create a new user + mount namespace so per-call mounts are invisible to other processes and auto-cleaned on exit. |

---

## 1. Workflow: `SandboxAPI.shell` to result

### 1.1 Big picture

```
┌──────────────────────── HOST PROCESS ────────────────────────┐
│  caller ──► SandboxAPI.shell(sandbox_id, request)            │
│                       │                                      │
│                       ▼                                      │
│             api/tool/shell.py:shell()                        │
│                       ▼                                      │
│             api/tool/_runtime.py:call_runtime_api            │
│                       ▼                                      │
│             host/rpc/client._call_runtime_server             │
└───────────────────────┼──────────────────────────────────────┘
                        │  provider.exec (RPC into sandbox)
                        ▼
┌─────────────────── SANDBOX (runtime daemon) ─────────────────┐
│  daemon/handlers/shell.shell ─► services/shell_runner        │
│                                       │                      │
│         ┌─────────────────────────────┼─────────────────┐    │
│         ▼                             ▼                 ▼    │
│    layer_stack                     overlay            occ    │
│    (snapshot +                  (mount + capture)  (validate │
│     leases +                                       + publish)│
│     publish)                                                 │
│                                                              │
│   On-disk:  <layer_stack_root>/                              │
│      ├─ manifest.json     (active pointer)                   │
│      ├─ layers/L00000N-*  (immutable layer dirs)             │
│      ├─ staging/*         (OCC commit staging)               │
│      ├─ runtime/                                             │
│      │   └─ transient-lowerdirs/<req>/lower/  (per-call)     │
│      └─ workspace.binding (workspace_root ↔ stack)           │
│                                                              │
│   /dev/shm/eos-command-exec/<root>/<req>/                    │
│      ├─ upper/            (overlay upperdir)                 │
│      ├─ work/             (overlay workdir)                  │
│      ├─ stdout.bin / stderr.bin                              │
│      └─ namespace-request.json                               │
└──────────────────────────────────────────────────────────────┘
```

The host knows nothing about mounts, manifests, or layers; it only sees the
JSON dict the daemon returns.

### 1.2 End-to-end sequence

Two views: host→daemon transport, then in-sandbox orchestration.

**Host → daemon (transport):**

```
caller        SandboxAPI.shell        _runtime           daemon
  │   shell        │                     │                 │
  ├───────────────►│ call_runtime_api    │                 │
  │                ├────────────────────►│ provider.exec   │
  │                │                     ├────────────────►│ api.shell(args)
  │                │                     │                 ├─► _execute_shell (below)
  │                │                     │                 │
  │                │                     │◄──── dict ──────┤
  │ ShellResult ◄──┤◄───── raw dict ─────┤                 │
```

**Inside the sandbox — `_execute_shell` orchestrates:**

```
cmd_exec               layer_stack            overlay/mount         occ
   │                       │                       │                 │
   │ prepare_workspace_snapshot                    │                 │
   ├──────────────────────►│ RLock + lease + materialize lowerdir    │
   │◄── lease, Manifest(N), lowerdir ──────────────│                 │
   │                                               │                 │
   │ run_workspace_replaced_command                │                 │
   ├──────────────────────────────────────────────►│ unshare -Urm    │
   │                                               │ mount overlay   │
   │                                               │ exec argv       │
   │◄────────── stdout / stderr / exit ────────────┤                 │
   │                                                                 │
   │ capture_workspace_upperdir (walk upper/)                        │
   │ workspace_changes_to_occ_changes                                │
   │                                                                 │
   │ OCCClient.apply_changeset                                       │
   ├────────────────────────────────────────────────────────────────►│
   │   prepare (route + base_hash) → serial_merger.apply             │
   │     └─ commit_transaction (RLock): revalidate, stage, publish L(N+1)
   │◄────────────────────── ChangesetResult ─────────────────────────┤
   │                                                                 │
   │ release_lease, drop transient-lowerdir/<req>/                   │
   │                                                                 │
   └─► dict {success, stdout, stderr, exit, changed_paths, conflict, timings}
```

### 1.3 Phase-by-phase

#### Phase 1 — Host marshalling

`api/tool/shell.py:shell` builds the args dict, normalizes absolute `cwd`
to `"."`, and ships it via `call_runtime_api` (`api/tool/_runtime.py`),
which adds `layer_stack_root=$BUNDLE_REMOTE_DIR/layer-stack` and forwards
through the provider adapter into the sandbox's resident runtime daemon.

#### Phase 2 — Lease a snapshot (layer_stack)

`runtime/command_exec_server._execute_shell` calls
`LayerStackManager.prepare_workspace_snapshot`:

```
   ┌─────────────────────── manifest.json (v=N) ─────────────────────┐
   │ layers = [ L1-base, L2-edits, L3-build, … LN-recent ]           │
   └─────────────────────────────────────────────────────────────────┘
                       │
       acquire RLock, copy ref, register lease(req_id) ──┐
                       │                                  │
                       ▼                                  ▼
    materialize(lowerdir, manifest)            LeaseRegistry pins
    apply L1..LN bottom-up into                layers so they cannot
    runtime/transient-lowerdirs/<req>/lower/   be GC'd while in use
```

Returns:

```
lease_id            = "lease-abcd…"
manifest_version    = N
manifest            = Manifest(N, (L1..LN))    ← shared with OCC later
lowerdir            = "<root>/runtime/transient-lowerdirs/<req>/lower"
```

#### Phase 3 — Mount and run (overlay / namespace)

`command_exec/workspace_mount.run_workspace_replaced_command`:

```
  workspace_root  = /testbed   (declared, what command literals expect)
  lowerdir        = …/lower    (read-only merged snapshot)
  upperdir        = …/upper    (empty, captures writes/whiteouts)
  workdir         = …/work     (overlay scratch)

  Linux + userns?
    yes ──► unshare -Urm
            mount -t overlay overlay /testbed \
              -o lowerdir=…/lower,upperdir=…/upper,workdir=…/work
            chdir(/testbed/<request.cwd>)
            exec argv  → stdout.bin / stderr.bin / exit_code
            (namespace dies → mount auto-cleared)
    no  ──► copy_backed: cp -r lower → run_dir/workspace
            chdir there, exec; capture upperdir = workspace minus lower
            (rejects commands that literally name /testbed)
```

Inside the namespace the process sees a complete filesystem: every
committed layer applied, plus a clean upperdir for its own writes. It
does not see other concurrent shells.

> **What does the command actually see at `/testbed`?**
> The lowerdir is the *full* merged view: `L000001-base` (a complete
> workspace copy) plus every committed layer on top — not a partial
> overlay. In `private_namespace` mode it is overlay-mounted onto the
> declared `workspace_root` itself, so absolute literals like
> `/testbed/foo` resolve naturally. Everything outside the workspace
> (`/usr`, `/home`, `/etc`) is the host sandbox FS unchanged.
> In `copy_backed` fallback the same content lands at `run_dir/workspace`
> instead, so commands referencing `/testbed` literals are rejected.
> The mount is per-call and ephemeral; two concurrent shells get two
> independent overlays over the same lowerdir snapshot version.

#### Phase 4 — Capture changes (overlay)

`command_exec/capture/upperdir.capture_workspace_upperdir` →
`overlay/capture/upperdir.capture_changes` walks `upper/`:

```
  upper/
    src/foo.py            ──► OverlayPathChange(write, "src/foo.py", hash, size)
    src/bar.py            ──► OverlayPathChange(write, "src/bar.py", …)
    {whiteout}old.txt     ──► OverlayPathChange(remove, "old.txt")
    {opaque}build/        ──► OverlayPathChange(opaque_dir, "build")
```

Then `workspace_changes_to_occ_changes` adapts those into typed
`occ.changeset.types.Change` records (with `source="overlay_capture"`).

This is the single boundary between overlay and OCC: a typed sequence of
path-level events, no FS state required.

#### Phase 5 — OCC validate + publish

`occ/service.py` and `occ/commit_transaction.py` run in two stages.

**Stage A — `prepare_changeset_sync` (in executor, lock-free).** Each
change is routed by `OccOrchestrator(gitignore_oracle).route(change)`:

| Route | Condition | Behavior |
|---|---|---|
| `DROP` | path is `.git` or under `.git/` | Discard silently. |
| `OCC_SKIPPED_MERGE` | path is gitignored (build artifacts, `.venv`, `node_modules`, `__pycache__`) | Direct merge, last-writer-wins, no base-hash check. |
| `OCC_GATED_MERGE` | tracked source file (default) | Gated merge, revalidates against `base_hash` under the publish lock; conflicts abort. |
| `REJECT` | path normalization failed | Refused without staging. |

For gated rows, `base_hash = infer_manifest_base_hash(layer_stack, N, path)`
is captured up front. Output: `PreparedChangeset(path_groups=[…],
snapshot=Manifest(N), atomic=…)`.

**Stage B — `serial_merger.apply(prepared)` (single worker, under
`commit_transaction` RLock):**

```
active = transaction.snapshot()                  # may now be N+k
for group in prepared.path_groups:
    GatedMerge / DirectMerge revalidate against `active`
    → FileResult(ACCEPTED | ABORTED_VERSION | ABORTED_OVERLAP | …)
    stage accepted bytes into staging/occ-commit-…/

if (atomic and any failure) or (overlay_capture and any gated failure):
    publish nothing
else:
    transaction.publish_layer(changes)            # LayerPublisher
        writes layers/L00000(N+1)-<id>/  (immutable)
        os.replace(manifest.tmp, manifest.json)   (atomic swap)
        N → N+1  (or N+k+1 under concurrency)
```

Why gitignored paths skip OCC: untracked artifacts (build outputs,
`.venv`, `node_modules`, `__pycache__`) are expected to be overwritten
concurrently and have no semantically meaningful base content.
OCC-gating them would generate spurious conflicts with no benefit.
Tracked source paths are where stale-base writes must be rejected so
two concurrent shells don't silently clobber each other's edits.

Disk view before vs. after a successful publish:

```
BEFORE                                 AFTER
manifest.json: v=N                     manifest.json: v=N+1  (atomic os.replace)
layers/                                layers/
  L1-base/                               L1-base/
  L2-edits/                              L2-edits/
  …                                      …
  LN-recent/                             LN-recent/
                                         L(N+1)-<id>/   ← only accepted paths
staging/                               staging/
  occ-commit-<uuid>/  (active)           (drained on context exit)
```

Returned to the daemon: `ChangesetResult(files=[FileResult(...)],
published_manifest_version=N+1, timings={…})`.

#### Phase 6 — Release & respond

```
layer_stack.release_lease(lease_id)
    → LeaseRegistry drops the pin on L1..LN
    → unreferenced layers (if any were squashed away) get GC'd
shutil.rmtree(transient-lowerdirs/<req>/)

_payload_from_result builds:
  { success, exit_code, stdout, stderr,
    changed_paths, status, conflict, conflict_reason,
    workspace_capture: {snapshot_version=N, mount_mode, changes},
    timings }
```

Daemon returns the dict; host `_result_from_payload` rehydrates a
`ShellResult`.

---

## 2. Are OCC, layer_stack, and overlay loosely coupled?

**Yes — deliberately, via narrow protocol ports.**

### 2.1 Ports

`sandbox.occ.ports.OccLayerStackPorts` is the only contract OCC needs
from storage. It is the union of three narrow protocols:

- `SnapshotReader` — `read_active_manifest`, `read_bytes`, `read_text`
- `CommitStagingStore` — `allocate_commit_staging`,
  `drop_commit_staging`
- `CommitPublisher` — `commit_transaction()` returning a
  `CommitTransaction` (which exposes only `snapshot()` and
  `publish_layer(changes)`)

`LayerStackClient` (`daemon/services/layer_stack_client.py`) implements that
union by forwarding to `LayerStackManager`. OCC never imports
`LayerStackManager`; it only sees `OccLayerStackPorts`.

Dependency arrow: **OCC → ports ← layer_stack** (layer_stack does not
import OCC at all).

### 2.2 Module dependency picture

```
                       ┌────────────────────────────────┐
   command_exec ──────►│ overlay.capture (path diffs)   │
        │              └─────────────────┬──────────────┘
        │                                ┊ adapter
        ├─────────────►┌─────────────┐   ┊
        │              │ layer_stack │   ┊  (storage substrate)
        │              └──────┬──────┘   ┊
        │                     │ implements
        │                     ▼          ▼
        └─────────────►┌────────────────┐       ┌────────┐
                       │  occ.ports     │◄──────┤  occ   │
                       │  (Protocols)   │       └────────┘
                       └────────────────┘
```

### 2.3 Connections

The overlay capture layer (`sandbox/overlay/capture/...` and
`sandbox/command_exec/capture/...`) is independent of both OCC and
layer_stack. It produces a typed `Sequence[OverlayPathChange]` from an
upperdir + snapshot manifest. The boundary into OCC is
`workspace_changes_to_occ_changes(path_changes)`
(`command_exec/capture/changeset.py`), which converts overlay events
into `occ.changeset.types.Change` objects.

- Overlay knows nothing about OCC routing, gitignore, or transactions.
- OCC knows nothing about mounts, namespaces, or `unshare`.

The runtime command-exec layer (`daemon/services/shell_runner.py`) is
the only place all three meet. `_execute_shell` is the **sole
orchestration sink**: it holds `lease_id`, `Manifest(N)`, `lowerdir`,
and the captured changes only as local variables for one call —
nothing is stored across calls, and no other module reaches into more
than one subsystem. Each pair of subsystems remains decoupled through
these boundaries:

- layer_stack ↔ overlay: only via the materialized lowerdir path + the
  leased `Manifest` value.
- overlay ↔ OCC: only via the `OverlayPathChange → Change` adapter
  (`command_exec/capture/changeset.py`). The split between
  `overlay/capture/` and `command_exec/capture/` is deliberate:
  `overlay/capture/` knows pure overlayfs semantics (whiteouts, opaque
  dirs); `command_exec/capture/` knows the runtime context
  (workspace_root, snapshot manifest) and adapts the result for OCC.
- OCC ↔ layer_stack: only via `OccLayerStackPorts`.

### 2.4 Real but necessary coupling

They share a leased `Manifest` value as the snapshot of truth for a
single command. The lease pins layers in layer_stack so OCC's
revalidation can compare against the base hashes the command actually
saw. That is a logical contract on a value, not a code dependency.

Net: layer_stack is the storage substrate (CAS layers, manifest,
leases, transactions), overlay is a pure FS-diff producer/consumer,
OCC is a validator/serializer that only sees ports. Swapping any one
for another implementation is a port-level change, not a rewrite of
the others.

---

## 3. State and concurrency

### 3.1 Snapshot lifecycle for one shell call

```
                acquire_snapshot_lease(req_id)
   ┌──────────────────────────────────────┐
   ▼                                      │
[idle]──prepare──►[leased(N), lowerdir]──run──►[leased, captured]
                                                    │
                                               apply_changeset
                                                    │
                                                    ▼
                            [committed N→N+1 or rejected/conflict]
                                                    │
                                                release_lease
                                                    │
                                                    ▼
                                                 [idle]
```

A second call starting concurrently would be at `[leased(N or N+1),
lowerdir2]`. They share the publisher RLock for the publish step
only; reads, materialize, and command execution overlap.

### 3.2 Concurrency surfaces

```
SERIAL MERGE                   OccSerialMerger.apply
  (occ/serial_merger.py)       one worker, ~2ms batch window;
                               coalesces disjoint commits

CROSS-PROCESS                  fcntl flock on <root>/.commit.lock
  _commit_lock                 skipped inside resident daemon
                               (single process — asyncio gate suffices)

THREAD/RLock                   LayerStackManager._lock
  (layer_stack/stack_manager)  guards manifest read/swap, lease
                               registry, layer dir delete

```

Each subsystem owns one lock concept; they do not nest each other
except through the documented `commit_transaction()` port.

---

## 4. File index

| Concern | Module |
|---|---|
| Host entrypoint | `sandbox/api/tool/shell.py`, `sandbox/api/facade.py` |
| Host → daemon transport | `sandbox/api/tool/_runtime.py`, `sandbox/host/rpc/client.py` |
| Daemon dispatch | `sandbox/daemon/rpc/dispatcher.py` |
| Shell orchestrator | `sandbox/daemon/services/shell_runner.py` |
| Mount + exec | `sandbox/command_exec/workspace_mount.py`, `sandbox/command_exec/namespace_helper.py` |
| Upperdir capture | `sandbox/overlay/capture/upperdir.py`, `sandbox/command_exec/capture/upperdir.py` |
| Overlay → OCC adapter | `sandbox/command_exec/capture/changeset.py` |
| OCC service | `sandbox/occ/service.py`, `sandbox/occ/orchestrator.py` |
| OCC commit | `sandbox/occ/commit_transaction.py`, `sandbox/occ/serial_merger.py` |
| OCC ports | `sandbox/occ/ports.py` |
| Layer stack | `sandbox/layer_stack/stack_manager.py`, `sandbox/layer_stack/publisher.py`, `sandbox/layer_stack/merged_view.py` |
| Layer stack client | `sandbox/daemon/services/layer_stack_client.py` |
| Workspace base | `sandbox/layer_stack/workspace_base.py`, `sandbox/layer_stack/workspace.py` |
