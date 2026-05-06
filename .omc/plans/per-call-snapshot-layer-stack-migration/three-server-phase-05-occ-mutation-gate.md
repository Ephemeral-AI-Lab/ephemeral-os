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
prepare typed changes against a selected layer-stack snapshot
revalidate against latest active manifest before publish
stage accepted final bytes
publish through layer-stack compare_publish_layer CAS
retry prepare/revalidate on CAS mismatch
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
  -> read workspace binding and active manifest N through layer-stack protocols
  -> normalize path inside /testbed
  -> classify with SnapshotGitignoreOracle
  -> attach base hash for tracked paths
  -> prepare WriteChange
  -> re-read latest active manifest
  -> revalidate base hash / create policy
  -> allocate layer-stack staging
  -> write staged payload
  -> compare_publish_layer(expected_manifest=latest)
  -> return changed_paths or conflict
```

Edit:

```text
host edit_file("src/a.py", edits)
  -> occ-server api.edit_file
  -> read target bytes from layer-stack snapshot
  -> require file exists and is UTF-8 text
  -> validate search anchors and occurrence counts
  -> prepare final bytes
  -> shared OCC publish gate
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
- shell tracked conflict publishes no partial shell layer
- `.git` and gitignored routing decisions are in OCC only
- `capture_to_changeset` calls `OCCClient`, not `OccService`
