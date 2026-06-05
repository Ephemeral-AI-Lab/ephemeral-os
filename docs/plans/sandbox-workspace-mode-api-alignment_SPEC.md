# SPEC: Sandbox Workspace Mode API Alignment

Status: DRAFT
Date: 2026-06-05
Owner doc: `docs/plans/sandbox-workspace-mode-api-alignment_SPEC.md`
Scope: `sandbox/crates/eos-daemon`, `sandbox/crates/eos-ephemeral-workspace`,
`sandbox/crates/eos-isolated-workspace`, and the typed sandbox API result
parsers that consume daemon workspace responses.

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

## 2. Non-Goals

- No model-facing tool rename.
- No daemon wire op rename.
- No isolated workspace promotion path.
- No plugin/LSP execution inside isolated mode.
- No extraction of PTY/process/session registry ownership out of `eos-daemon`.
- No `eos-occ` dependency in `eos-isolated-workspace`.

---

## 3. Target File and Folder Structure

```text
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
  file_ops/
    mod.rs
    read.rs
    write.rs
    edit.rs
    response.rs
  command_session/
    mod.rs
    prepare.rs                  # fresh overlay command workspace
    finalize.rs                 # capture + publish outcome
    types.rs
  capture.rs
  cleanup.rs
  finalize.rs
  ports.rs
  runner.rs
  types.rs

sandbox/crates/eos-isolated-workspace/src/
  file_ops/
    mod.rs
    read.rs
    write.rs
    edit.rs
    response.rs
  command_session/
    mod.rs
    prepare.rs                  # handle/setns/private command workspace
    finalize.rs                 # audit-only capture, no publish
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

## 4. Workspace Ops Role

`sandbox/crates/eos-daemon/src/workspace_ops.rs` becomes dispatch-only:

1. Receive `api.v1.read_file`, `api.v1.write_file`, and `api.v1.edit_file`.
2. Select mode by active isolated state for `agent_id`.
3. Call `eos_isolated_workspace::file_ops::*` when isolated is open.
4. Otherwise call `eos_ephemeral_workspace::file_ops::*`.
5. Inject daemon adapters for LayerStack, OCC publish, and snapshot reads.
6. Map lower-crate errors into `DaemonError`.

It should not contain direct `LayerStack::open`, direct `apply_occ_changeset`,
search/replace logic, isolated upperdir logic, or response builders.

---

## 5. Command Session Role

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

The workspace crates expose symmetric mode-policy entry points. The daemon owns
the long-running session control plane.

---

## 6. Symmetry Table

| Area | Ephemeral | Isolated | Symmetric Contract |
|---|---|---|---|
| File module | `eos-ephemeral-workspace/src/file_ops/` | `eos-isolated-workspace/src/file_ops/` | Same root-level folder |
| File functions | `read_file`, `write_file`, `edit_file` | `read_file`, `write_file`, `edit_file` | Same callable shape from daemon |
| Command module | `command_session/` | `command_session/` | Same root-level folder |
| Command prep | Fresh overlay workspace | Existing isolated handle workspace | `prepare_command_workspace(...)` |
| Command finalize | Capture + publish | Capture + audit-only | `finalize_command_workspace(...)` |
| Public daemon ops | Shared | Shared | One wire/API surface |
| Daemon role | Route + adapters | Route + adapters | No mode policy in daemon |
| Tests | Shared behavior tests | Shared behavior tests | Same public op expectations |

Target internal signatures:

```rust
// eos-ephemeral-workspace
pub fn read_file(...);
pub fn write_file(...);
pub fn edit_file(...);
pub fn prepare_command_workspace(...) -> PreparedCommandWorkspace;
pub fn finalize_command_workspace(...) -> WorkspaceCommandOutcome;

// eos-isolated-workspace
pub fn read_file(...);
pub fn write_file(...);
pub fn edit_file(...);
pub fn prepare_command_workspace(...) -> PreparedCommandWorkspace;
pub fn finalize_command_workspace(...) -> WorkspaceCommandOutcome;
```

Exact argument structs should be typed DTOs, not ad hoc JSON bags, once moved
below the daemon adapter boundary.

---

## 7. Intentional Asymmetry Table

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

---

## 8. Dependency Rules

| Edge | Status | Reason |
|---|---|---|
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

## 9. Result Shape Rules

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

---

## 10. Surface Decisions

| Surface | Decision |
|---|---|
| `read_file` / `write_file` / `edit_file` | Symmetric public ops, mode-specific implementation in workspace crates |
| `exec_command` | Symmetric public op; daemon session control, mode-specific workspace prepare/finalize |
| `write_stdin` / cancel / collect / count | Daemon-owned command-session control |
| Search | Use `exec_command`; future dedicated search tools should route symmetrically |
| Plugins / LSP | Explicitly forbidden while isolated mode is active |
| Isolated lifecycle | Owned by `eos-isolated-workspace::session` |

---

## 11. Verification Targets

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
