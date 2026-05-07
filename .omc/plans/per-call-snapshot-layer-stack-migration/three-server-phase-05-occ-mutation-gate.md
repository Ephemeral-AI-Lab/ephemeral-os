# Phase 05 - OCC Mutation Gate

**Status:** draft implementation plan
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

## 1. Task Specification

Host `api.write_file`, `api.edit_file`, `api.read_file`, and `api.shell` on
`command-exec-server` as the single host-facing API surface. In-workspace
mutations from any of the three mutating apis pass through one OCC mutation
gate via `OCCClient.apply_changeset`. `occ-server` owns mutation policy
(gitignore routing, base-hash computation, conflict handling, staging, CAS
retry) and exposes only the internal `apply_changeset` method plus lifecycle —
no host-callable write/edit/read endpoints.
`layer-stack-server` remains the policy-blind storage publisher.

Implementation scope:

```text
host api.write_file, api.edit_file, api.read_file, api.shell on
  command-exec-server (single host-facing API surface; api.read_file moves
  alongside write/edit because it shares the SnapshotReader port and the
  same in/out-of-workspace classifier)
delete api.write_file / api.edit_file / api.read_file dispatch from
  runtime/api_handlers.py (today's owner); api_handlers.py shrinks to
  layer_metrics + shared service-cache helpers, or is deleted entirely
  if those move with the handlers
command-exec-server classifies the request path before dispatch:
  in-workspace  -> OCCClient.apply_changeset (full OCC mutation gate)
  out-of-workspace -> direct host-FS write/read+edit, no OCC, no layer-stack
  (matches shell's namespace-passthrough: writes to /tmp, /home, /etc from
   shell already land on the host sandbox FS unchanged)
in-workspace classifier predicate (single source of truth, command-exec):
  abs = realpath(path)
  in_workspace = abs == workspace_root or abs.startswith(workspace_root + "/")
  reject any unresolved ".." segment after realpath as a hard error;
  symlinks resolving outside workspace_root classify as out-of-workspace;
  symlinks resolving inside are in-workspace
occ-server's only externally reachable surface is:
  - OCCClient.apply_changeset (the mutation gate)
  - lifecycle: start, stop, health
  no api.write_file / api.edit_file / api.read_file symbols on occ-server
edit byte derivation (read snapshot, apply search/replace) runs in
  command-exec-server using the SnapshotReader port; OCC sees only final bytes
acquire short-lived snapshot lease in command-exec-server for in-workspace
  write/edit covering prepare->publish; reuse the existing shell LeaseRegistry
  (single registry, per-call lease key) so layer-stack GC sees a unified pin
  set across shell and write/edit
OCCClient.apply_changeset prepares typed changes against the leased snapshot
api.write_file and api.edit_file are single-path calls: classification yields
  exactly one branch per request, so atomicity is per-call. Multiple edits on
  the same path collapse to one EditChange with one base_hash and one
  apply_changeset call; the api never mixes in-workspace and out-of-workspace
  paths in the same request
revalidate against latest active manifest before publish
stage accepted final bytes (write and edit both stage)
publish through layer-stack compare_publish_layer CAS
retry prepare/revalidate on CAS mismatch up to MAX_OCC_CAS_RETRIES (default 3)
on retry exhaustion surface a conflict result, never loop indefinitely
```

Out of scope:

```text
no command execution ownership in OCC
no layer storage layout ownership in OCC
no host-callable api.write_file, api.edit_file, or api.read_file on occ-server
no host-side layer-stack precheck for write/edit
no direct capture-to-OccService call
no path classification (in/out of workspace) inside occ-server
no separate lease registry for write/edit (shell + write/edit share one
  LeaseRegistry; only the lease key differs per call)
no atomicity across multiple paths in a single api.write_file / api.edit_file
  call (these apis are single-path by contract)
```

Exit condition:

```text
all in-workspace mutations publish through occ-server's apply_changeset,
out-of-workspace ops bypass OCC and land on host FS via command-exec,
shell tracked conflicts publish no partial shell layer,
layer-stack remains policy-blind, and occ-server has no host-callable
write/edit endpoints.
```

## 2. Main Data Objects

```text
OCCClient
  apply_changeset(workspace_ref, changeset, snapshot, options)

WriteChange
  path
  content
  overwrite/create policy
  base_hash when OCC-gated

EditChange
  path
  search
  replace
  expected_occurrences
  base_hash

PreparedChangeset
  snapshot identity
  accepted changes
  dropped changes
  rejected changes
  base hashes
  atomicity group

ChangesetResult
  accepted paths
  dropped paths
  rejected paths
  conflict paths
  changed_paths
  timings
```

## 3. File/Folder Structure Change

Target additions, updates, and removals:

```text
backend/src/sandbox/runtime/
|-- command_exec_server.py        (existing, hosts api.shell)
+-- write_edit_handlers.py        (new: api.write_file, api.edit_file,
|                                  api.read_file, path classification,
|                                  in/out-of-workspace dispatch, edit byte
|                                  derivation via SnapshotReader,
|                                  OCCClient.apply_changeset call,
|                                  direct-FS fallback)
~-- api_handlers.py               (modified: write_file / edit_file /
|                                  read_file dispatch removed and migrated
|                                  to write_edit_handlers.py; layer_metrics
|                                  + service-cache helpers stay or move
|                                  with the handlers. Delete the file
|                                  entirely once empty.)
+-- occ_server.py                 (internal; hosts only OCCClient backend
|                                  not host-facing write/edit)
+-- occ_handlers.py               (externally reachable surface:
|                                  apply_changeset + start/stop/health
|                                  lifecycle methods only)

backend/src/sandbox/occ/
|-- client.py                     (OCCClient — used by command-exec-server)
+-- mutation_coordinator.py
|-- service.py                    (no path classification, no workspace
|                                  binding, no direct-FS fallback)
|-- commit_transaction.py
|-- changeset/
|   +-- builders.py               (build_api_write_change / build_api_edit_change
|                                  callable from command-exec-server)
|   +-- prepared.py
|   +-- types.py
|-- content/
|   +-- gitignore_oracle.py       (consumed by OCC during prepare)
|   +-- layer_backed_content.py

backend/src/sandbox/api/tool/
|-- write.py                      (host-side wrapper, calls command-exec)
|-- edit.py                       (host-side wrapper, calls command-exec)
|-- read.py                       (host-side wrapper, calls command-exec)
|-- result_projection.py

backend/tests/unit_test/test_sandbox/test_command_exec/
+-- test_write_edit_dispatch.py   (path classification + branch coverage)
+-- test_out_of_workspace_passthrough.py
+-- test_edit_snapshot_byte_derivation.py

backend/tests/unit_test/test_sandbox/test_occ/
+-- test_mutation_gate.py         (apply_changeset only; no api.* tests here)
+-- test_shell_capture_atomicity.py
```

## 4. Workflow Demonstration

### 4.1 Visual diagrams

Write (`api.write_file(path, content)`):

```text
                       host call: write_file(path, content)
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │   command-exec-server                │
                    │   (write_edit_handlers.py)           │
                    │                                      │
                    │   read workspace_root                │
                    │   classify path (realpath + ..       │
                    │     rejection, see §1 predicate)     │
                    └───────────┬──────────────────────────┘
                                │
              in-workspace ◄────┴────► out-of-workspace
                    │                          │
                    ▼                          ▼
   ┌─────────────────────────────┐    ┌──────────────────────────┐
   │  A. lease snapshot N        │    │  B. direct host-FS write │
   │     (shared LeaseRegistry)  │    │     Path(p).parent.mkdir │
   │  normalize → /testbed/...   │    │     Path(p).write_text(c)│
   │  build WriteChange(         │    │  return changed_paths=[p]│
   │     path, content,          │    │  no manifest, no base_hash│
   │     create_only)            │    │  no OCC, no layer-stack  │
   │                             │    └──────────────────────────┘
   │  OCCClient.apply_changeset( │           (matches shell
   │     [WriteChange],          │      `echo > /tmp/foo` semantics)
   │     snapshot=N)             │
   │           │                 │
   │           ▼                 │
   │  ┌──────────────────────┐   │
   │  │ occ-server           │   │
   │  │ (occ_handlers.py)    │   │
   │  │ apply_changeset:     │   │
   │  │  • gitignore route   │   │
   │  │  • base_hash infer   │   │
   │  │  • revalidate vs M   │   │
   │  │  • stage bytes       │   │
   │  │  • compare_publish_  │   │
   │  │      layer (CAS)     │   │
   │  │  • retry ≤ 3         │   │
   │  └──────────┬───────────┘   │
   │             │               │
   │             ▼               │
   │     layer-stack-server      │
   │     (storage publisher)     │
   │             │               │
   │             ▼               │
   │   ChangesetResult           │
   │             │               │
   │  release lease              │
   └─────────────┬───────────────┘
                 │
                 ▼
        return changed_paths
        or conflict (retries
        exhausted / hard CAS
        mismatch)
```

Edit (`api.edit_file(path, edits)`):

```text
                       host call: edit_file(path, edits)
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │   command-exec-server                │
                    │   (write_edit_handlers.py)           │
                    │                                      │
                    │   read workspace_root                │
                    │   classify path (same predicate as   │
                    │     write/read, single source)       │
                    └───────────┬──────────────────────────┘
                                │
              in-workspace ◄────┴────► out-of-workspace
                    │                          │
                    ▼                          ▼
   ┌─────────────────────────────────┐  ┌─────────────────────────────┐
   │  A. lease snapshot N            │  │  B. direct host-FS edit     │
   │     (shared LeaseRegistry)      │  │                             │
   │                                 │  │  bytes = host FS read       │
   │  bytes = SnapshotReader         │  │  require exists + UTF-8     │
   │     .read_bytes(path, N)        │  │  validate anchors +         │
   │  require exists + UTF-8         │  │     expected_occurrences    │
   │  validate anchors +             │  │     against on-disk bytes   │
   │     expected_occurrences        │  │  derive final bytes         │
   │     against snapshot N bytes    │  │  Path(p).write_text(final)  │
   │  derive final bytes             │  │  return changed_paths=[p]   │
   │                                 │  │  no manifest, no base_hash  │
   │  build EditChange(              │  │  no OCC                     │
   │     path, final_bytes)          │  └─────────────────────────────┘
   │                                 │
   │  OCCClient.apply_changeset(     │
   │     [EditChange], snapshot=N)   │
   │           │                     │
   │           ▼                     │
   │  ┌─────────────────────────┐    │
   │  │ occ-server              │    │
   │  │  • gitignore route      │    │
   │  │  • base_hash infer      │    │
   │  │  • re-read manifest M   │    │
   │  │  • if M ≠ N and base    │    │
   │  │    mismatch → HARD      │    │
   │  │    CONFLICT             │    │
   │  │    (do NOT re-derive    │    │
   │  │     bytes against M)    │    │
   │  │  • stage final bytes    │    │
   │  │  • compare_publish_     │    │
   │  │      layer (CAS)        │    │
   │  │  • retry ≤ 3 on CAS     │    │
   │  │    mismatch only        │    │
   │  └──────────┬──────────────┘    │
   │             ▼                   │
   │     layer-stack-server          │
   │             │                   │
   │             ▼                   │
   │  ChangesetResult                │
   │  release lease                  │
   └─────────────┬───────────────────┘
                 │
                 ▼
       changed_paths or conflict
```

Three callers, one gate:

```text
                  ┌────────────────────────────────────────┐
                  │  command-exec-server                   │
                  │                                        │
                  │  api.shell ──► capture_to_changeset ─┐ │
                  │  api.write_file (in-workspace)      ─┼─┼─► OCCClient.apply_changeset
                  │  api.edit_file  (in-workspace)      ─┘ │           │
                  │                                        │           ▼
                  │  api.read_file  (in-workspace)         │      occ-server
                  │     └─► SnapshotReader.read_bytes      │           │
                  │                                        │           ▼
                  │  api.write_file (out-of-workspace)     │    layer-stack-server
                  │  api.edit_file  (out-of-workspace)     │      (CAS publish)
                  │  api.read_file  (out-of-workspace)     │
                  │     └─► host-FS direct (no OCC)        │
                  └────────────────────────────────────────┘
```

### 4.2 Write step-by-step

Write:

```text
host write_file(path, content)     # single-path contract -> single branch
  -> command-exec-server api.write_file
  -> read workspace binding (workspace_root)
  -> classify path (see classifier predicate in section 1):
       in-workspace      -> A. OCC mutation gate (below)
       out-of-workspace  -> B. direct host-FS write (below)

A. in-workspace write (still in command-exec-server)
  -> acquire short-lived snapshot lease (manifest N)
  -> normalize path inside /testbed
  -> build WriteChange(path, content, create_only)
  -> OCCClient.apply_changeset([WriteChange], snapshot=N)
       (occ-server internals: gitignore route, base_hash infer,
        revalidate, stage, compare_publish_layer with retry)
  -> release lease
  -> return changed_paths or conflict

B. out-of-workspace write (command-exec-server, no OCC)
  -> Path(path).parent.mkdir(parents=True, exist_ok=True)
  -> Path(path).write_text(content) (or write_bytes)
  -> return changed_paths=[path], no manifest version, no base_hash
  -> matches shell semantics: shell `echo > /tmp/foo` already persists on
     the host sandbox FS unchanged
```

### 4.3 Edit step-by-step

Edit:

```text
host edit_file(path, edits)        # `edits` is a list of search/replace ops
                                   # against a single path; single-path
                                   # contract -> single branch -> single
                                   # base_hash -> single apply_changeset call
  -> command-exec-server api.edit_file
  -> read workspace binding (workspace_root)
  -> classify path (see classifier predicate in section 1):
       in-workspace      -> A. OCC mutation gate (below)
       out-of-workspace  -> B. direct host-FS edit (below)

A. in-workspace edit (still in command-exec-server)
  -> acquire short-lived snapshot lease (manifest N)
  -> SnapshotReader.read_bytes(path, manifest=N)  (port call, not OCC)
  -> require file exists and is UTF-8 text
  -> validate search anchors and expected_occurrences against snapshot N bytes
  -> derive final bytes (search/replace applied)
  -> build EditChange(path, final_bytes)
  -> OCCClient.apply_changeset([EditChange], snapshot=N)
       (occ-server internals: gitignore route, base_hash infer,
        revalidate, stage, compare_publish_layer with retry)
  -> release lease
  -> return changed_paths or conflict

B. out-of-workspace edit (command-exec-server, no OCC)
  -> read host-FS bytes from path
  -> require file exists and is UTF-8 text
  -> validate search anchors and expected_occurrences against host bytes
  -> derive final bytes
  -> Path(path).write_text(final)
  -> return changed_paths=[path], no manifest version, no base_hash
```

### 4.4 Read step-by-step

Read:

```text
host read_file(path)               # read also lives on command-exec because
                                   # it shares the SnapshotReader port + the
                                   # in/out-of-workspace classifier with edit
  -> command-exec-server api.read_file
  -> classify path (same predicate as write/edit):
       in-workspace:
         lease snapshot N
         SnapshotReader.read_bytes(path, manifest=N)
         release lease
         return bytes
       out-of-workspace:
         Path(path).read_bytes() / read_text()
         return bytes
  read never invokes OCCClient.apply_changeset (read is not a mutation)
```

### 4.5 OCC publish gate (occ-server internal)

OCC publish gate (occ-server internals, invoked via OCCClient.apply_changeset
for in-workspace write/edit and shell capture):

```text
attempt = 0
loop:
  re-read latest active manifest M (M >= N)
  route changes via SnapshotGitignoreOracle (DROP / SKIPPED / GATED / REJECT)
  for GATED rows: base_hash = infer_manifest_base_hash(layer_stack, M, path)
  revalidate base_hash + create/overwrite policy under publish RLock
    (edit: if M != N, treat base_hash mismatch as a hard conflict; do
     NOT silently re-derive bytes from M — anchors were validated under N
     by command-exec, and re-deriving would publish a write the caller
     never validated)
  allocate layer-stack staging
  write staged payload (write: caller bytes; edit: derived bytes)
  compare_publish_layer(expected_manifest=M)
    success -> return ChangesetResult(changed_paths)
    CAS mismatch and attempt < MAX_OCC_CAS_RETRIES (default 3):
      attempt += 1
      drop staging, refresh latest manifest, retry
    CAS mismatch and retries exhausted: return ChangesetResult(conflict)
```

### 4.6 Shell capture (unchanged, kept on command-exec)

Shell capture (already on command-exec-server):

```text
command-exec api.shell -> _execute_shell
  -> lease snapshot N + materialize lowerdir
  -> mount overlay + unshare -Urm + exec argv + capture upperdir
  -> capture_to_changeset (OverlayPathChange -> Change)
  -> OCCClient.apply_changeset(changes, snapshot=N)
       (same OCC publish gate as in-workspace write/edit)
  -> tracked conflict rejects the whole shell layer
  -> accepted layer publishes through layer-stack CAS
  -> release lease, drop transient lowerdir
```

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `command-exec-server` | Single host-facing API surface. Hosts `api.shell`, `api.write_file`, `api.edit_file`, `api.read_file`. Owns path classification, snapshot leases, edit byte derivation, and the in-workspace/out-of-workspace dispatch. Calls into OCC via `OCCClient.apply_changeset` for in-workspace mutations; does direct host-FS ops for out-of-workspace; does SnapshotReader-or-host-FS for read. |
| `occ-server` | Internal mutation gate consumed via `OCCClient.apply_changeset`. Owns gitignore routing, base-hash inference, conflict handling, staging, and CAS retry. **No host-callable write/edit/read endpoints.** External surface is `apply_changeset` + lifecycle (`start`, `stop`, `health`) only. |
| `OCCClient` | Internal RPC boundary between command-exec-server and occ-server. Used by both write/edit dispatch (in-workspace branch) and shell capture. |
| `SnapshotGitignoreOracle` | Gitignore policy reads layer-stack snapshots but belongs to OCC; consulted during `apply_changeset` prepare. |
| `SnapshotReader` (port) | Used by command-exec-server to read bytes for edit byte derivation, by OCC for base-hash inference, and by the read_file handler. Same port, three consumers. |
| `PreparedChangeset` | Separates mutation intent from validated, publishable changes (occ-server internal). |
| `ChangesetResult` | Shared result shape for API and shell mutations. |
| no host-side precheck | The request is an intent; OCC must validate inside the mutation gate. |
| short-lived snapshot lease | command-exec-server holds the lease for in-workspace write/edit covering prepare->publish so layer-stack GC cannot remove layers backing `SnapshotReader.read_bytes` or `infer_manifest_base_hash`. Shell already holds its own lease around mount+exec+capture. **Single shared `LeaseRegistry`** for shell, write, edit, and read; the lease key is per-call but the pin set is unified, so GC sees one source of truth across all four flows. |
| in-workspace classifier predicate | Lives in command-exec only. Definition: `realpath(path) == workspace_root or starts_with(workspace_root + "/")`; symlinks resolve before classification; unresolved `..` segments after realpath are a hard error, not a silent fallthrough. Single source of truth — OCC never re-classifies. |
| edit no-resync on CAS bump | Search anchors are validated against snapshot N (in command-exec). If the active manifest moves to M>N before publish, OCC surfaces a conflict instead of re-running search/replace against M, because anchors may have moved or duplicated. |
| `MAX_OCC_CAS_RETRIES` | Bounded CAS-mismatch retry budget (default 3). Prevents pathological loops under write contention; final attempt's mismatch becomes a conflict result. |
| in-workspace vs out-of-workspace fork (command-exec) | OCC's only concern is the workspace (`workspace_root`, default `/testbed`). Paths outside fall through to direct host-FS ops in command-exec-server, mirroring shell's namespace passthrough — `unshare -Urm` only overlay-mounts `/testbed`, so `/tmp`, `/home`, `/etc` writes from shell already hit the host sandbox FS unchanged. write/edit adopt the same split, in the same server, for consistency. |
| no OCC for out-of-workspace | Out-of-workspace ops have no manifest version, no base_hash, no staging, and no CAS — there is no layer-stack snapshot to validate against. Concurrent writers race on the host FS, the same hazard shell already accepts. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_command_exec -q
uv run pytest backend/tests/unit_test/test_sandbox/test_occ -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_write.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_edit.py -q
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_codegen_race.py -q
```

Required assertions:

Server topology:
- `api.write_file`, `api.edit_file`, and `api.read_file` are hosted on
  `command-exec-server`, not `occ-server`; importing or calling them from
  occ-server fails
- `runtime/api_handlers.py` no longer exports `write_file` / `edit_file` /
  `read_file` symbols (they migrated to `runtime/write_edit_handlers.py`);
  the file is either reduced to `layer_metrics` + service-cache helpers or
  deleted outright
- `occ-server`'s externally reachable surface is exactly
  `OCCClient.apply_changeset` plus lifecycle (`start`, `stop`, `health`);
  no `api.write_*` / `api.edit_*` / `api.read_*` symbols exist on occ-server
  (assertion: enumerate occ-server's registered wire methods and assert the
  set equals `{apply_changeset, start, stop, health}`)
- in-workspace write/edit and shell-capture all reach OCC via the same
  `OCCClient.apply_changeset` call site
- shell, write, edit, and read all acquire leases from the same
  `LeaseRegistry` instance; layer-stack GC sees one unified pin set
- in-workspace classifier predicate lives in command-exec only; greping
  occ-server source for `workspace_root` returns no classification call sites
- api.write_file and api.edit_file each carry exactly one path; a request
  carrying a list of paths or a mix of in/out-of-workspace targets is
  rejected at validation time (single-path contract)

OCC mutation gate (in-workspace only):
- concurrent in-workspace writes to the same tracked path produce
  deterministic conflict behavior
- create-only in-workspace write rejects if the path exists in the
  validation snapshot
- in-workspace edit validates target existence, UTF-8 text, anchors, and
  occurrence counts (validation done in command-exec against snapshot N)
- in-workspace edit against snapshot N where the active manifest moves to
  M>N before publish surfaces a conflict (OCC does NOT silently re-derive
  bytes against M)
- CAS mismatch retries are bounded by `MAX_OCC_CAS_RETRIES`; retry-exhaustion
  returns a conflict result and does not loop indefinitely
- in-workspace write and edit acquire and release a snapshot lease covering
  prepare->publish; layer-stack cannot GC layers referenced by an in-flight
  write/edit
- write/edit to `/testbed/...` and `<repo-relative>` resolve to the same
  in-workspace OCC path

Out-of-workspace passthrough (command-exec, no OCC):
- out-of-workspace write to `/tmp/foo` succeeds, lands on host sandbox FS,
  bypasses OCC and layer-stack entirely (no manifest version bumped, no
  layer published, OCCClient not invoked)
- out-of-workspace edit on a host-FS file applies search/replace against the
  on-disk bytes (not a snapshot) and writes back through the host FS
- out-of-workspace read returns on-disk bytes via `Path(path).read_bytes()`;
  SnapshotReader and the lease registry are not touched
- shell `echo hi > /tmp/foo` followed by `write_file("/tmp/foo", "hi2")` and
  `read_file("/tmp/foo")` from shell observe the same final byte sequence
  (consistency with shell namespace-passthrough)
- out-of-workspace path classification rejects no requests with
  `WorkspaceBindingError`; the prior exception path is replaced by the
  direct-FS branch

Classifier predicate (command-exec, write/edit/read share one impl):
- `<workspace_root>/foo` and the equivalent repo-relative `foo` classify
  in-workspace and resolve to the same OCC path
- a symlink at `<workspace_root>/link -> /tmp/foo` classifies
  out-of-workspace because realpath escapes `workspace_root`; the request
  goes to the direct-FS branch, OCC is not invoked
- a symlink at `<workspace_root>/link -> <workspace_root>/inner/foo`
  classifies in-workspace and routes through OCC
- `<workspace_root>/../etc/passwd` is rejected at classification time (hard
  error after realpath); does not silently fall through to the direct-FS
  branch
- write/edit/read share the same classifier call site; greping for the
  predicate finds exactly one definition

Shell capture (unchanged, kept on command-exec):
- shell tracked conflict publishes no partial shell layer
- `.git` and gitignored routing decisions are in OCC only (not duplicated
  in command-exec or layer-stack)
- `capture_to_changeset` calls `OCCClient`, not `OccService` directly
