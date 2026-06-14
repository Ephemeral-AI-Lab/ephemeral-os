# operation Operation Contract Consolidation Spec

Status: **proposed v2** (2026-06-12) — supersedes v1 (`core/*` consolidation).
v1 was adversarially verified read-only and rejected a unified input/output
interface (old §10); a second deep review (daemon dispatch sweep, sibling-crate
contract inventory, gateway/host edge map) produced findings F7–F14 that
overturn that rejection. v2 finalizes the unified **operation schema** design.
No code has been moved yet.

Scope: `crates/operation/src/**`, one contract relocation out of
`crates/command-session`, and the `daemon` adapter/wire-shaping layer
that the schema seams replace (`dispatch/`, `op_adapter/`, `runtime/response.rs`).
Gateway, host, and fixtures are blast radius, never redesigned here.

---

## 1. Motivation — verified findings

### 1.1 Carried from v1 (all still hold)

| # | Finding | Evidence |
|---|---|---|
| F1 | The 8 per-module `ops.rs` files (`file`, `command`, `checkpoint`, `plugin`, `control`, `sandbox`, `isolation`, `workspace_run`) each hand-roll the same trio — `FAMILY_OPS`, a per-family enum, a `contract()` method — ~230 LOC with **zero consumers** anywhere in the repo. | Daemon registers from the catalog directly: `OpTable::with_builtins()` filters `BUILTIN_OPS` by `ServedBy::Daemon` (`daemon/src/dispatch/dispatcher.rs:39-50`). |
| F2 | Four modules — `control/`, `eos-sandbox/`, `isolation/`, `workspace_run/` — contain **nothing but** that scaffolding. | `src/{control,sandbox,isolation,workspace_run}/lib.rs`. |
| F3 | `core/ops.rs` is undescriptive; the file's own vocabulary calls it the "static sandbox operation catalog". | `src/core/ops.rs:1-6,265-279`. |
| F4 | Live audit semantics are the `mutation_source` strings: `"direct_write"`, `"direct_edit"` (`file/direct.rs`), `"isolated_workspace"` (two producers: `file/isolated.rs` and `command/settle.rs:119`), `"overlay_capture"` (`command/settle.rs:74`). `"plugin_overlay"` is daemon-synthesized and stays daemon-owned. | Daemon contract tests assert the strings byte-for-byte. |
| F5 | `FileOpsError` (`file/lib.rs:21-35`) and `WorkspaceApiError` (`command/outcome.rs:6-28`) are the same contract: a `struct(String)` whose `new(_kind, message)` **discards** `kind`. | Verified across `file/direct.rs`, `command/settle.rs`, `command/prepare.rs`. |
| F6 | The command op contract lives in the PTY substrate (`command-session/src/contract.rs`); `CommandResponse::to_wire_value()` re-reads the workspace mutation envelope out of an untyped `metadata: serde_json::Value` by string key, keys written by `command/settle.rs:166-175`. | Substrate's only contact is `session.rs:242` `persist_final(&CommandResponse)`. |

### 1.2 New in v2 (second review)

| # | Finding | Evidence |
|---|---|---|
| F7 | **The core outcome type is not the wire contract.** `WorkspaceMutationOutcome` derives `Serialize`, but the daemon never serializes it: `mutation_response` hand-copies fields into `GuardedResponse`, which re-keys `workspace_kind`→`workspace`, always emits explicit `"conflict": null` / `"error": null` (core's derive skips `conflict`), and gates `published`/`applied_edits` by op. Two parallel definitions of one wire shape. | `daemon/src/runtime/response.rs:93-130` vs `core/outcome.rs:30-49`. |
| F8 | **Three error-envelope producers, two channels.** Dispatcher `ErrorEnvelope` (`{success, warnings, timings, error}`) from `DaemonError::wire_kind()`; adapter `error_json` (`{success, error}`) returned through the **success** channel for business refusals (`invalid_argument`, `active_background_work`); command's synthetic not-found response. Same `kind/message/details` vocabulary, three shapes. | `op_adapter/mod.rs:19-40`, `op_adapter/isolation.rs:19-45`, `op_adapter/command.rs:218`, `wire/envelope.rs`. |
| F9 | **The plugin family already violates the v1 §9 layering rule** ("the module never sees the wire `Value`"): `PluginRuntime::ensure(&self, args: &Value)`, `ParsedEnsure::from_args(&Value)`, overlay caller-id fishing — the wire `Value` penetrates two layers into operation. | `plugin/lib.rs:217`, `plugin/ensure.rs:72-101`, `plugin/overlay.rs:57-60`. |
| F10 | **Stringly vocabulary census** beyond F4: `workspace_kind` `"ephemeral"`/`"isolated"` at 10 sites across file/command/daemon; outcome `status` (`"committed"`, `"rejected"`, `"aborted_version"`, `"aborted_overlap"` + `CommitStatus::wire_str()` passthrough); command `status` (`"running"/"ok"/"cancelled"/"error"/"timed_out"`); `changed_path_kinds` values (`"write"/"delete"/"symlink"/"opaque_dir"` — typed as `LayerChange` in layerstack, stringly in every outcome); caller fallback `"default"`. | `file/direct.rs:29,110,114`, `file/isolated.rs:25,70,73`, `file/lib.rs:192,222-247`, `command/settle.rs:69,114`, `command/service.rs:124,169,413,427`, `op_adapter/mod.rs:44-50`. |
| F11 | **Routing never reads family args.** File ops route isolated-vs-direct purely on `caller_id` + daemon workspace state (`command_binding_for`); `layer_stack_root` is only required by the direct branch *after* routing. Exec-command targeting is the same. | `op_adapter/files.rs:151-176` (verified directly). |
| F12 | **The dispatcher ABI admits generic typed handlers.** `Handler = for<'ctx> fn(&Value, DispatchContext<'ctx>) -> Result<Value, DaemonError>`; a monomorphized generic wrapper collapses to exactly this fn pointer. | `dispatch/dispatcher.rs:26`. |
| F13 | **The catalog already has external data consumers**: the gateway embeds and parses `ops.json` (`include_str!`, no crate dep) for routing + visibility; `cargo xtask check-contract` gates drift; docs generate from it. The catalog artifact — not the crate — is the cross-trust-boundary contract surface. | `gateway/src/gateway.rs:13,52-54,203-219`, `xtask/src/main.rs:149-216`. |
| F14 | **No dependency cycles; identity types don't exist below.** operation sits above all substrate crates; nothing below depends on it. v1 §10 claimed caller-identity types are "owned by workspace/command-session" — those crates own bare `String` fields; the only identity newtype anywhere is `IsolatedWorkspaceId` (`workspace/src/isolated_workspace/manager/mod.rs:26`). | Cargo graph sweep; contract inventory. |

## 2. Decisions

| # | Decision | Final |
|---|---|---|
| D1 | Schema trait naming | `Operation` (identity) + `OperationInput` + `OperationOutput` in `core/schema.rs`. The `io` abbreviation is banned from names. |
| D2 | Schema granularity | Input and output are separate traits; per-op marker types implement both. Identity (`const OP`) lives once, on `Operation`. |
| D3 | Scope | The `ServedBy::Daemon` ops. The 4 host `sandbox.*` ops are excluded (trust boundary -- host/gateway consume `ops.json` as data, F13). Static first-party plugin providers use cataloged `sandbox.plugin.*` ops. |
| D4 | Family contract file | `contract.rs` per family. All 8 `ops.rs` scaffolds deleted; "ops" vocabulary is reserved for the core catalog. |
| D5 | DTO suffixes | Standardized `*Input` / `*Output`. Exception: `WorkspaceMutationOutcome` keeps its name (shared semantic block, known to daemon contract tests). |
| D6 | Catalog prefix | Catalog types keep `Op*` (`OpContract`, `OpFamily`, `OpVisibility`, `OpError`). No `Operation*` rename of catalog types. |
| D7 | Typed IDs | `CallerId`, `InvocationId` in `core/id.rs` now; `CommandSessionId` lands with the command relocation. Substrate crates keep `String` fields; conversion at their boundary (possible because of F14's dependency direction). |
| D8 | Mutation block | Full `MutationCore` composition via `#[serde(flatten)]`; `WorkspaceMutationOutcome` and `CommandMetadata` both compose it. Daemon `GuardedResponse` is deleted — core serialization becomes the wire. |
| D9 | Errors | One boundary type `OpError { kind, message, details }`. `FileOpsError`/`WorkspaceApiError` become documented re-exports of it; the adapter `error_json` channel folds into it. Family-internal typed errors (`CheckpointError`, `PluginRuntimeError`, `IsolatedError`) stay heterogeneous. |
| D10 | Plugin inputs | Static provider inputs live in `plugin/contract.rs`; legacy provisioning DTOs and dynamic plugin payloads are removed from compiled code and are no longer part of the public catalog/API. |
| D11 | Command contract | Relocates to `command/contract.rs` (§11); `CommandResponse.metadata: Value` → `Option<CommandMetadata>`; `status: String` → `CommandStatus` enum (typing becomes possible only because the DTO moves above the substrate). |
| D12 | v1 §9 rule 2 ("adapter-inline parsing is the design") | **Overturned.** Control/isolation/workspace_run/checkpoint get typed input/output DTOs in operation; behavior stays daemon-side. operation owns the contract for every daemon-served op; the daemon implements against it. |

## 3. Final layout

```
crates/operation/src/
├── lib.rs                      re-exports core contracts
├── core/
│   ├── lib.rs                  facade (below)
│   ├── catalog.rs              ← renamed from ops.rs; + OpFamily::contracts()        (§4)
│   ├── schema.rs               + NEW  Operation/OperationInput/OperationOutput,
│   │                                  parse_input/output_value, CallerFields,
│   │                                  OperationSchemaEntry registry                  (§5)
│   ├── outcome.rs              ~ RESTRUCTURED  MutationCore, WorkspaceMutationOutcome,
│   │                                  WorkspaceConflict, WorkspaceKind, MutationStatus,
│   │                                  ChangedPathKind, WorkspaceTimings              (§6)
│   ├── audit.rs                + NEW  MutationSource                                 (§7)
│   ├── error.rs                + NEW  OpError { kind, message, details }             (§8)
│   └── id.rs                   + NEW  CallerId, InvocationId, CommandSessionId       (§9)
├── checkpoint/      lib.rs, commit.rs, contract.rs   (CommitInput/CommitOutput move
│                                                      here from lib.rs)
├── command/         lib.rs, contract.rs (relocated DTOs + CommandMetadata +
│                    CommandStatus), outcome.rs, prepare.rs, registry.rs,
│                    runtime.rs, service.rs, settle.rs                                (§11)
├── file/            lib.rs, contract.rs, direct.rs, isolated.rs, port.rs, tests.rs
├── plugin/          lib.rs, contract.rs (PluginListInput/PluginHealthInput/Pyright DTOs),
│                    + existing modules
├── control/         lib.rs, contract.rs              (DTOs + markers only; behavior
├── isolation/       lib.rs, contract.rs               stays in the daemon — D12)
└── workspace_run/   lib.rs, contract.rs

DELETED: all 8 family ops.rs; the eos-sandbox/ module entirely (host family has no
daemon schema, D3); daemon GuardedResponse + per-op hand parsing +
error_json producers (§12).
```

```rust
// core/lib.rs
pub mod catalog;
pub mod schema;

mod audit;
mod error;
mod id;
mod outcome;

pub use audit::MutationSource;
pub use error::OpError;
pub use id::{CallerId, CommandSessionId, InvocationId};
pub use outcome::{
    ChangedPathKind, ChangedPathKinds, MutationCore, MutationStatus, WorkspaceConflict,
    WorkspaceKind, WorkspaceMutationOutcome, WorkspaceTimings,
};
```

`catalog` and `schema` stay `pub mod` (consumers path into name constants and
trait bounds); single-type modules stay private and re-export at `core::`,
matching the existing `outcome` pattern. `src/lib.rs` drops only the `sandbox`
module decl; `control`/`isolation`/`workspace_run` keep their decls (their
content becomes `contract.rs`).

Net effect: operation ≈ LOC-neutral (contract DTOs replace dead
scaffolding); daemon clearly net-negative (adapters become thin generic
wrappers; `GuardedResponse` re-keying and `error_json` duplication deleted).

## 4. `core/catalog.rs` — registration (rename + one accessor)

Unchanged from v1 §3: `core/ops.rs` is renamed to `core/catalog.rs` (the file's
own vocabulary, F3); the `declare_builtin_ops!` macro, the 32-op table,
`OpFamily`/`ServedBy`/`OpVisibility`, the name constants, `PROTOCOL_VERSION`,
and `ops_json_document()` stay byte-identical. Two names intentionally do
**not** change: `ops_json_document()` and the `ops.json` artifact (tied to
`eosd dump-ops` and the conformance suite).

One addition replaces all eight `FAMILY_OPS` constants and family enums:

```rust
impl OpFamily {
    /// All catalog contracts owned by this family, in `ops.json` order.
    pub fn contracts(self) -> impl Iterator<Item = &'static OpContract> {
        BUILTIN_OPS.iter().filter(move |contract| contract.family == self)
    }
}
```

Rename blast radius (`core::ops` → `core::catalog`), enumerated exhaustively:

| Consumer | Sites | Change |
|---|---|---|
| `daemon` | `dispatch/dispatcher.rs:9`, `dispatch/builtin_handlers.rs:3`, `wire/mod.rs:15` | import path only |
| `eosd` | `src/main.rs:55` (`dump-ops`) | import path only |
| `e2e-test` | 50 test files + `src/pool.rs` | mechanical rename; verified by `cargo check --workspace` |

## 5. `core/schema.rs` — the unified operation schema (new)

Every daemon-served catalog op declares a typed signature: its wire input type
and wire output type, bound to the catalog through a marker type.

```rust
use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::Value;

use super::catalog::BuiltinOp;
use super::error::OpError;
use super::id::{CallerId, InvocationId};

/// Typed identity of one catalog operation.
pub trait Operation {
    const OP: BuiltinOp;
}

/// Wire input schema of an operation: caller intent only — no config, no
/// context. Inputs tolerate unknown keys (no `deny_unknown_fields`) and pin
/// today's absent-vs-default semantics with `#[serde(default)]` attributes.
pub trait OperationInput: Operation {
    type Input: DeserializeOwned + Serialize;
}

/// Wire output schema of an operation: serialization is the wire success body.
pub trait OperationOutput: Operation {
    type Output: Serialize;

    /// Envelope-level `success`; mutation-bearing outputs override from
    /// `MutationCore.success` (conflicts return through the success channel
    /// with `success: false`).
    fn success(_output: &Self::Output) -> bool {
        true
    }
}

/// The one parse seam: wire `args` → typed input, `OpError::invalid_request`
/// (or the op's pinned refusal kind) on mismatch.
pub fn parse_input<O: OperationInput>(args: &Value) -> Result<O::Input, OpError>;

/// The one render seam: `{"success": …}` ∪ serialized output.
pub fn output_value<O: OperationOutput>(output: &O::Output) -> Value;

/// Cross-cutting identity parsed before routing (F11): file, command, plugin,
/// isolation, and workspace_run all key on it. The daemon applies the
/// `"default"` caller fallback after extraction (its current behavior).
pub struct CallerFields {
    pub caller_id: Option<CallerId>,
    pub invocation_id: Option<InvocationId>,
}

/// Value-level registry row; each family `contract.rs` exports its slice.
/// A crate test asserts coverage: every `ServedBy::Daemon` catalog entry has
/// exactly one row. Endgame (§15 phase 8): rows grow `input_schema` /
/// `output_schema` fns emitted into `ops.json`.
pub struct OperationSchemaEntry {
    pub op: BuiltinOp,
    pub check_input: fn(&Value) -> Result<(), OpError>,
}
```

Binding pattern (markers named after their `BuiltinOp` variant):

```rust
// file/contract.rs
pub enum WriteFile {}
impl Operation       for WriteFile { const OP: BuiltinOp = BuiltinOp::WriteFile; }
impl OperationInput  for WriteFile { type Input  = WriteFileInput; }
impl OperationOutput for WriteFile {
    type Output = WorkspaceMutationOutcome;
    fn success(output: &Self::Output) -> bool { output.core.success }
}
```

Why markers, not impls on the DTOs: shared shapes. `WorkspaceMutationOutcome`
is the output of `WriteFile` and `EditFile`; `CallerCountInput` serves
`sandbox.call.count` and `sandbox.command.count`; `EmptyInput` serves
`sandbox.isolation.list_open` and `test_reset`. One DTO, many ops, no
conflicting impls.

The daemon handler becomes a generic wrapper requiring both sides —
`fn handle<O: OperationInput + OperationOutput>(…)` — monomorphized per op into
the existing sync fn-pointer `OpTable` (F12). Asymmetric consumers take only
the bound they need: the dispatch entry `OperationInput`, the response layer
`OperationOutput`.

Request lifecycle:

```
{op, invocation_id, args}
   │ dispatcher: OpTable lookup (unchanged HashMap + sync fn-pointer ABI)
   ▼
parse CallerFields ──► route on caller + daemon state     (F11: routing never reads family args)
   ▼
parse_input::<O>(args) ─► O::Input                         (one seam)
   ▼
enrich: DispatchContext → explicit params (FileLimits, roots, registries)
   ▼
op behavior (operation fn or daemon runtime) ─► Result<O::Output, FamilyError>
   ▼                                                  └─ From → OpError ─► one envelope producer
output_value::<O> ─► {success} ∪ serialized Output    (core serialization IS the wire)
   ▼
daemon response enrichment: resource-timing splice, plugin_overlay synthesis
(now reading MutationCore instead of re-keying by hand)
```

## 6. `core/outcome.rs` — `MutationCore` composition (restructured)

The mutation block is spelled four ways today: `WorkspaceMutationOutcome`,
`command/settle.rs`'s `json!` bag, the daemon's `GuardedResponse`, and
`guarded_changeset_response` (F7, F4). One shared core, composed:

```rust
/// Outcome vocabulary, spellings pinned by daemon contract tests.
pub enum WorkspaceKind { Ephemeral, Isolated }            // "ephemeral" / "isolated"
pub enum MutationStatus {
    Committed, Rejected, AbortedVersion, AbortedOverlap,
    // + CommitStatus wire passthrough variants, pinned during phase 2
}
pub enum ChangedPathKind { Write, Delete, Symlink, OpaqueDir }
pub type ChangedPathKinds = BTreeMap<String, ChangedPathKind>;
pub type WorkspaceTimings = BTreeMap<String, Value>;

/// The shared mutation block: the shape file ops, command settlement, and
/// plugin overlays all converge on.
#[derive(Serialize, Deserialize)]
pub struct MutationCore {
    pub success: bool,
    pub changed_paths: Vec<String>,
    pub changed_path_kinds: ChangedPathKinds,
    pub mutation_source: String,             // built from MutationSource::as_str()
    pub conflict: Option<WorkspaceConflict>, // serialized as explicit null (§13.2)
    pub conflict_reason: Option<String>,
    pub timings: WorkspaceTimings,
}

#[derive(Serialize, Deserialize)]
pub struct WorkspaceMutationOutcome {
    #[serde(flatten)]
    pub core: MutationCore,
    #[serde(rename = "workspace")]           // wire key pinned (F7)
    pub workspace_kind: WorkspaceKind,
    pub published: bool,
    pub status: MutationStatus,
    pub applied_edits: i64,                  // omitted when 0 (wire rule §13.2)
}
```

`CommandMetadata` (§11) composes the same block. The daemon response layer and
tests gain one generic access path to every mutating op's outcome:
`output.core` / a `mutation_core()` accessor on mutation-bearing outputs.

Carried v1 refutations that still hold:

- `file/lib.rs`'s private `conflict_outcome()` stays module-private (call sites
  depend on the `FileBackend` abstraction and pass conflict components).
- No shared timing helper: file/command timings are `BTreeMap<String, Value>`;
  checkpoint's are deliberately `BTreeMap<String, f64>` mirroring its wire
  shape one-to-one (`checkpoint/lib.rs:47-62`).

## 7. `core/audit.rs` — live attribution vocabulary (new)

v1 §5 verbatim:

```rust
/// Canonical `mutation_source` strings recorded on mutation outcomes. Wire
/// spellings are asserted byte-for-byte by daemon contract tests. The daemon
/// additionally synthesizes `"plugin_overlay"` at its response layer; that
/// spelling is daemon-owned and intentionally absent here.
pub enum MutationSource { DirectWrite, DirectEdit, IsolatedWorkspace, OverlayCapture }

impl MutationSource {
    pub const fn as_str(self) -> &'static str { /* "direct_write" | "direct_edit"
        | "isolated_workspace" | "overlay_capture" */ }
}
```

Consumers: `FileBackend::workspace_kind()` → `WorkspaceKind` and
`FileBackend::mutation_source()` → `MutationSource` (trait at `file/lib.rs:85`,
impls in `direct.rs`/`isolated.rs`/`tests.rs`); `command/settle.rs:74,119`
switch literals to `as_str()`. Outcome fields stay `String` on the wire. The
load-bearing case is the cross-module `"isolated_workspace"` literal (F4).

## 8. `core/error.rs` — the one error boundary (new)

v1 §6 extended with `details` to fully mirror the wire `ErrorBody` and absorb
the `error_json` channel (F8):

```rust
/// Request-level operation failure: stable kind + operator-facing message +
/// optional structured details. `Display` prints the message only, matching
/// both predecessor types, so daemon error envelopes are unchanged.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{message}")]
pub struct OpError {
    pub kind: &'static str,        // "invalid_request", "invalid_argument",
    pub message: String,           // "active_background_work", …
    pub details: Option<serde_json::Value>,
}

impl OpError {
    pub fn new(kind: &'static str, message: impl Into<String>) -> Self;
    pub fn invalid_request(message: impl Into<String>) -> Self;
    pub fn with_details(self, details: serde_json::Value) -> Self;
}
```

Consumed as documented re-exports: `pub use crate::core::OpError as
FileOpsError;` in `file/lib.rs`, same for `WorkspaceApiError` in
`command/outcome.rs` (`kind` stops being discarded — F5). Business refusals
that today travel as `Ok(error_json(…))` — `invalid_argument`,
`active_background_work`, the `IsolatedError` payload mapping — become
`Err(OpError { kind, … })` with byte-identical rendering (§13.3). The command
not-found synthetic is **not** an error: it is a `CommandResponse`-shaped
output and stays one.

Module-internal error enums are **not** unified: `CheckpointError`,
`PluginRuntimeError`, and `IsolatedError` stay heterogeneous; they map into
`OpError` at the op boundary via `From`.

## 9. `core/id.rs` — typed identities (new)

```rust
pub struct CallerId(String);
pub struct InvocationId(String);
pub struct CommandSessionId(String);   // lands with the command relocation (§11)
```

String newtypes with `new`/`as_str`/serde transparency. Used by op DTOs and
`CallerFields`; substrate crates keep `String` fields with conversion at their
boundary — no dependency changes required (F14). v1 §10's `CallerId` rejection
is overturned as factually wrong: no other crate owns an identity type
(only `IsolatedWorkspaceId` exists, and it stays in workspace).

## 10. Family contracts — the operation schema table

The complete signature set. Each row is a marker in its family's
`contract.rs` implementing `Operation` + `OperationInput` + `OperationOutput`.
Output field sets are today's wire keys, verbatim (canonical-equal bar, §13.1).

| Op | Input → fields | Output → fields | Behavior |
|---|---|---|---|
| **control/contract.rs** | | | daemon runtime |
| `sandbox.runtime.ready` | `RuntimeReadyInput { layer_stack_root }` | `RuntimeReadyOutput { ready, probes, daemon_pid, uptime_s, timings }` | |
| `sandbox.call.heartbeat` | `HeartbeatInput { invocation_ids }` | `HeartbeatOutput { touched }` | |
| `sandbox.call.cancel` | `CancelInvocationInput { invocation_id }` | `CancelInvocationOutput { invocation_id, cancelled, already_done, cleanup_done }` | |
| `sandbox.call.count` | `CallerCountInput { caller_id }` *(shared)* | `InflightCountOutput { caller_id, count }` | |
| **checkpoint/contract.rs** | | | operation + layerstack |
| `sandbox.checkpoint.layer_metrics` | `LayerMetricsInput { layer_stack_root }` | `LayerMetricsOutput` (manifest/storage/lease metrics, wire-pinned) | |
| `sandbox.checkpoint.ensure_base` | `EnsureBaseInput { layer_stack_root, workspace_root }` | `WorkspaceBaseOutput { created, binding, timings }` *(shared)* | |
| `sandbox.checkpoint.build_base` | `BuildBaseInput { layer_stack_root, workspace_root, reset }` | `WorkspaceBaseOutput` | |
| `sandbox.checkpoint.commit_to_workspace` | `CommitToWorkspaceInput { layer_stack_root, workspace_root }` | `CommitToWorkspaceOutput { manifest_version, timings }` | |
| `sandbox.checkpoint.commit_to_git` | `CommitInput { layer_stack_root, workspace_root, message, paths }` (`paths`: custom `null\|str\|[str]` deserializer) | `CommitOutput { committed, commit_sha, manifest_version, manifest_root_hash, paths, worktree_mode, timings }` | |
| `sandbox.checkpoint.binding` | `BindingInput { layer_stack_root }` | `BindingOutput { binding }` | |
| **file/contract.rs** | | | operation::file |
| `sandbox.file.read` | `ReadFileInput { path, layer_stack_root? }` | `ReadFileOutput { workspace, success, content, exists, encoding, timings }` | |
| `sandbox.file.write` | `WriteFileInput { path, content, overwrite, layer_stack_root? }` | `WorkspaceMutationOutcome` | |
| `sandbox.file.edit` | `EditFileInput { path, edits, layer_stack_root? }` | `WorkspaceMutationOutcome` | |
| **plugin/contract.rs** | | | operation::plugin |
| `sandbox.plugin.list` | `PluginListInput` | `PluginListOutput { providers }` | |
| `sandbox.plugin.health` | `PluginHealthInput` | `PluginHealthOutput { providers }` | |
| `sandbox.plugin.pyright_lsp.query_symbols` | `PyrightLspQuerySymbolsInput { file_path, query?, workspace? }` | `PyrightLspQuerySymbolsOutput { provider, manifest_key, freshness, stale, symbols }` | |
| `sandbox.plugin.pyright_lsp.definition` | `PyrightLspDefinitionInput { file_path, position }` | `PyrightLspLocationsOutput { provider, manifest_key, freshness, stale, locations }` | |
| `sandbox.plugin.pyright_lsp.references` | `PyrightLspReferencesInput { file_path, position, include_declaration? }` | `PyrightLspLocationsOutput { provider, manifest_key, freshness, stale, locations }` | |
| `sandbox.plugin.pyright_lsp.diagnostics` | `PyrightLspDiagnosticsInput { file_path }` | `PyrightLspDiagnosticsOutput { provider, manifest_key, freshness, stale, diagnostics }` | |
| **command/contract.rs** (§11) | | | operation::command |
| `sandbox.command.exec` | `ExecCommandInput { cmd, caller_id, layer_stack_root?, timeout_seconds?, yield_time_ms? }` | `CommandResponse { status: CommandStatus, exit_code, stdout, stderr, command_session_id?, workspace?, metadata: Option<CommandMetadata> }` | top-level `invocation_id` is the exec identity |
| `sandbox.command.write_stdin` | `WriteStdinInput { command_session_id, chars, yield_time_ms? }` | `CommandResponse` | |
| `sandbox.command.poll` | `ReadProgressInput { command_session_id, last_n_lines }` | `CommandResponse` | |
| `sandbox.command.cancel` | `CancelCommandInput { command_session_id }` | `CommandResponse` | |
| `sandbox.command.collect_completed` | `CollectCompletedInput { command_session_ids?, caller_id? }` | `CollectCompletedOutput { success, completions, has_more, max_completions }` | bounded oldest-first completion batch; callers repeat while `has_more` |
| `sandbox.command.count` | `CallerCountInput` *(shared)* | `CommandSessionCountOutput { caller_id, count }` | |
| **isolation/contract.rs** | | | daemon WorkspaceRuntime |
| `sandbox.isolation.enter` | `IsolationEnterInput { caller_id, layer_stack_root }` | `IsolationEnterOutput { manifest_version, manifest_root_hash, workspace_handle_id, workspace_root }` | |
| `sandbox.isolation.exit` | `IsolationExitInput { caller_id, grace_s? }` | `IsolationExitOutput { evicted_upperdir_bytes, lifetime_s, total_ms, phases_ms, inspection }` | |
| `sandbox.isolation.status` | `IsolationStatusInput { caller_id }` | `IsolationStatusOutput` (enum: `Open{…}` \| `Closed`; wire `{open: false}` pinned) | |
| `sandbox.isolation.list_open` | `EmptyInput` *(shared)* | `ListOpenOutput { open_caller_ids }` | |
| `sandbox.isolation.test_reset` | `EmptyInput` | `TestResetOutput { reset, exited_callers }` | |
| **workspace_run/contract.rs** | | | daemon WorkspaceRuntime |
| `sandbox.run.end` | `RunEndInput { caller_id, grace_s? }` | `RunEndOutput { caller_id, cancelled_command_sessions, isolated_exited }` | |
| `sandbox.run.cancel_all` | `RunCancelAllInput { grace_s? }` | `RunCancelAllOutput { cancelled_command_sessions, isolated_callers_exited }` | |

File-family note (wire-pure inputs): `max_read_bytes` / `max_file_bytes` leave
the request DTOs — they are daemon config, not caller intent
(`file/lib.rs:98-109`). `read_file`/`write_file`/`edit_file` take a
`FileLimits` parameter supplied by the daemon from `DispatchContext`
(`file_limits` already lives there, `runtime/context.rs`). Defaults pinned per
current adapter behavior: `overwrite` defaults `true`, `last_n_lines` defaults
`50`, caller fallback `"default"` applied by the daemon after `CallerFields`
extraction.

## 11. Command contract relocation

Mechanics unchanged from v1 §8; restated with the v2 types.
`command-session/src/contract.rs` op DTOs move to
`operation/src/command/contract.rs`. The dependency direction is dissolved
by re-cutting one substrate API, not a re-export shim (a crate cycle makes any
"substrate imports the moved types" plan unbuildable):

```
BEFORE  daemon ──▶ operation ──▶ command-session   substrate owns op DTOs +
              └────────────────────────────────▲               wire shaping it never constructs

AFTER   daemon ──▶ operation ──▶ command-session   substrate consumes a
                        command/contract.rs     (PTY/process)  pre-rendered Value only
                        owns the op DTOs, typed against core/
```

- `session.rs` `persist_final(response: &CommandResponse)` becomes
  `persist_final(final_wire: &serde_json::Value)`; the caller in
  `operation::command` renders. This is the substrate's only DTO contact
  (`session.rs:242,295`).
- **Moves**: the five request DTOs (renamed per D5: `ExecCommandInput`,
  `WriteStdinInput`, `ReadProgressInput`, `CancelCommandInput`,
  `CollectCompletedInput`), `CommandResponse`, `CommandSessionCompletion`,
  `CollectCompletedOutput`, plus their unit tests.
- **Stays in the substrate**: `tail_lines()` and `CommandSessionError`.
- The `metadata: Value` round-trip (F6) becomes typed, composing the core
  block (D8):

```rust
/// Workspace settlement carried by a settled command response. Serialized by
/// `to_wire_value` into the exact key set the wire has always carried
/// (fixture round-trip test pins this).
pub struct CommandMetadata {
    #[serde(flatten)]
    pub core: MutationCore,
    /// Spliced into the response top-level (today's nested "metadata" object,
    /// plus isolated-mode keys per settle.rs:103-112). Exact membership pinned
    /// by the fixture round-trip test.
    pub extras: serde_json::Map<String, serde_json::Value>,
}

pub enum CommandStatus { Running, Ok, Cancelled, Error, TimedOut }  // wire spellings pinned
```

`CommandResponse.metadata: Value` → `Option<CommandMetadata>`; `settle.rs`
constructs the struct instead of `json!`; `to_wire_value()` becomes
serialization instead of key-fishing. `CommandMetadata` deliberately does
**not** reuse `WorkspaceMutationOutcome` (no `published`/`status`/
`applied_edits`; carries `extras`) — the v1 finding that drove composition
over a mega-struct.

## 12. Daemon-side changes

| Today | After |
|---|---|
| Per-op hand parsing (`require_string`/`as_bool`/`optional_u64`) across 7 `op_adapter/*` files | one generic wrapper per op: `handle::<O>` → `parse CallerFields` → route → `parse_input::<O>` → enrich → call → `output_value::<O>` |
| `GuardedResponse` + `mutation_response` field copy + `workspace_kind`→`workspace` re-keying (F7) | deleted; core `WorkspaceMutationOutcome` serializes the wire. Files stop using it in phase 4; the struct dies when the plugin-overlay path converts (phase 6) |
| `error_json` + `require_arg` second error channel (F8) | deleted; refusals are `Err(OpError)`; the dispatcher remains the sole envelope renderer |
| `plugin_overlay_response` synthesis re-keying changeset fields by hand | unchanged in ownership (daemon-owned synthesis, `"plugin_overlay"` spelling stays daemon-side) but reads/builds `MutationCore` instead of fishing keys |
| `DispatchContext` | unchanged; it is the enrichment source (`FileLimits`, services, registry), explicitly threaded as op parameters |
| `OpTable` / `Handler` ABI, timing splice, `ErrorEnvelope` | unchanged (F12) |

## 13. Wire-invariance rules (binding for every phase)

1. **Acceptance bar** (v1 §8, now global): requests byte-identical; responses
   canonical-equal under all three conformance suites
   (`cargo xtask check-contract`). Key order is not part of the bar; key set,
   values, and null-vs-absent are.
2. **Pinned serde details**: `workspace` rename on `workspace_kind`; explicit
   `"conflict": null` and the mutation response's literal `"error": null`
   (mechanism chosen in phase 4, pinned by fixture round-trip);
   `published`/`applied_edits` omitted-not-null where today omitted;
   `#[serde(flatten)]` membership verified by a round-trip test per
   mutation-bearing op. Inputs: no `deny_unknown_fields`; defaults pinned per
   §10 notes.
3. **`error_json` byte-compat**: each folded refusal keeps its exact rendered
   shape, including which envelope variant carries `warnings`/`timings`.
4. **Artifact stability**: `ops.json` and `ops_json_document()` names
   unchanged; schema fields (phase 8) are additive.
5. **Rollback rule**: every phase is wire-invariant by construction; if any
   conformance suite fails, the phase is reverted, not the fixtures
   (`contract/PROTOCOL.md` immutability rule).

## 14. Adversarial record — v1 §10 revisited

Overturned (with the disproving evidence):

| v1 rejection | Verdict | Why |
|---|---|---|
| Unified input trait — "defaults come from config/`DispatchContext` unavailable at deserialization" | **Overturned** | Self-inflicted: config was mixed into request DTOs (`max_*_bytes`). Wire-pure inputs + explicit context params separate intent from policy. |
| "Routing precedes validation" | **Overturned** | F11: routing reads only `CallerFields` + daemon state; two-phase parse (head → route → input) matches the real dataflow. |
| "Dispatcher `Handler` is a sync fn-pointer ABI" | **Overturned** | F12: monomorphized generic wrappers are fn pointers; zero ABI change. |
| "Error shaping differs per family" | **Overturned at the boundary** | The wire error body is already uniform; `OpError` mirrors it, family internals stay typed. Bonus: F8's three producers collapse to one. |
| `CallerId` rejection ("identity types owned elsewhere") | **Overturned** | F14: factually wrong — only bare `String` fields exist below. |
| Per-family typed enums "zero consumers" | **Overturned for bindings, upheld for enums** | The old scaffolding carried no information beyond the catalog. Schema bindings carry the I/O contract and have consumers: the generic daemon wrapper, the 28/28 completeness test, and phase-8 emission. |
| v1 §9 rule 2 — adapter-inline parsing "is the design, not a gap" | **Overturned** (D12) | With one parse seam, inline fishing has a better home; operation owns the contract, the daemon owns the behavior. |

Upheld:

| v1 rejection | Why it stands |
|---|---|
| Unified outcome mega-struct | Composition (`MutationCore` + flatten) has no dead optional fields; one-struct-fits-all would. `ReadFileOutput`, `CommitOutput`, plugin outcome enums remain distinct. |
| `core/envelope.rs` shared across trust boundaries | `host` runs inside untrusted sandboxes, depends only on `anyhow/serde_json/thiserror`, and must not gain an operation dep (verified). The shared artifact is `ops.json` + `contract/` fixtures (F13). Hence D3: host ops excluded from the schema layer. |
| `AuditRecord` / journal types | Revives a feature removed 2026-06-11; the live remainder is exactly §7. |
| Checkpoint timing type split | `BTreeMap<String, f64>` mirrors its wire one-to-one; stays distinct from `WorkspaceTimings`. |
| Module-internal error unification | `CheckpointError` / `PluginRuntimeError` / `IsolatedError` are heterogeneous by design; only the boundary unifies (§8). |
| Dynamic plugin payloads | Retired. First-party plugin providers are cataloged `sandbox.plugin.*` ops with typed inputs. |

## 15. Migration plan

Each phase lands independently, wire-invariant, and is verified before the
next.

| Phase | Change | Verification |
|---|---|---|
| 1 | Delete 8 dead `ops.rs` + the 4 scaffold-only module decls (re-added with real content in phase 7) | `cargo check -p operation && cargo test -p operation`; grep gate: no `FAMILY_OPS` / family-enum / `operation::{control,sandbox,isolation,workspace_run}` references remain |
| 2 | Vocabulary grounding: `core/audit.rs`, `core/error.rs` (+ re-exports as `FileOpsError`/`WorkspaceApiError`), `core/id.rs`, outcome enums (`WorkspaceKind`, `MutationStatus`, `ChangedPathKind`); retype `FileBackend`; switch settle/backend literals | `cargo test -p operation -p daemon` (daemon contract tests pin the wire strings) |
| 3 | Rename `core/ops.rs` → `core/catalog.rs`; add `OpFamily::contracts()`; mechanical import rename (§4 table) | `cargo check --workspace`; `cargo xtask check-contract` (rendering code unchanged) |
| 4 | `core/schema.rs`; **files** family end-to-end: `file/contract.rs` (wire-pure inputs, markers), `FileLimits` param, generic daemon wrapper, `MutationCore` flatten restructure, files stop using `GuardedResponse` | contract fixtures + files e2e + flatten round-trip test (§13.2) |
| 5 | Command relocation (§11): move DTOs + tests, re-cut `persist_final`, type `CommandMetadata`/`CommandStatus`, update `settle.rs` and daemon/e2e imports | `cargo xtask check-contract` (all three suites) + `CommandMetadata` → `to_wire_value` round-trip against `contract/fixtures/envelopes/` |
| 6 | Static plugin typed inputs (`plugin/contract.rs`, provider list/health and Pyright LSP DTOs); legacy ensure/upload DTOs are internal-only; plugin-overlay synthesis reads `MutationCore`; delete `GuardedResponse` | plugin tests + fixtures |
| 7 | Control/isolation/workspace_run/checkpoint contracts + markers; fold `error_json` refusals into `OpError` (§13.3); delete `eos-sandbox/` module; completeness test (28/28 schema rows vs catalog) | full e2e + fixtures; grep gate: no `error_json` / `require_arg` producers remain |
| 8 *(opt)* | schemars: `input_schema`/`output_schema` per op emitted into `ops.json` → gateway / TypeScript `@eos/contracts` codegen | extended `xtask check-contract` drift gate |
