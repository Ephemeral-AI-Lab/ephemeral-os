# Proposal: Caller-Owned Workspace Lifecycle And LayerStack Publish Boundary

Date: 2026-06-17
Status: Draft
Scope: `crates/daemon/workspace`, `crates/daemon/core`, `crates/daemon/operation`, `crates/daemon/layerstack`, `crates/daemon/plugin`

## Summary

The workspace model should be unified around caller-owned lifecycle, not hidden
workspace kinds or publish modes.

Final responsibility split:

```text
workspace:
  lifecycle + mounted workspace capture orchestration

operation:
  command/file operation flow

layerstack:
  publish/OCC mechanics for normalized LayerChange sets
```

Workspace is not a storage engine. It creates mounted workspaces, captures their
upperdir changes, and destroys them. Operation code decides which workflow is
being executed. LayerStack owns the normalized change format and the single
publish/OCC boundary for already-computed `LayerChange` sets.

Do not add `WorkspaceKind`, `BaseSelector`, or a latest-snapshot workspace mode.
Workspace creation always selects the latest LayerStack head at acquisition
time, records that snapshot as observed metadata, and creates a real workspace
lease, overlay mount, and namespace. The only creation difference is network
configuration: `NetworkMode::Host` or `NetworkMode::Isolated`.

Readonly access to the latest state should remain a separate query, for example
`get_workspace_latest_snapshot`. It is not a workspace mode and not a command
execution handle.

## Design Decisions

1. Workspace creation always acquires the latest LayerStack snapshot/lease.
2. Workspace creation always creates overlay dirs, mounts the workspace view,
   and creates a holder namespace.
3. `NetworkMode::Host` skips only dedicated network namespace setup.
4. `NetworkMode::Isolated` adds dedicated network namespace setup, DNS, veth,
   and netfilter policy.
5. Workspace lifecycle is caller-owned: the caller explicitly creates, runs,
   captures, publishes if desired, and destroys.
6. One-shot Host command execution is an operation workflow, not a separate
   daemon workspace kind.
7. Workspace owns the public `capture_changes(handle)` workflow for mounted
   upperdirs.
8. Workspace must not publish, apply changes, or own OCC policy.
9. LayerStack exposes one publish entrypoint for snapshot-scoped `LayerChange`
   sets.
10. Operation code owns command/file workflows and converts targeted file
    requests into `LayerChange` before publishing.

## Drop List

Drop these concepts from the workspace/storage design:

```text
WorkspaceKind
WorkspaceMode
BaseSelector
read_only workspace flag
writable workspace flag
mutation workspace flag
mutates_state workspace flag
WorkspacePublishPolicy
PublishMode
workspace.publish_changes(...)
workspace.apply_changeset(...)
workspace.write_file_latest(...)
workspace.edit_file_latest(...)
layerstack.capture_upperdir_against_snapshot(...)
layerstack.apply_changeset_against_snapshot(...)
separate LayerStack publish APIs for command capture vs file write/edit
```

Also drop full snapshot handles from the publish API. Publish needs base
identity for OCC/routing, not a workspace handle or a mounted snapshot object.

Keep these concepts:

```text
NetworkMode::Host
NetworkMode::Isolated
workspace.create(...)
workspace.capture_changes(...)
workspace.destroy(...)
get_workspace_latest_snapshot(...)
layerstack.publish_to_layer_stack(...)
```

Readonly becomes API-shaped, not flag-shaped:

```text
get_workspace_latest_snapshot(...) -> readonly by construction
```

Mutation becomes operation-shaped, not flag-shaped:

```text
operation::file write/edit -> LayerChange -> layerstack.publish_to_layer_stack
operation::command -> workspace capture -> LayerChange -> layerstack.publish_to_layer_stack
```

External command metadata such as intent or `mutates_state` may still exist for
admission, audit, tracing, or UI. It must not define workspace mode, workspace
lifecycle, or LayerStack publish behavior.

## Public Model

The workspace model stays lifecycle-oriented:

```rust
pub enum NetworkMode {
    Host,
    Isolated,
}

pub struct WorkspaceHandle {
    pub id: WorkspaceId,
    pub owner: CallerId,
    pub workspace_root: PathBuf,
    pub network: NetworkMode,
    pub snapshot: LayerStackSnapshotRef,
}
```

`snapshot` is not a selector supplied by the caller. It is the latest LayerStack
state observed when the workspace was created. If the LayerStack head advances
later, the workspace becomes stale relative to the newest head but remains valid
for capture and OCC publish against its recorded snapshot.

Workspace capture returns normalized LayerStack changes plus a report:

```rust
pub struct CapturedWorkspaceChanges {
    pub snapshot: LayerStackSnapshotRef,
    pub changes: Vec<LayerChange>,
    pub capture_report: CaptureReport,
}
```

`CaptureReport` is produced by the workspace capture flow. It can include
unsupported special files, invalid paths, dropped paths, source-conflict
metadata, byte/file stats, and other capture diagnostics. Capture drops belong
in this report, not in publish policy.

LayerStack publish input should be minimal and shared by captured workspace
changes and targeted file mutations:

```rust
pub struct LayerStackPublishBase {
    pub manifest_version: i64,
    pub root_hash: String,
}

pub struct PublishLayerChangesRequest {
    pub base: LayerStackPublishBase,
    pub changes: Vec<LayerChange>,
}
```

There is no `WorkspacePublishPolicy`, no `PublishMode`, and no publish policy
enum on the workspace boundary. If command-specific Git/OCC behavior is still
needed during migration, it should be encoded inside `publish_to_layer_stack`,
not surfaced as a workspace policy, publish mode, or second LayerStack publish
API.

## Workspace API Shape

Workspace should expose lifecycle primitives and readonly latest-snapshot
projection:

```rust
fn create_workspace(
    request: CreateWorkspaceRequest,
) -> Result<WorkspaceHandle, WorkspaceError>;

fn capture_changes(
    handle: &WorkspaceHandle,
    request: CaptureChangesRequest,
) -> Result<CapturedWorkspaceChanges, WorkspaceError>;

fn destroy_workspace(
    handle: WorkspaceHandle,
    request: DestroyWorkspaceRequest,
) -> Result<DestroyWorkspaceResult, WorkspaceError>;

fn get_workspace_latest_snapshot(
    workspace_root: PathBuf,
) -> Result<LatestSnapshotHandle, WorkspaceError>;
```

Workspace capture owns the upperdir path and returns `CapturedWorkspaceChanges`.
There should not be a separate operation-facing LayerStack API such as
`capture_upperdir_against_snapshot(snapshot, upperdir, options)`.

Workspace must not expose:

```text
workspace.publish_changes(...)
workspace.apply_changeset(...)
workspace.write_file_latest(...)
workspace.edit_file_latest(...)
```

Those APIs would turn workspace into a storage mutation boundary, which is the
wrong ownership direction.

## LayerStack API Shape

LayerStack should expose one operation-facing mutation primitive. Both command
captures and targeted file write/edit operations should converge here after
they have produced `LayerChange` values:

```rust
fn publish_to_layer_stack(
    request: PublishLayerChangesRequest,
) -> Result<PublishLayerChangesResult, LayerStackError>;
```

This avoids splitting captured workspace publish from targeted file apply. At
the LayerStack boundary, the input is just the publish base and the normalized
changes. Workspace capture and file operation edit/write logic are responsible
for converting their domain inputs into `LayerChange` before calling this
function.

## Operation Workflows

Operation code is the orchestrator. It decides whether a request needs a mounted
workspace or can be handled as a targeted file mutation.

### Command Session Shell

Command execution over a workspace should be explicit:

```text
a. workspace.create(...) -> WorkspaceHandle
b. operation::command runs command over WorkspaceHandle
c. workspace.capture_changes(handle) -> CapturedWorkspaceChanges
d. layerstack.publish_to_layer_stack(captured.snapshot.publish_base(), captured.changes)
e. workspace.destroy(handle)
```

Code-shaped workflow:

```rust
let handle = workspace.create(request)?;
let command = operation::command::run_in_workspace(&handle, command_request)?;
let captured = workspace.capture_changes(&handle, capture_request)?;
let published = layerstack::publish_to_layer_stack(PublishLayerChangesRequest {
    base: captured.snapshot.publish_base(),
    changes: captured.changes,
})?;
let destroyed = workspace.destroy(handle)?;
```

Destroy must not implicitly publish. If command execution fails before publish,
operation code decides whether to capture diagnostics, discard the workspace, or
return a terminal dropped result.

### Targeted File Write/Edit

`write_file` and `edit_file` should not need to create a workspace when the
operation is a targeted file mutation.

Implementation should live in `operation::file`:

```text
operation::file::write_file
  acquire latest LayerStack snapshot
  read target file from that snapshot
  validate create/overwrite constraints
  compute LayerChange::Write
  call layerstack.publish_to_layer_stack(snapshot.publish_base(), changes)

operation::file::edit_file
  acquire latest LayerStack snapshot
  read target file from that snapshot
  apply search/replace or edit semantics in memory
  compute LayerChange::Write
  call layerstack.publish_to_layer_stack(snapshot.publish_base(), changes)
```

A helper such as `apply_targeted_file_mutation` can exist inside
`operation::file`, but it must not be a workspace method. Workspace cannot
publish or apply changes.

## Latest Snapshot Query Workflow

Readonly consumers should not create a namespace:

```text
snapshot = get_workspace_latest_snapshot(workspace_root)
read or analyze snapshot.view_root
drop snapshot handle
```

For Pyright LSP:

```text
snapshot = get_workspace_latest_snapshot(workspace_root)
plugin receives snapshot.view_root and snapshot.generation_key
plugin restarts or reopens files when generation_key changes
caller refreshes by requesting a new latest snapshot
```

The plugin crate should own only the Pyright process and LSP protocol. It should
not publish, apply changes, or decide LayerStack OCC behavior.

## Dependency Rule

Enforce the boundary with crate dependencies:

```text
layerstack -> workspace forbidden
layerstack -> operation forbidden
workspace -> layerstack allowed for lease and mounted view setup
operation -> workspace allowed for mounted command lifecycle
operation -> layerstack allowed for publish orchestration
plugin -> layerstack discouraged; prefer latest snapshot handles from operation/core
```

The important direction is ownership:

- Workspace may depend on LayerStack to acquire snapshots and mount views.
- Operation may depend on both workspace and LayerStack to orchestrate flows.
- LayerStack must not know about workspace handles, command sessions, plugins,
  or network modes.
- Plugin should consume a prepared readonly view and generation key.

## Staleness Model

Created workspaces and readonly latest snapshots both start from the latest head
visible at acquisition time.

After acquisition:

- The LayerStack head may advance.
- The workspace remains valid but can be stale.
- Readonly snapshot handles are generation-scoped.
- Writable workspace changes publish through LayerStack OCC.
- Targeted file changes publish against the snapshot acquired by that operation.
- Staleness is detected by LayerStack publish validation, not by mutating
  an open workspace base in place.

Do not remount or rebase an open writable workspace automatically when the
LayerStack head advances. Automatic rebase would change the caller's execution
environment while commands or edits are in progress.

## Migration Plan

### Phase A: Contract Clarification

- Document that workspace creation always means latest lease plus namespace.
- Document that `NetworkMode` only controls network setup.
- Document `get_workspace_latest_snapshot` as a readonly query, not a mode.
- Remove `WorkspaceKind`, `BaseSelector`, `WorkspacePublishPolicy`, and
  `PublishMode` from the proposed model.
- Remove workspace-level read/write/mutation booleans from the proposed model.

### Phase B: LayerStack Publish Primitive

- Introduce or formalize `CaptureReport`.
- Make workspace capture return `CapturedWorkspaceChanges`.
- Add or formalize `LayerStackPublishBase`.
- Add or formalize `PublishLayerChangesRequest { base, changes }`.
- Add or formalize `publish_to_layer_stack(request)`.
- Route captured workspace changes and targeted file mutations through that
  single publish primitive.
- Ensure route decisions and OCC validation are snapshot-scoped.

### Phase C: Workspace Lifecycle Cleanup

- Keep workspace APIs focused on create, capture, destroy, and latest readonly
  snapshot projection.
- Remove workspace publish/apply concepts from the proposal and any migration
  targets.
- Ensure Host and Isolated differ only by network setup and caller lifecycle.

### Phase D: Operation Workflow Migration

- Move one-shot Host command semantics into `operation::command` orchestration.
- Let command operation explicitly create, run, capture, publish, and destroy.
- Keep file edit/write semantics in `operation::file`.
- Replace direct file fast paths with snapshot-scoped targeted mutation:
  acquire latest snapshot, compute `LayerChange`, publish through LayerStack
  OCC.

### Phase E: Plugin Latest Snapshot Refresh

- Keep Pyright process/LSP behavior in `plugin`.
- Provide plugin code a readonly snapshot view root and generation key.
- Move snapshot acquisition/refresh orchestration out of plugin protocol code.

### Phase F: Dependency Enforcement

- Add contract checks that prevent LayerStack from depending on workspace,
  operation, plugin, or command concepts.
- Add contract checks that prevent workspace from exposing publish/apply/file
  mutation APIs.
- Keep operation as the only layer that composes workspace lifecycle with
  LayerStack publish.

## Open Questions

- Should `CapturedWorkspaceChanges.changes` remain `Vec<LayerChange>` publicly,
  or should workspace expose a named wrapper around `changes` plus
  `capture_report`?
- Should operation call LayerStack directly, or should core inject a narrow
  storage mutation port that is implemented by LayerStack?
- What is the compatibility story for legacy `sandbox.file.write` and
  `sandbox.file.edit` requests that currently pass only `layer_stack_root`?

## Non-Goals

- Do not add `WorkspaceKind`.
- Do not add a public base selector.
- Do not make latest snapshot a workspace mode or network mode.
- Do not add workspace-level `read_only`, `writable`, `mutation`, or
  `mutates_state` flags.
- Do not add `WorkspacePublishPolicy`.
- Do not add `PublishMode`.
- Do not put publish/apply on workspace.
- Do not add `write_file_latest` or `edit_file_latest` to workspace.
- Do not force targeted file write/edit through mounted workspace creation.
- Do not make destroy publish implicitly.
