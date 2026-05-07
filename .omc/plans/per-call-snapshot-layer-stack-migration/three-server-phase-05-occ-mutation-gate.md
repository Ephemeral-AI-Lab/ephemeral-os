# Phase 05 - OCC Mutation Gate

**Status:** draft implementation plan
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

## 1. Task Specification

Route `write_file`, `edit_file`, and command-exec shell capture through one OCC
mutation gate. `occ-server` owns mutation policy, base-hash computation,
gitignore routing, conflict handling, staging, and CAS retry. `layer-stack-server`
remains the policy-blind storage publisher.

Implementation scope:

```text
route write_file directly to occ-server
route edit_file directly to occ-server
accept shell capture only through occ.client.OCCClient.apply_changeset
acquire short-lived snapshot lease for write_file and edit_file covering prepare->publish
prepare typed changes against the leased layer-stack snapshot
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
no host-side layer-stack precheck for write/edit
no direct capture-to-OccService call
```

Exit condition:

```text
all workspace mutations publish through occ-server, shell tracked conflicts
publish no partial shell layer, and layer-stack remains policy-blind.
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

Target additions and updates:

```text
backend/src/sandbox/runtime/
+-- occ_server.py
+-- occ_handlers.py

backend/src/sandbox/occ/
|-- client.py
+-- mutation_coordinator.py
+-- workspace_capture.py
|-- service.py
|-- commit_transaction.py
|-- changeset/
|   +-- builders.py
|   +-- prepared.py
|   +-- types.py
|-- content/
|   +-- gitignore_oracle.py
|   +-- layer_backed_content.py

backend/src/sandbox/api/tool/
|-- write.py
|-- edit.py
|-- result_projection.py

backend/tests/unit_test/test_sandbox/test_occ/
+-- test_mutation_gate.py
+-- test_write_edit_routes.py
+-- test_shell_capture_atomicity.py
```

## 4. Workflow Demonstration

Write:

```text
host write_file("src/a.py", content)
  -> occ-server api.write_file
  -> acquire short-lived snapshot lease (manifest N)
  -> read workspace binding and active manifest N through layer-stack protocols
  -> normalize path inside /testbed
  -> classify with SnapshotGitignoreOracle
  -> for tracked paths: base_hash = infer_manifest_base_hash(layer_stack, N, path)
  -> prepare WriteChange (no file read; bytes provided by caller)
  -> enter shared OCC publish gate (see below)
  -> release lease
  -> return changed_paths or conflict
```

Edit:

```text
host edit_file("src/a.py", edits)
  -> occ-server api.edit_file
  -> acquire short-lived snapshot lease (manifest N)
  -> read target bytes from layer-stack snapshot N (SnapshotReader.read_bytes)
  -> require file exists and is UTF-8 text
  -> validate search anchors and expected_occurrences against snapshot N bytes
  -> derive final bytes (search/replace applied)
  -> base_hash = infer_manifest_base_hash(layer_stack, N, path)
  -> prepare EditChange with derived final bytes
  -> enter shared OCC publish gate (see below)
  -> release lease
  -> return changed_paths or conflict
```

Shared OCC publish gate (write and edit):

```text
attempt = 0
loop:
  re-read latest active manifest M (M >= N)
  revalidate base_hash + create/overwrite policy under publish RLock
    (edit: if M != N, treat base_hash mismatch as a hard conflict; do
     NOT silently re-derive bytes from M — anchors validated under N
     may not match M, and re-deriving would publish a write the caller
     never validated)
  allocate layer-stack staging
  write staged payload (write: caller bytes; edit: derived bytes)
  compare_publish_layer(expected_manifest=M)
    success -> return changed_paths
    CAS mismatch and attempt < MAX_OCC_CAS_RETRIES (default 3):
      attempt += 1
      drop staging, refresh latest manifest, retry
    CAS mismatch and retries exhausted: return conflict
```

Shell capture:

```text
command-exec capture upperdir for manifest N
  -> capture_to_changeset
  -> occ.client.OCCClient.apply_changeset(changes, snapshot=N)
  -> occ-server prepares against leased snapshot
  -> revalidate against latest active manifest
  -> tracked conflict rejects the whole shell layer
  -> accepted layer publishes through layer-stack CAS
```

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `OCCClient` | Public mutation boundary used by write/edit and shell capture. |
| `occ-server` | Owns mutation policy and commit orchestration. |
| `SnapshotGitignoreOracle` | Gitignore policy reads layer-stack snapshots but belongs to OCC. |
| `PreparedChangeset` | Separates mutation intent from validated, publishable changes. |
| `ChangesetResult` | Shared result shape for API and shell mutations. |
| no host-side precheck | The request is an intent; OCC must validate inside the mutation gate. |
| short-lived snapshot lease | write/edit lease N for the prepare->publish window so layer-stack GC cannot remove layers backing `read_bytes`/`infer_manifest_base_hash` mid-gate. Shell-capture already holds its own lease via command-exec. |
| edit no-resync on CAS bump | Search anchors are validated against snapshot N. If the active manifest moves to M>N before publish, we surface a conflict instead of re-running search/replace against M, because anchors may have moved or duplicated. |
| `MAX_OCC_CAS_RETRIES` | Bounded CAS-mismatch retry budget (default 3). Prevents pathological loops under write contention; final attempt's mismatch becomes a conflict result. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_occ -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_write.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_edit.py -q
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_codegen_race.py -q
```

Required assertions:

- concurrent writes to the same tracked path produce deterministic conflict
  behavior
- create-only write rejects if the path exists in the validation snapshot
- edit validates target existence, UTF-8 text, anchors, and occurrence counts
- edit against snapshot N where the active manifest moves to M>N before
  publish surfaces a conflict (no silent re-derivation against M)
- CAS mismatch retries are bounded by `MAX_OCC_CAS_RETRIES`; retry-exhaustion
  returns a conflict result and does not loop indefinitely
- write and edit acquire and release a snapshot lease covering prepare->publish;
  layer-stack cannot GC layers referenced by an in-flight write/edit
- shell tracked conflict publishes no partial shell layer
- `.git` and gitignored routing decisions are in OCC only
- `capture_to_changeset` calls `OCCClient`, not `OccService`
