# Phase 03 - Narrow Layer-Stack and OCC Client Protocols

**Status:** draft implementation plan
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

## 1. Task Specification

Define the protocol boundaries that keep `occ-server` and
`command-exec-server` from importing concrete layer-stack storage internals or
OCC service internals. This phase is the seam before server transport becomes
real.

Implementation scope:

```text
define layer-stack role protocols consumed by OCC
define layer-stack lease/snapshot client consumed by command-exec
define OCC mutation client consumed by command-exec
move OCC internals off direct LayerStackManager dependencies
keep SnapshotGitignoreOracle inside OCC
add import-fence tests for server boundaries
```

Out of scope:

```text
no AF_UNIX socket implementation required yet
no shell mount implementation required yet
no raw exec blocking policy
```

Exit condition:

```text
OCC and command-exec code can be wired against protocol-shaped clients, and no
module below command-exec imports concrete LayerStackManager, Manifest,
MergedView, OccService, publish internals, or Git policy across boundaries.
```

## 2. Main Data Objects

```text
SnapshotReader
  get_active_manifest(workspace_ref)
  read_bytes(workspace_ref, path, manifest_version?)
  read_text(workspace_ref, path, manifest_version?)

SnapshotMaterializer
  materialize_snapshot(workspace_ref, manifest_version)

CommitStagingStore
  allocate_commit_staging(workspace_ref, request_id)
  drop_commit_staging(workspace_ref, staging_id)

CommitPublisher
  compare_publish_layer(workspace_ref, expected_manifest, staged_changes)

WorkspaceLeaseClient
  prepare_workspace_snapshot(workspace_ref, request_id, ttl_seconds)
  release_lease(workspace_ref, lease_id)

OCCMutationClient
  apply_changeset(workspace_ref, typed_changes, snapshot, options)
```

## 3. File/Folder Structure Change

Target additions and updates:

```text
backend/src/sandbox/runtime/clients/
+-- layer_stack.py
+-- occ.py

backend/src/sandbox/occ/
+-- ports.py
|-- client.py
|-- service.py
|-- commit_transaction.py
|-- content/gitignore_oracle.py

backend/src/sandbox/command_exec/
+-- clients.py

backend/tests/unit_test/test_sandbox/
+-- test_import_fence.py
```

## 4. Workflow Demonstration

```text
write_file request
  -> occ-server handler
  -> OccMutationCoordinator(
       snapshot_reader=SnapshotReader,
       staging=CommitStagingStore,
       publisher=CommitPublisher,
       gitignore=SnapshotGitignoreOracle(snapshot_reader)
     )
  -> apply changes through protocol roles
```

```text
shell capture request
  -> command-exec-server
  -> WorkspaceLeaseClient.prepare_workspace_snapshot(...)
  -> run command and capture upperdir
  -> OCCMutationClient.apply_changeset(...)
```

Forbidden dependency example:

```text
command_exec.capture.upperdir
  -> occ.client.OCCClient                    allowed
  -> sandbox.occ.service.OccService          forbidden
  -> sandbox.layer_stack.stack_manager       forbidden
```

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `SnapshotReader` | Describes the read capability OCC needs without exposing storage layout. |
| `CommitPublisher` | Names the policy-blind CAS publish primitive supplied by layer-stack. |
| `WorkspaceLeaseClient` | Names command-exec's lease/snapshot dependency without leaking the registry implementation. |
| `OCCMutationClient` / `OCCClient` | Keeps command-exec on the public OCC client boundary for shell capture. |
| `SnapshotGitignoreOracle` | Gitignore policy belongs to OCC and reads snapshot content through protocols. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_import_fence.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_occ/test_ports.py -q
uv run ruff check backend/src/sandbox
```

Required assertions:

- OCC internals type against role protocols instead of `LayerStackManager`
- command-exec imports only client/protocol modules for layer-stack and OCC
- `layer_stack` imports no OCC, command-exec, or Git/gitignore policy
- missing workspace binding causes OCC client flows to fail closed
- shell capture cannot call `OccService` directly
