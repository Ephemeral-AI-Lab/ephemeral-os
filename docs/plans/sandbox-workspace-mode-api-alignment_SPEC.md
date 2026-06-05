# SPEC: Sandbox Workspace Mode API Alignment

Status: DRAFT
Date: 2026-06-05
Owner doc: `docs/plans/sandbox-workspace-mode-api-alignment_SPEC.md`
Scope: `sandbox/crates/eos-daemon`, `sandbox/crates/eos-ephemeral-workspace`,
`sandbox/crates/eos-isolated-workspace`, a small neutral workspace API contract
crate, and the typed sandbox API result parsers that consume daemon workspace
responses.

This spec defines the target shape for aligning `ephemeral_workspace` and
`isolated_workspace` APIs. The public daemon tool surface stays unified. The
mode-specific behavior moves into the two workspace crates behind symmetric
internal APIs, while each crate keeps its different overlay, OCC, LayerStack,
and lifecycle semantics.

---

## 1. Goals

1. Keep one public daemon/API surface for file and shell tools.
2. Make `eos-daemon` a router and adapter layer, not the owner of workspace-mode
   policy.
3. Move file API behavior into root-level `file_ops/` modules in both workspace
   crates.
4. Move command workspace prepare/finalize behavior into root-level
   `command_session/` modules in both workspace crates.
5. Keep daemon command-session control in `eos-daemon`: PTY, process group,
   live/completed registry, output cursors, `write_stdin`, cancel, collect,
   count, reaper, and orphan recovery.
6. Preserve the isolated no-publish guarantee at the dependency level.
7. Normalize result shapes so typed API consumers can distinguish
   `Workspace::Ephemeral` from `Workspace::Isolated`.
8. Make the intended OO-style symmetry explicit through narrow Rust capability
   traits, not a monolithic workspace base class.

## 2. Non-Goals

- No model-facing tool rename.
- No daemon wire op rename.
- No isolated workspace promotion path.
- No plugin/LSP execution inside isolated mode.
- No extraction of PTY/process/session registry ownership out of `eos-daemon`.
- No `eos-occ` dependency in `eos-isolated-workspace`.
- No `WorkspaceLifecycle` trait that forces no-op lifecycle methods onto
  ephemeral workspaces.
- No one-size-fits-all `Workspace` trait that mixes file operations, command
  sessions, lifecycle, audit, network, and publishing.

---

## 3. Target File and Folder Structure

```text
sandbox/crates/eos-workspace-api/src/
  lib.rs
  mode.rs                       # WorkspaceMode and mode metadata
  file_ops.rs                   # WorkspaceFileOps trait + file DTOs
  command_session.rs            # CommandWorkspacePolicy trait + command DTOs
  read_view.rs                  # WorkspaceReadView trait
  mutation.rs                   # WorkspaceMutationSink trait
  response.rs                   # shared outcomes/conflicts/timing DTOs

sandbox/crates/eos-daemon/src/
  workspace_ops.rs              # thin read/write/edit router
  workspace_adapters.rs         # daemon ports for LayerStack/OCC/snapshot reads
  command/
    mod.rs
    session.rs                  # registry, live/completed sessions
    output.rs                   # cursors, max_output_tokens, UTF-8 handling
    pty.rs                      # PTY pair
    lifecycle.rs                # child process/process group spawn
    control.rs                  # write_stdin, cancel, collect, count
    reaper.rs                   # timeout/orphan recovery

sandbox/crates/eos-ephemeral-workspace/src/
  ops.rs                        # EphemeralWorkspaceOps trait implementation
  file_ops/
    mod.rs
    read.rs
    write.rs
    edit.rs
    response.rs
  command_session/
    mod.rs
    prepare.rs                  # build fresh-overlay command workspace context
    finalize.rs                 # finish capture/publish/cleanup after runner exit
    types.rs
  capture.rs
  cleanup.rs
  finalize.rs
  ports.rs
  runner.rs
  types.rs

sandbox/crates/eos-isolated-workspace/src/
  ops.rs                        # IsolatedWorkspaceOps trait implementation
  file_ops/
    mod.rs
    read.rs
    write.rs
    edit.rs
    response.rs
  command_session/
    mod.rs
    prepare.rs                  # build handle/setns private command context
    finalize.rs                 # finish audit-only capture after runner exit
    types.rs
  session/
    mod.rs
    lifecycle.rs
    persistence.rs
    gc.rs
    ports.rs
    types.rs
  audit.rs
  caps.rs
  network.rs
  error.rs
```

`file_ops/` and `command_session/` live at the root of
`eos-isolated-workspace`, not under `session/`. They are workspace-mode
capabilities. `session/` owns enter/exit/TTL/persistence and exposes handle
state to those root-level capability modules.

---

## 4. OO-Style Capability Interfaces

The alignment should use Rust trait interfaces where both workspace modes really
share a callable capability. These are OO-style interfaces in the Rust sense:
small behavior contracts implemented by concrete mode structs. They are not an
inheritance hierarchy.

The shared traits and DTOs live in `eos-workspace-api` so
`eos-ephemeral-workspace` and `eos-isolated-workspace` compile against the same
contract without depending on each other.

Concrete implementations:

```rust
pub struct EphemeralWorkspaceOps<P> {
    // daemon-injected snapshot/read/publish/audit adapters as needed
    ports: P,
}

pub struct IsolatedWorkspaceOps<P> {
    // daemon-supplied handle plus snapshot/read/audit adapters as needed
    ports: P,
}
```

The daemon can dispatch through an enum wrapper or trait object. Prefer an enum
when the selected mode is known per request and no dynamic storage is needed;
use `dyn` only where the command-session registry benefits from storing one
polymorphic finalize/cleanup policy.

### 4.1 `WorkspaceFileOps`

Use this trait for direct file APIs:

```rust
pub trait WorkspaceFileOps {
    fn read_file(&self, request: ReadFileRequest) -> Result<ReadFileOutcome, WorkspaceApiError>;
    fn write_file(&self, request: WriteFileRequest) -> Result<WriteFileOutcome, WorkspaceApiError>;
    fn edit_file(&self, request: EditFileRequest) -> Result<EditFileOutcome, WorkspaceApiError>;
}
```

`EphemeralWorkspaceOps` implements it with LayerStack/OCC-backed publish
semantics. `IsolatedWorkspaceOps` implements it with upperdir-first reads,
private upperdir writes, and audit-only metadata.

### 4.2 `CommandWorkspacePolicy`

Use this trait for mode-specific command workspace policy:

```rust
pub trait CommandWorkspacePolicy: Send + Sync {
    fn prepare_command_workspace(
        &self,
        request: PrepareCommandRequest,
    ) -> Result<PreparedCommandWorkspace, WorkspaceApiError>;

    fn finalize_command_workspace(
        &self,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError>;
}
```

`prepare_command_workspace` is implemented inside each crate's
`command_session/prepare.rs`. `finalize_command_workspace` is implemented
inside each crate's `command_session/finalize.rs`.

This trait must not absorb daemon-owned command-session control. PTY allocation,
process spawning/reaping, output cursors, `write_stdin`, cancel, collect, count,
completion parking, and live-session registry ownership remain in `eos-daemon`.

### 4.3 `WorkspaceReadView`

Use this trait for the read side below file ops and command finalization:

```rust
pub trait WorkspaceReadView {
    fn resolve_path(&self, request_path: &str) -> Result<ResolvedWorkspacePath, WorkspaceApiError>;
    fn read_bytes(
        &self,
        path: &ResolvedWorkspacePath,
    ) -> Result<WorkspaceReadBytes, WorkspaceApiError>;
}
```

Ephemeral implementation reads from active LayerStack truth through daemon
adapters. Isolated implementation reads from upperdir first, then from the
pinned snapshot/merged view. This keeps read semantics explicit without making
isolated depend directly on publish-capable LayerStack/OCC internals.

### 4.4 `WorkspaceMutationSink`

Use this trait for the write/capture result sink:

```rust
pub trait WorkspaceMutationSink {
    fn commit_or_record(
        &self,
        request: WorkspaceMutationRequest,
    ) -> Result<WorkspaceMutationOutcome, WorkspaceApiError>;
}
```

Ephemeral implementation publishes captured changes through a daemon-injected
OCC publisher port. Isolated implementation records audit-only metadata and
returns `published: false`. This trait is the explicit polymorphic boundary for
"same mutation shape, different persistence semantics."

The trait must not require `eos-occ`; otherwise the isolated build-time
no-publish guarantee is lost.

### 4.5 OO Refactor Matrix

| Candidate | Plan | Why |
|---|---|---|
| `WorkspaceFileOps` | Yes | Direct file APIs are symmetric public capabilities |
| `CommandWorkspacePolicy` | Yes | Command prepare/finalize are symmetric mode-policy hooks |
| `WorkspaceReadView` | Yes | Both modes need path resolution and bytes reads with different backing stores |
| `WorkspaceMutationSink` | Yes | Both modes produce mutation outcomes with different persistence semantics |
| Response builders | DTO first, optional trait later | Shared typed outcomes reduce JSON drift before adding polymorphism |
| Audit/timing builders | Builder structs, not required trait | The DTOs differ enough that a trait may add ceremony |
| Workspace lifecycle | No | Isolated has real enter/exit/TTL; ephemeral would be fake no-op lifecycle |

---

## 5. Workspace Ops Role

`sandbox/crates/eos-daemon/src/workspace_ops.rs` becomes dispatch-only:

1. Receive `api.v1.read_file`, `api.v1.write_file`, and `api.v1.edit_file`.
2. Select mode by active isolated state for `agent_id`.
3. Build the corresponding concrete `WorkspaceFileOps` implementation:
   `IsolatedWorkspaceOps` when isolated is open, otherwise
   `EphemeralWorkspaceOps`.
4. Call `read_file`, `write_file`, or `edit_file` through the shared trait
   contract.
5. Inject daemon adapters for LayerStack, OCC publish, and snapshot reads.
6. Map lower-crate errors into `DaemonError`.

It should not contain direct `LayerStack::open`, direct `apply_occ_changeset`,
search/replace logic, isolated upperdir logic, or response builders.

---

## 6. Command Session Role

`eos-daemon` still contains command-session management:

| Concern | Owner |
|---|---|
| Public ops: `exec_command`, `write_stdin`, cancel, collect, count | `eos-daemon` |
| PTY pair and process group spawn | `eos-daemon` |
| Live/completed command-session registry | `eos-daemon` |
| Output cursors, token caps, UTF-8 handling | `eos-daemon` |
| Reaper, orphan recovery, completion parking | `eos-daemon` |
| Ephemeral command workspace prepare/finalize policy | `eos-ephemeral-workspace` |
| Isolated command workspace prepare/finalize policy | `eos-isolated-workspace` |
| Concrete OCC publish adapter | `eos-daemon` |

The workspace crates expose the shared `CommandWorkspacePolicy` mode-policy
interface. The daemon owns the long-running session control plane.

### 6.1 `command_session/prepare.rs`

`prepare.rs` builds the workspace-mode context needed before the daemon spawns a
command-session child. It is not the process/session manager.

Responsibilities common to both workspace crates:

1. Accept typed command-workspace input from the daemon adapter.
2. Resolve the workspace root and runner workspace view.
3. Allocate or select mode-specific scratch/output/final paths.
4. Build the `eos_runner::RunRequest` or equivalent runner request DTO.
5. Return a `PreparedCommandWorkspace` containing:
   - `run_request`
   - `request_path`
   - `output_path`
   - `final_path`
   - `finalize_context`
   - any cleanup/failure rollback context needed by the mode

Responsibilities specific to `eos-ephemeral-workspace`:

1. Acquire a fresh LayerStack snapshot through an injected snapshot port.
2. Allocate fresh overlay run dirs.
3. Build a `FreshNs` runner request over the snapshot layer paths and run dirs.
4. Roll back the lease and run dirs if preparation fails before the daemon takes
   ownership of the prepared context.

Responsibilities specific to `eos-isolated-workspace`:

1. Use an existing isolated `WorkspaceHandle` supplied by the daemon adapter.
2. Allocate command-session scratch paths under the handle scratch directory.
3. Build a private runner request using the handle workspace root, layer paths,
   upperdir, workdir, namespace FDs, and cgroup path.
4. Acquire no new LayerStack lease and create no publish path.

`prepare.rs` must not:

- spawn the PTY child;
- own the command-session registry;
- implement `write_stdin`, cancel, collect, or count;
- park completions for heartbeat/notification delivery;
- call OCC directly.

After `prepare.rs` returns, `eos-daemon` serializes the returned runner request
to `request_path`, opens the PTY, spawns the `ns-runner` child process group,
and registers the live command session.

### 6.2 `command_session/finalize.rs`

`finalize.rs` converts a finished command-session runner result plus a
mode-specific finalize context into a workspace command outcome. It is not the
child reaper and does not decide when a process is done.

Responsibilities common to both workspace crates:

1. Accept typed finalize input from daemon command-session code:
   `finalize_context`, runner result if available, terminal status, exit code,
   stdout/stderr, start time, and whether a session id should be included.
2. Inspect mode-specific writable state after the runner exits.
3. Build a normalized `WorkspaceCommandOutcome` with:
   - workspace mode;
   - status and exit code;
   - stdout/stderr;
   - changed paths and path kinds;
   - conflict/error fields;
   - timings/resource metrics;
   - mode-specific metadata.
4. Write or return enough data for the daemon to persist the final response at
   `final_path`.

Responsibilities specific to `eos-ephemeral-workspace`:

1. Capture the fresh overlay upperdir.
2. Publish captured changes through an injected `WorkspacePublisherPort`.
3. Convert publish results into the shared guarded command response shape.
4. Clean up the fresh run dir and release the snapshot lease, or expose an
   idempotent cleanup hook that the daemon must call in a `finally` path.

Responsibilities specific to `eos-isolated-workspace`:

1. Capture the isolated upperdir for reporting/audit only.
2. Never publish and never call OCC.
3. Stamp isolated metadata such as handle id, manifest pin, and
   `published: false`.
4. Emit or return the isolated tool-call audit payload through an injected audit
   sink/port.
5. Leave handle teardown, lease release, namespace teardown, and scratch removal
   to `exit_isolated_workspace`.

`finalize.rs` must not:

- wait on or reap the child process;
- kill process groups;
- manipulate PTY readers/writers;
- remove entries from the daemon command-session registry;
- park completed results for later collection;
- add any publish-capable dependency to `eos-isolated-workspace`.

---

## 7. Symmetry Table

| Area | Ephemeral | Isolated | Symmetric Contract |
|---|---|---|---|
| Concrete ops struct | `EphemeralWorkspaceOps<P>` | `IsolatedWorkspaceOps<P>` | Same trait implementations |
| File module | `eos-ephemeral-workspace/src/file_ops/` | `eos-isolated-workspace/src/file_ops/` | Same root-level folder |
| File trait | `WorkspaceFileOps` impl | `WorkspaceFileOps` impl | Same direct file API |
| Read view | LayerStack-backed `WorkspaceReadView` | Upperdir + snapshot `WorkspaceReadView` | Same path/read contract |
| Mutation sink | Publish-capable `WorkspaceMutationSink` | Audit-only `WorkspaceMutationSink` | Same mutation outcome contract |
| Command module | `command_session/` | `command_session/` | Same root-level folder |
| Command trait | `CommandWorkspacePolicy` impl | `CommandWorkspacePolicy` impl | Same command policy API |
| Command prep | Fresh overlay workspace | Existing isolated handle workspace | `prepare_command_workspace(...)` in trait |
| Command finalize | Capture + publish | Capture + audit-only | `finalize_command_workspace(...)` in trait |
| Public daemon ops | Shared | Shared | One wire/API surface |
| Daemon role | Route + adapters | Route + adapters | No mode policy in daemon |
| Tests | Shared behavior tests | Shared behavior tests | Same public op expectations |

Target internal signatures:

```rust
impl<P> WorkspaceFileOps for EphemeralWorkspaceOps<P> { ... }
impl<P> CommandWorkspacePolicy for EphemeralWorkspaceOps<P> { ... }

impl<P> WorkspaceFileOps for IsolatedWorkspaceOps<P> { ... }
impl<P> CommandWorkspacePolicy for IsolatedWorkspaceOps<P> { ... }
```

Exact argument structs should be typed DTOs, not ad hoc JSON bags, once moved
below the daemon adapter boundary.

---

## 8. Intentional Asymmetry Table

| Area | Ephemeral | Isolated | Keep Different? |
|---|---|---|---|
| Persistence | Publishes accepted writes | Discards on exit | Yes |
| OCC | Uses daemon OCC publisher port | No OCC dependency | Yes, hard boundary |
| Overlay lifetime | Fresh per operation/session | Persistent private upperdir | Yes |
| Read source | Active LayerStack truth | Upperdir first, then pinned snapshot | Yes |
| Command output publish | Can publish changed paths | Reports `published: false` | Yes |
| Plugins/LSP | Allowed through shared projection | Forbidden | Yes |
| Search | `exec_command` over shared view | `exec_command` over private view | Symmetric via shell |
| Lifecycle | No enter/exit | Explicit enter/status/exit/TTL | Yes |
| Crate deps | May use runner/overlay and publish ports | No `eos-occ`; avoid LayerStack direct | Yes |
| Concrete trait internals | Publishes and cleans per operation/session | Keeps handle-private state until exit | Yes |

---

## 9. Dependency Rules

| Edge | Status | Reason |
|---|---|---|
| `eos-workspace-api -> eos-daemon` | Forbidden | API contract must stay neutral |
| `eos-workspace-api -> eos-occ` | Forbidden | Shared contract must not imply publish |
| `eos-workspace-api -> eos-layerstack` | Avoid | Use neutral DTOs and daemon-injected ports |
| `eos-ephemeral-workspace -> eos-workspace-api` | Allowed | Implements shared capability traits |
| `eos-isolated-workspace -> eos-workspace-api` | Allowed | Implements shared capability traits |
| `eos-isolated-workspace -> eos-occ` | Forbidden | Build-time no-publish guarantee |
| `eos-isolated-workspace -> eos-layerstack` | Avoid | Use daemon-injected snapshot/read ports |
| `eos-isolated-workspace -> eos-runner` | Avoid | Keep runner request/process execution behind daemon/runtime ports |
| `eos-isolated-workspace -> eos-ephemeral-workspace` | Forbidden | Modes must not depend on each other |
| `eos-ephemeral-workspace -> eos-occ` | Avoid | Use `WorkspacePublisherPort`; daemon owns singleton OCC cache |
| `eos-ephemeral-workspace -> eos-layerstack` | Avoid | Prefer daemon-injected snapshot/path/read ports |
| `eos-daemon -> eos-*-workspace` | Allowed | Daemon is the adapter/router crate |

`eos-isolated-workspace` may add `eos-protocol` for file DTOs, path types,
limits, and search/replace helpers if file ops move there. It must still avoid
publish-capable dependencies.

---

## 10. Result Shape Rules

1. `read_file`, `write_file`, `edit_file`, and `exec_command` responses must
   preserve the actual workspace mode.
2. Typed sandbox API parsing must not collapse isolated results to
   `Workspace::Ephemeral`.
3. File conflict responses should have the same field semantics in both modes:
   `status`, `conflict.reason`, `conflict_file`, `message`,
   `conflict_reason`, and edit `applied_edits`.
4. Isolated responses may include isolated-only metadata such as handle id,
   `published: false`, and audit fields.
5. Ephemeral responses may include publish/OCC timings and published manifest
   metadata.
6. Shared trait methods return typed outcomes; daemon JSON construction should
   be a thin conversion from those DTOs rather than hand-built mode JSON.

---

## 11. Surface Decisions

| Surface | Decision |
|---|---|
| `read_file` / `write_file` / `edit_file` | Symmetric public ops through `WorkspaceFileOps`; mode-specific implementation in workspace crates |
| `exec_command` | Symmetric public op; daemon session control, mode-specific `CommandWorkspacePolicy` prepare/finalize |
| `write_stdin` / cancel / collect / count | Daemon-owned command-session control |
| Path/read backing | Trait-shaped through `WorkspaceReadView` |
| Mutation persistence | Trait-shaped through `WorkspaceMutationSink`; publish vs audit differs by mode |
| Search | Use `exec_command`; future dedicated search tools should route symmetrically |
| Plugins / LSP | Explicitly forbidden while isolated mode is active |
| Isolated lifecycle | Owned by `eos-isolated-workspace::session` |

---

## 12. Verification Targets

Implementation should add or preserve focused coverage for:

1. Ephemeral and isolated `read_file` use the same public op and preserve
   workspace mode.
2. Ephemeral `write_file` publishes through OCC; isolated `write_file` is
   visible only inside the isolated handle and is discarded on exit.
3. Ephemeral and isolated `edit_file` conflicts use the same response field
   semantics.
4. `exec_command` uses the same public op in both modes and returns the correct
   workspace mode.
5. `write_stdin`, cancel, collect, count remain daemon-owned and work for both
   command workspace kinds.
6. Plugin/LSP operations return `forbidden_in_isolated_workspace` while isolated
   mode is active.
7. `eos-isolated-workspace` still has no `eos-occ` dependency.
8. `eos-ephemeral-workspace` and `eos-isolated-workspace` both compile against
   `WorkspaceFileOps` and `CommandWorkspacePolicy`.
9. `WorkspaceReadView` tests cover LayerStack-backed reads and isolated
   upperdir-first reads through the same request DTOs.
10. `WorkspaceMutationSink` tests prove ephemeral publishes while isolated
    returns `published: false` without linking `eos-occ`.
