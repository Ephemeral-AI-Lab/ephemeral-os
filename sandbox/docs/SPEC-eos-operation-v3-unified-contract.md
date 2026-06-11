# eos-operation Unified Operation Contract Spec (v3)

Status: **proposed v3** (2026-06-12) — the aggregate of
`SPEC-eos-operation-core.md` (v2: operation schema, contract-ownership
inversion, typed vocabulary) and `SPEC-eos-operation-unified-op-io.md`
(wire-schema-first: single parse site, `OpTable` deletion, response funnel).
Both parents were adversarially verified; v3 keeps each parent's verified
wins, drops each parent's refuted machinery, and resolves their conflicts
under one directive: **prefer unification and architectural health over
refactoring effort.** No code has been moved yet.

Scope: `crates/eos-operation/src/**`, one contract relocation out of
`crates/eos-command-session`, the `eos-daemon` dispatch/adapter/wire-shaping
layer, and one attribute change in `eos-layerstack`. Gateway, host, and
fixtures are blast radius, never redesigned.

---

## 0. The one-sentence architecture

> **eos-operation owns the complete typed contract of every daemon-served
> operation — identity (catalog), input (request enum), output (typed DTOs
> whose serialization is the wire), vocabulary (enums), and error (one
> boundary type). The daemon owns behavior and transport, nothing else.**

```
                eos-operation (the contract platform)
                ┌──────────────────────────────────────────────┐
   identity     │ core/catalog.rs   BuiltinOp + OpContract     │──▶ ops.json ──▶ gateway/host
   input        │ core/request.rs   OpRequest (28 variants)    │     (data across the trust
                │ <family>/contract.rs  *Input DTOs + parse    │      boundary, F13)
   output       │ <family>/contract.rs  *Output DTOs (serde    │
                │                    serialization IS the wire)│
   response     │ core/response.rs  OpResponse channel         │
   vocabulary   │ core/workspace_outcome.rs  MutationCore +    │
                │                    workspace enums           │
                │ core/audit.rs     MutationSource             │
                │ core/id.rs        CallerId, …                │
   error        │ core/error.rs     OpError {kind,msg,details} │
                └──────────────────────────────────────────────┘
                              ▲ contract        │ typed DTOs
                              │                 ▼
                eos-daemon (behavior + transport only)
                ┌──────────────────────────────────────────────┐
                │ dispatch: shell checks → from_op_name →      │
                │   OpRequest::parse → exhaustive match →      │
                │   behavior fns → OpResponse → daemon finalize│
                └──────────────────────────────────────────────┘
```

## 1. Reconciliation record — what v3 takes from each parent, and why

### 1.1 Kept from unified-op-io (the skeleton)

| Item | Why it wins |
|---|---|
| `OpRequest` enum, ONE parse site, parse as the single authority | Compile-time totality with a structural producer/consumer per variant; the F1 failure mode is impossible by construction |
| `OpTable`/`Handler`/`fn_addr_eq`/boot-panic **deleted**; dispatch = name-resolve → parse → exhaustive match | The boot panic upgrades to a compile error; no registration policing; no opaque `HashMap` hop |
| Hand-written parse fns over shared helpers (NOT serde derive for inputs) | Serde derive cannot reproduce the five wire-pinned string semantics, per-op key-check order, or the exact error spellings — all wire-visible behavior pinned by daemon unit tests |
| Operation-owned `OpResponse` channel funnel with named escape paths | The only design that byte-reproduces all three builtin-op failure encodings without leaving channel ownership in daemon adapters (§7.1) |
| Catalog hardening: drop `#[non_exhaustive]`, macro-generated `contract()`, `from_op_name` | 3-LOC compile-time guarantee, verified safe against e2e usage |
| All verifier pins: parse-order fidelity, caller-id default-before-trim, plugin defaults **false**, overlay capture order, `transport/server.rs:24,341`, five clippy expects, session tests rebuilt, `tail_lines`/`CommandSessionError` stay substrate, additive conflict fixture | Each one is a silent wire break caught adversarially; non-negotiable |

### 1.2 Kept from v2 (the contract surface)

| Item | Why it wins |
|---|---|
| **D12 ownership inversion**: control/isolation/workspace_run/checkpoint get typed Input AND Output DTOs in eos-operation; behavior stays daemon-side | The healthiest boundary: eos-operation owns the contract for every daemon-served op, the daemon implements against it. unified-op-io's "runtime-state outputs stay `json!`" exemption is the lone untyped hole in an otherwise typed surface — closed here |
| Wire-pure inputs: `max_read_bytes`/`max_file_bytes` leave the DTOs; `FileLimits` is an explicit behavior parameter | Caller intent and daemon policy stop sharing a struct — the root cause of v1's "defaults unavailable at deserialization" objection |
| Typed vocabulary enums: `WorkspaceKind`, `MutationStatus`, `ChangedPathKind`, `CommandStatus`, `MutationSource` (F10 census: 10+ stringly sites) | Repo rule: typed enums over stringly shapes. Verified closed sets (§6) |
| `MutationCore` composition; **core serialization IS the wire**; `GuardedResponse` deleted, not relocated | Ends F7 (two parallel definitions of one wire shape). unified-op-io's `MutationWire` relocation kept the daemon-shape duplicated in spirit — one struct whose fields were `Value`-typed to dodge the question. v3 resolves the question instead (§1.3 bar change) |
| `OpError { kind, message, details }` absorbing the `error_json` producer (F8: three producers → one renderer pair) | The refusal channel becomes typed data instead of a per-call-site `Ok(error_json(…))` convention |
| Typed IDs: `CallerId`, `InvocationId`, `CommandSessionId` (F14: nothing below owns identity types) | `CallerId` is not ceremony — it is the **owner** of the default-before-trim routing semantics that two adversarial rounds flagged as a silent-break hazard. The accessor convention becomes a type |
| Naming: `contract.rs` per family; `*Input`/`*Output` suffixes; `Op*` reserved for catalog types; "ops" vocabulary reserved for the catalog | One naming scheme across the whole surface |
| F7–F14 findings and the §14 adversarial record | The evidentiary base |

### 1.3 Dropped, from each parent, with the refuting evidence

| Dropped | From | Why |
|---|---|---|
| `Operation`/`OperationInput`/`OperationOutput` traits, 28 marker types, `OperationSchemaEntry` registry, generic `handle::<O>` wrapper, `OpTable` retention | v2 | Under the fn-pointer ABI **or** the exhaustive-match dispatcher, no code is ever generic over the trait bounds — zero consumers, F1 at trait granularity (verified verdict). The enum makes identity↔input binding structural; the 28/28 completeness test is replaced by the compiler |
| `OpFamily::contracts()` | v2 | Zero consumers (verified). Phase-9 emission iterates `BUILTIN_OPS` directly |
| `CallerFields` pre-parse step | v2 | Full payload parse up front (F11: routing reads only caller + daemon state, never family args) subsumes the two-phase head parse |
| Serde-derived inputs (`type Input: DeserializeOwned`) | v2 | Breaks the five pinned parse-error spellings and key-check order (§4); inputs are hand-parsed, **outputs** are serde-rendered — the asymmetry is the design |
| `MutationWire` explicit-key renderer with `Value`-typed fields | op-io | Superseded by `MutationCore` + typed enums under the corrected acceptance bar (§1.4). Keeping a hand-rolled renderer forever forfeits "core types are the contract" |
| Runtime-state families exempt from typed outputs | op-io | The D12 inversion closes the hole; accepted churn |
| Untyped `raw: Value` plugin input envelopes | op-io | v2's D10 typed `PluginEnsureInput`/`PluginStatusInput` end F9 (`&Value` penetrating two layers into eos-operation); only the dynamic `plugin.<id>.*` payload stays `Value` by nature |

### 1.4 The one bar change, stated honestly

The parents disagreed because they held different acceptance bars.
unified-op-io self-imposed **byte-order identity** on mutation responses and
therefore had to relocate serializer bodies verbatim. v3 adopts v2's bar —
the one the conformance suites actually enforce:

- Requests and **error envelopes: byte-identical.**
- Success responses: **canonical-equal** (`cargo xtask check-contract`, all
  suites). Key **set**, values, null-vs-absent, and array order are the
  contract; object key order is not.
- Every wire-visible **string** is pinned by construction: parse-error
  spellings, vocabulary enum spellings, `error.kind` set, details shapes.
- `ops.json`: byte-identical in every phase.

Named consequences of giving up byte-order identity (deterministic order is
kept — `preserve_order` + struct declaration order — it just may differ from
today's hand-insertion order): the `final.json` crash artifact's key order
may change (same-version write/read; recovery is unaffected; the session
byte-test is rebuilt as a canonical test + round-trip), and byte-asserting
daemon unit tests are rebuilt as canonical assertions. Fixtures never change;
one fixture is **added** (§10).

## 2. Final layout

```
crates/eos-operation/src/
├── lib.rs                      re-exports core contracts
├── core/
│   ├── lib.rs                  facade
│   ├── catalog.rs              ← renamed from ops.rs; + from_op_name();
│   │                             contract() macro-generated; #[non_exhaustive] dropped
│   ├── request.rs              + NEW  OpRequest (28 variants), RequestError, ArgsError,
│   │                             the five parse helpers — delegates payload parsing
│   │                             to the family contract.rs files (~150 LOC, size watch resolved)
│   ├── response.rs             + NEW  OpResponse, OpEnvelopeError,
│   │                             OpEnvelopeErrorKind
│   ├── workspace_outcome.rs    ~ RENAMED/RESTRUCTURED  MutationCore,
│   │                             WorkspaceMutationOutcome, WorkspaceConflict,
│   │                             WorkspaceKind, MutationStatus,
│   │                             ChangedPathKind(+s), WorkspaceTimings
│   ├── audit.rs                + NEW  MutationSource
│   ├── error.rs                + NEW  OpError { kind, message, details }
│   └── id.rs                   + NEW  CallerId, InvocationId, CommandSessionId
├── file/         contract.rs (+NEW: Input/Output DTOs + parse fns),
│                 lib.rs, direct.rs, isolated.rs, tests.rs
├── checkpoint/   contract.rs (CommitInput/CommitOutput move here), lib.rs, commit.rs
├── command/      contract.rs (relocated DTOs + CommandMetadata + CommandStatus),
│                 lib.rs, outcome.rs, prepare.rs, registry.rs, runtime.rs,
│                 service.rs, settle.rs
├── plugin/       contract.rs (PluginEnsureInput/PluginStatusInput/outputs),
│                 + existing modules
├── control/      contract.rs ONLY   (DTOs + parse; behavior stays daemon — D12)
├── isolation/    contract.rs ONLY
└── workspace_run/ contract.rs ONLY

DELETED in eos-operation: all 8 family ops.rs (229 LOC), file/port.rs,
  the sandbox/ module entirely (host family has no daemon schema)
DELETED in eos-daemon: OpTable/Handler/fn_addr_eq/boot panic, builtin_handlers.rs,
  runtime/request_args.rs string helpers, GuardedResponse, mutation_response
  re-keying, error_json/require_arg producers, per-op hand parsing in op_adapter/*
CHANGED in eos-layerstack: CommitStatus drops #[non_exhaustive] (all consumers
  in-workspace; same argument as BuiltinOp)
```

```rust
// core/lib.rs
pub mod catalog;
pub mod request;

mod audit;
mod error;
mod id;
mod response;
mod workspace_outcome;

pub use audit::MutationSource;
pub use error::OpError;
pub use id::{CallerId, CommandSessionId, InvocationId};
pub use response::{OpEnvelopeError, OpEnvelopeErrorKind, OpResponse};
pub use workspace_outcome::{
    ChangedPathKind, ChangedPathKinds, MutationCore, MutationStatus, WorkspaceConflict,
    WorkspaceKind, WorkspaceMutationOutcome, WorkspaceTimings,
};
```

`core/request.rs` stays small because payload structs and their parse fns
live in each family's `contract.rs` (real ownership boundaries); the enum,
the shared helpers, and the `parse` entry point are the only core-owned parts.

## 3. Request lifecycle

```
{op, invocation_id, args}
   │ dispatcher: envelope shell checks (unchanged, OUTSIDE the funnel — raw messages)
   ▼
BuiltinOp::from_op_name(op)                      ── catalog, macro-generated
   │  None ──────────────────────────────► "plugin." registry fallback (raw Value,
   │  Err(NotDaemonServed) ──────────────►  gate-before-route unchanged), else
   ▼                                        unknown_op envelope (details {"op": name})
OpRequest::parse(op, &args)                      ── ONE parse site (core/request.rs →
   │  Err(Args) ── family→channel map ──►   family contract.rs), wire-pure inputs
   ▼
builtin::dispatch(request, ctx) ── exhaustive match, sync, same spawn_blocking
   │   arms: inject policy (FileLimits from ctx; command defaults from the
   │   process-global config), route on caller + daemon state (F11), call the
   │   behavior fn (eos-operation) or runtime service (daemon — D12 families)
   ▼
Result<TypedOutput, FamilyError> ── From → OpError (refusal families)
   │                                or OpEnvelopeError (envelope families)
   ▼
OpResponse { Success(serde::to_value(output)) | Refused(OpError) | Envelope(OpEnvelopeError) }
   ▼
into_wire() ── the sole renderer pair for both error shapes
   ▼
dispatcher finalize: runtime.* timings splice (unchanged), encode (unchanged)
```

## 4. Unified input — `core/request.rs` + family `contract.rs`

```rust
// core/request.rs
pub const DEFAULT_CALLER_ID: &str = "default";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgsError { pub key: &'static str, pub problem: ArgProblem }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ArgProblem {
    Required,             // "{key} is required"
    MustBeString,         // "{key} must be a string"
    MustBeNonEmpty,       // "{key} must be non-empty"
    MustBeList,           // "{key} must be a list"
    Invalid(String),      // verbatim: serde edit errors, "paths must be strings",
                          // "edit anchor old_text must be non-empty", "last_n_lines is too large"
}

#[derive(Debug)]
pub enum RequestError {
    /// Host-served catalog op. There is NO served_by pre-guard in the daemon;
    /// parse is the single authority, and the daemon routes this through the
    /// same fallback path as an unresolved name — byte-identical to today.
    NotDaemonServed(BuiltinOp),
    Args(ArgsError),
}

#[derive(Debug)]
pub enum OpRequest {
    RuntimeReady(RuntimeReadyInput),
    InvocationHeartbeat(HeartbeatInput),
    InvocationCancel(CancelInvocationInput),
    InflightCount(CallerCountInput),
    LayerMetrics(LayerMetricsInput),
    EnsureWorkspaceBase(EnsureBaseInput),
    BuildWorkspaceBase(BuildBaseInput),
    CommitToWorkspace(CommitToWorkspaceInput),
    CommitToGit(CommitInput),
    WorkspaceBinding(BindingInput),
    ReadFile(ReadFileInput),
    WriteFile(WriteFileInput),
    EditFile(EditFileInput),
    PluginEnsure(PluginEnsureInput),
    PluginStatus(PluginStatusInput),
    IsolatedWorkspaceEnter(IsolationEnterInput),
    IsolatedWorkspaceExit(IsolationExitInput),
    IsolatedWorkspaceStatus(IsolationStatusInput),
    IsolatedWorkspaceListOpen,
    IsolatedWorkspaceTestReset,
    ExecCommand(ExecCommandInput),
    WriteStdin(WriteStdinInput),
    CommandReadProgress(ReadProgressInput),
    CommandCancel(CancelCommandInput),
    CommandCollectCompleted(CollectCompletedInput),
    CommandSessionCount(CallerCountInput),
    CancelWorkspaceRunsByCaller(RunEndInput),
    CancelWorkspaceRuns(RunCancelAllInput),
}

impl OpRequest {
    /// Within-crate exhaustive match over BuiltinOp (no `_` arm): a new
    /// catalog row fails to compile until it gains a parse arm here and a
    /// dispatch arm in the daemon. Payload parsing delegates to the owning
    /// family's contract.rs. Unknown keys always ignored (host stamps
    /// `_eos_daemon_protocol_version`; e2e injects extra keys).
    pub fn parse(op: BuiltinOp, args: &serde_json::Value) -> Result<Self, RequestError>;
}
```

`OpRequest` is **not** `#[non_exhaustive]` — the daemon's exhaustive match IS
the totality guarantee. Honesty note (carried): the guarantee is weak-form on
the daemon side — a new row always forces a parse arm; it forces a dispatch
arm only when it introduces a new variant. The conformance suites remain the
behavioral gate.

Representative family contract (inputs are wire projections — no config, no
context; `Option` for everything the route or runtime decides):

```rust
// file/contract.rs
#[derive(Debug, Clone)]
pub struct WriteFileInput {
    pub path: String,                       // trim + require — "path is required"
    pub content: String,                    // exact bytes (require_raw_string semantics)
    pub overwrite: bool,                    // default true
    pub caller: CallerId,                   // owns default-before-trim (§8)
    pub layer_stack_root: Option<PathBuf>,  // route-conditional: NOT required at parse
}

#[derive(Debug, Clone)]
pub struct EditFileInput {
    /// Parsed BEFORE `path` — preserves today's error precedence
    /// (op_adapter/files.rs:218-238; `{}` args ⇒ "edits must be a list").
    pub edits: Vec<SearchReplaceEdit>,
    pub path: String,
    pub caller: CallerId,
    pub layer_stack_root: Option<PathBuf>,
}

impl WriteFileInput {
    pub(crate) fn parse(args: &serde_json::Value) -> Result<Self, ArgsError>;
}
```

```rust
// plugin/contract.rs — D10: &Value stops penetrating ensure/overlay/dispatch (F9).
// Defaults verified against live code (plugin.rs:27-53): start_services FALSE;
// probe_services FALSE and probe_timeout_ms are STATUS-only args.
pub struct PluginEnsureInput {
    pub plugin: Option<String>,
    pub digest: Option<String>,
    pub manifest: Option<serde_json::Value>,  // the genuinely dynamic part
    pub start_services: bool,                 // default false
    pub caller: CallerId,
}
pub struct PluginStatusInput {
    pub probe_services: bool,                 // default false
    pub probe_timeout_ms: Option<u64>,
}
// Dynamic `plugin.<id>.*` payloads stay Value — runtime-discovered, outside
// the static schema by design.
```

**Binding parse rules** (all carried from the verified record):

- Per-op key-check ORDER is transcribed verbatim, never reordered to
  struct-field order. Named pins: edits-before-path; `command.poll` checks
  `command_session_id` before the `last_n_lines` "too large" conversion;
  checkpoint `{}`-args ⇒ "layer_stack_root is required".
- The five string semantics keep one owner (`ArgsError` + helpers): trimmed
  required / raw bytes / command non-blank / non-empty / untrimmed-with-default
  (exec `invocation_id`, historical default `"exec_command"` — the envelope id
  is dropped by the transport, documented quirk).
- Command timeout alias (`timeout` | `timeout_seconds`) resolves at parse;
  the default value injects in the dispatch arm from the process-global
  `command_session_config()` (NOT `DispatchContext` — verified).
- Defaults pinned: `overwrite` true, `last_n_lines` 50, plugin flags false.
- Route-conditional fields are never required at parse; `MissingLayerStackRoot`
  is raised in the dispatch arm after the binding probe, same string, same
  ordering. (`CommitInput` legitimately requires `layer_stack_root` at parse —
  commit ops have no route decision.)

**Per-family parse-failure channel** (today's two conventions, made data):

| Family | Channel | Bytes |
|---|---|---|
| IsolatedWorkspace, WorkspaceRun | `OpResponse::Refused` — in-band `{"success":false,"error":{"kind":"invalid_argument","message":"{key} is required","details":{"key":…}}}` | identical to `op_adapter::require_arg` |
| Control, Checkpoint, Files, CommandSession, Plugins | `OpResponse::Envelope` via `OpEnvelopeError::invalid_envelope(helper string)` — preserves the `invalid envelope: ` Display prefix | identical to today's handler-path parse errors |

## 5. Unified output — typed DTOs, serde serialization is the wire

Every daemon-served op gets a typed `*Output` in its family `contract.rs`
(complete signature table in §9). The render seam is serde itself: outputs
carry their own `success` field (directly or via `MutationCore`), and
`OpResponse::Success(serde_json::to_value(output))` is the only success path.
There is no output trait and no hand-rolled renderer — v2's `output_value`
hook and op-io's `MutationWire` both dissolve.

```rust
// core/workspace_outcome.rs — F7/F10 resolved: one definition of the mutation block,
// typed vocabulary, serialization IS the wire (canonical bar, §1.4).

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkspaceKind { Ephemeral, Isolated }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationStatus {
    // closed 7-variant union, verified: file-op literals "rejected" /
    // "aborted_overlap" (file/lib.rs:192,222-247) + CommitStatus passthrough
    // "accepted"/"committed"/"aborted_version"/"dropped"/"failed"
    // (eos-layerstack/src/commit/mod.rs:57-80). From<CommitStatus> conversion;
    // layerstack drops #[non_exhaustive] so the From has no wildcard.
    Accepted, Committed, Rejected, AbortedVersion, AbortedOverlap, Dropped, Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChangedPathKind { Write, Delete, Symlink, OpaqueDir }
pub type ChangedPathKinds = BTreeMap<String, ChangedPathKind>;
pub type WorkspaceTimings = BTreeMap<String, serde_json::Value>;

/// The shared mutation block: file ops, command settlement, and plugin
/// overlays all converge on it (keyed by behavior, not family).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MutationCore {
    pub success: bool,
    pub changed_paths: Vec<String>,
    pub changed_path_kinds: ChangedPathKinds,
    #[serde(serialize_with = "serialize_mutation_source")]
    pub mutation_source: Option<MutationSource>, // None renders "" (discarded settlements)
    pub conflict: Option<WorkspaceConflict>,     // None renders explicit null
    pub conflict_reason: Option<String>,         // None renders explicit null
    pub timings: WorkspaceTimings,
}

// serialize_mutation_source(Some(x)) => x's pinned snake_case spelling;
// serialize_mutation_source(None) => "".
// There is no wire-valid null mutation_source in this contract.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceMutationOutcome {
    #[serde(flatten)]
    pub core: MutationCore,
    #[serde(rename = "workspace")]               // wire key pinned (F7)
    pub workspace_kind: WorkspaceKind,
    pub published: bool,
    pub status: MutationStatus,
    #[serde(serialize_with = "serialize_null")]
    pub error: (),                              // always literal null, F7
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub applied_edits: Option<i64>,             // None for write, Some(count) for edit
}
```

`WorkspaceMutationOutcome` is intentionally output-only: its field order is
kept deterministic for debugging, but §1.4's canonical-equality bar makes key
membership and values, not object key order, the contract. The explicit
`error: null` field is not a renderer escape hatch; it is the typed owner of a
legacy wire member that remains required by daemon fixtures.

Output ownership map:

| Output | Owner | Replaces |
|---|---|---|
| `WorkspaceMutationOutcome` (write/edit) | core | `GuardedResponse` + `mutation_response` field-copy + `workspace_kind`→`workspace` re-keying (deleted) |
| `ReadFileOutput`, `CommitOutput`, checkpoint outputs | family `contract.rs` | adapter `json!` shapers (`read_response`, `commit_response`) |
| `CommandResponse` + `CommandMetadata` (§7.3) | `command/contract.rs` | the F6 key-fishing round-trip |
| control/isolation/workspace_run outputs | family `contract.rs` (D12) | adapter-inline `json!` shapes |
| `PluginEnsureOutput` (enum `NeedsUpload` \| `Ready`), `PluginStatusOutput` | `plugin/contract.rs` | adapter shaping |
| plugin-overlay response synthesis | **stays daemon-owned** — reads/builds `MutationCore` instead of fishing keys; `"plugin_overlay"` spelling stays a daemon literal; post-splices (runner shell fields, `plugin_result`, status rewrite) unchanged in order | hand re-keying |

**Order-sensitive carve-out (verified wire contract):**
`plugin_overlay.changed_paths` is a JSON **array in upperdir capture order**
(capture DFS ≠ lexicographic). The overlay outcome keeps a capture-ordered
`Vec<(String, ChangedPathKind)>` — the kind is typed, the container is not
re-sorted into a `BTreeMap`. Asserted by an e2e order test in P6.

## 6. `core/audit.rs`, `core/error.rs`, `core/id.rs`

```rust
// audit.rs — v1 §5 verbatim + serde spellings
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationSource { ApiWrite, ApiEdit, IsolatedWorkspace, OverlayCapture }
// "plugin_overlay" stays a daemon literal. FileBackend::workspace_kind() →
// WorkspaceKind and ::mutation_source() → MutationSource (trait file/lib.rs:85
// + 3 impls); command/settle.rs:74,119 literals → enum.

// error.rs — F5 + F8: one boundary type, three producers → one renderer pair
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{message}")]
pub struct OpError {
    pub kind: &'static str,        // "invalid_request", "invalid_argument",
                                   // "active_background_work", …
    pub message: String,
    pub details: Option<serde_json::Value>,
}
// Re-exports: pub use core::OpError as FileOpsError (file), as
// WorkspaceApiError (command). kind stops being discarded; refusals that
// today travel as Ok(error_json(…)) become OpResponse::Refused(OpError) with
// byte-identical rendering. The command not-found synthetic is NOT an error —
// it stays a CommandResponse-shaped output. Family-internal enums
// (CheckpointError, PluginRuntimeError/PpcError, IsolatedError) stay
// heterogeneous; they map via From at the op boundary.

// id.rs — F14: nothing below owns identity types; conversion at the
// substrate boundary, no dependency changes.
pub struct CallerId(String);          // OWNS default-before-trim (§8)
pub struct InvocationId(String);      // heartbeat invocation_ids, cancel, exec quirk
pub struct CommandSessionId(String);  // lands with the command relocation
// new/as_str/Display + serde transparency. Substrate crates keep String fields.
```

## 7. The operation-owned response funnel and the daemon edge

### 7.1 `OpResponse`

```rust
// eos-operation/src/core/response.rs
pub enum OpResponse {
    /// serde_json::to_value of a typed *Output. Dispatcher splices runtime.*
    /// timings after.
    Success(Value),
    /// In-band refusal through the success channel — {"success":false,"error":
    /// {kind,message,details}} WITHOUT warnings/timings; byte-identical to
    /// today's error_json convention.
    Refused(OpError),
    /// Envelope failure — {"success":false,"warnings":[],"timings":{},
    /// "error":{…}} rendered by the same contract-owned kind vocabulary as
    /// today's daemon ErrorKind.
    Envelope(OpEnvelopeError),
}

pub struct OpEnvelopeError {
    pub kind: OpEnvelopeErrorKind,
    pub message: String,
    pub details: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OpEnvelopeErrorKind {
    InvalidEnvelope,
    BadJson,
    RequestTooLarge,
    Unauthorized,
    UnknownOp,
    InternalError,
    Forbidden,
    ForbiddenInIsolatedWorkspace,
    LifecycleInProgress,
}
```

Deliberately OUTSIDE the funnel (verified expressiveness gaps): envelope
shell-check errors (raw messages, no Display prefix), the unknown_op envelope
(`details {"op": name}`, byte-pinned by fixture), and the `plugin.*` registry
fallback — all early-return finished `Value`s through `finalize`, exactly as
today. The **five** `#[expect(clippy::unnecessary_wraps)]` handlers
(`control.rs:65,92,119`, `command.rs:142,159`) become honest infallible
returns.

`DaemonError` stays daemon-internal. Builtin dispatch arms convert it into
`OpEnvelopeError` before returning an `OpResponse`, so `eos-operation` owns the
response contract without depending on `eos-daemon`. The daemon still owns final
transport duties: shell-check early returns, plugin fallback/unknown-op
short-circuiting, runtime timing splices, and newline framing.

### 7.2 Dispatcher

```rust
// eos-daemon/src/dispatch/dispatcher.rs — OpTable deleted
pub fn dispatch_with_context(request: &Request, context: DispatchContext<'_>) -> Value {
    // shell checks unchanged, outside the funnel
    let response = match BuiltinOp::from_op_name(&request.op) {
        Some(op) => match OpRequest::parse(op, &request.args) {
            Ok(parsed) => builtin::dispatch(parsed, context),
            Err(RequestError::Args(err)) => parse_error_response(op, err),
            Err(RequestError::NotDaemonServed(_)) => {
                return finalize(plugin_fallback_or_unknown(request, context));
            }
        },
        None => return finalize(plugin_fallback_or_unknown(request, context)),
    };
    finalize(response.into_wire())
}
```

```rust
// eos-daemon/src/dispatch/builtin.rs — replaces builtin_handlers.rs
pub(crate) fn dispatch(request: OpRequest, context: DispatchContext<'_>) -> OpResponse {
    match request {   // exhaustive, no wildcard; sync; same spawn_blocking
        OpRequest::WriteFile(input) => files::write_file(input, context),
        OpRequest::IsolatedWorkspaceEnter(input) => isolation::enter(input, context),
        // ...
    }
}
```

Dispatch arms keep all behavior: `FileLimits` from `ctx.file_limits()`;
command defaults from the process-global config; binding-probe-then-
`MissingLayerStackRoot` ordering with identical error text; post-op
`workspace.touch`; the exec isolated-branch `binding.caller_id` overwrite of
the parsed value (one explicit post-parse line, `op_adapter/command.rs:103-113`);
plugin PPC gate before any use of lifted fields.

### 7.3 Command contract relocation

Mechanics carried from both parents. `eos-command-session/src/contract.rs` op
DTOs move to `eos-operation/src/command/contract.rs` (renamed per D5:
`ExecCommandInput`, `WriteStdinInput`, `ReadProgressInput`,
`CancelCommandInput`, `CollectCompletedInput`); the substrate consumes a
pre-rendered `Value` only (`persist_final(&serde_json::Value)`,
`session.rs:242,295`); `tail_lines` widens to `pub` and **stays** in the
substrate (transcript.rs consumes it; moving it creates a cycle);
`CommandSessionError` **stays** in the substrate (`session.rs:15,156,291`);
the private `u64_to_f64_saturating` copy in `settle.rs:365-370` moves to
`command/contract.rs` as `pub(crate)` (the daemon keeps its own — no third
copy).

```rust
pub enum CommandStatus { Running, Ok, Cancelled, Error, TimedOut }  // spellings pinned

pub struct CommandMetadata {
    #[serde(flatten)]
    pub core: MutationCore,
    pub workspace: WorkspaceKind,        // workspace-without-metadata unrepresentable
    /// Spliced into the response top level (today's nested "metadata" object:
    /// isolated_workspace + warnings keys, settle.rs:103-112). Membership
    /// pinned by the fixture round-trip test.
    pub extras: serde_json::Map<String, serde_json::Value>,
}

pub struct CommandResponse {
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    pub command_session_id: Option<CommandSessionId>,
    pub settled: Option<CommandMetadata>,   // replaces {workspace: Option<String>, metadata: Value}
}
```

`to_wire_value()` becomes serialization instead of key-fishing
(`contract.rs:120-177` today re-reads `settle.rs:166-175`'s `json!` blob by
string key with per-key fallbacks). `CommandMetadata` deliberately does
**not** reuse `WorkspaceMutationOutcome` (no `published`/`status`; carries
`extras`) — composition over mega-struct, both parents agree.
`eos-command-session/tests/unit/session.rs` is **rebuilt** (it constructs the
workspace-Some/metadata-Null state the typed shape makes unrepresentable, and
relies on a `to_wire_value` success fallback dead in production). The
round-trip test covers: the `metadata.metadata` extras splice, the
`mutation_source` None ⇒ `""` rendering, the discarded-response case, and
`final.json` canonical equality.

## 8. `CallerId` — the routing-semantics owner

Two adversarial rounds independently flagged caller-id handling as the
likeliest silent wire break. v3 makes the type own it:

```rust
impl CallerId {
    /// Wire semantics: default-BEFORE-trim. Absent or non-string ⇒ "default".
    /// A present string is trimmed and kept — whitespace-only becomes "",
    /// which is a DIFFERENT isolated-binding routing key than "default".
    /// (op_adapter/mod.rs:44-50 today; unit-pinned in P4, including the echo
    /// cases: inflight_count / command.count echo the trimmed string back.)
    pub fn from_wire(args: &serde_json::Value) -> Self;
    pub fn as_str(&self) -> &str;
}
```

Every payload that routes or echoes caller identity carries `CallerId`; the
fallback literal lives in exactly one place.

## 9. The operation signature table (complete, from v2 §10)

Field sets are today's wire keys, verbatim (canonical bar). `Input` parse fns
and `Output` structs live in the named `contract.rs`.

| Op | Input | Output | Behavior |
|---|---|---|---|
| **control/contract.rs** | | | daemon runtime |
| `sandbox.runtime.ready` | `RuntimeReadyInput { layer_stack_root }` | `RuntimeReadyOutput { ready, probes, daemon_pid, uptime_s, timings }` | |
| `sandbox.call.heartbeat` | `HeartbeatInput { invocation_ids: Vec<InvocationId> }` | `HeartbeatOutput { touched }` | |
| `sandbox.call.cancel` | `CancelInvocationInput { invocation_id }` | `CancelInvocationOutput { invocation_id, cancelled, already_done, cleanup_done }` | |
| `sandbox.call.count` | `CallerCountInput { caller: CallerId }` *(shared)* | `InflightCountOutput { caller_id, count }` | |
| **checkpoint/contract.rs** | | | eos-operation + layerstack |
| `sandbox.checkpoint.layer_metrics` | `LayerMetricsInput { layer_stack_root }` | `LayerMetricsOutput` (wire-pinned metrics) | |
| `sandbox.checkpoint.ensure_base` | `EnsureBaseInput { layer_stack_root, workspace_root }` | `WorkspaceBaseOutput { created, binding, timings }` *(shared)* | |
| `sandbox.checkpoint.build_base` | `BuildBaseInput { layer_stack_root, workspace_root, reset }` | `WorkspaceBaseOutput` | |
| `sandbox.checkpoint.commit_to_workspace` | `CommitToWorkspaceInput { layer_stack_root, workspace_root }` | `CommitToWorkspaceOutput { manifest_version, timings }` | |
| `sandbox.checkpoint.commit_to_git` | `CommitInput { layer_stack_root, workspace_root, message, paths }` (custom `null\|str\|[str]` deserializer kept) | `CommitOutput { committed, commit_sha, manifest_version, manifest_root_hash, paths, worktree_mode, timings }` (timings stay `BTreeMap<String,f64>` — wire-mirror, upheld) | |
| `sandbox.checkpoint.binding` | `BindingInput { layer_stack_root }` | `BindingOutput { binding }` | |
| **file/contract.rs** | | | eos-operation::file |
| `sandbox.file.read` | `ReadFileInput { path, caller, layer_stack_root? }` | `ReadFileOutput { workspace: WorkspaceKind, success, content, exists, encoding, timings }` | |
| `sandbox.file.write` | `WriteFileInput { path, content, overwrite, caller, layer_stack_root? }` | `WorkspaceMutationOutcome` | |
| `sandbox.file.edit` | `EditFileInput { edits, path, caller, layer_stack_root? }` | `WorkspaceMutationOutcome` | |
| **plugin/contract.rs** | | | eos-operation::plugin |
| `sandbox.plugin.ensure` | `PluginEnsureInput` (§4) | `PluginEnsureOutput` (enum `NeedsUpload` \| `Ready`) | |
| `sandbox.plugin.status` | `PluginStatusInput` (§4) | `PluginStatusOutput { loaded_plugins, running_service_processes, … }` | |
| *(dynamic `plugin.<id>.*`)* | `Value` by design | `PluginDispatchOutcome` unchanged | |
| **command/contract.rs** | | | eos-operation::command |
| `sandbox.command.exec` | `ExecCommandInput { cmd, caller, layer_stack_root?, timeout?, yield_time_ms?, invocation_id? }` | `CommandResponse` (§7.3) | |
| `sandbox.command.write_stdin` | `WriteStdinInput { command_session_id, chars, yield_time_ms? }` | `CommandResponse` | |
| `sandbox.command.poll` | `ReadProgressInput { command_session_id, last_n_lines }` (id checked first — pin) | `CommandResponse` | |
| `sandbox.command.cancel` | `CancelCommandInput { command_session_id }` | `CommandResponse` | |
| `sandbox.command.collect_completed` | `CollectCompletedInput { command_session_ids?, caller? }` | `CollectCompletedOutput { success, completions }` | |
| `sandbox.command.count` | `CallerCountInput` *(shared)* | `CommandSessionCountOutput { caller_id, count }` | |
| **isolation/contract.rs** | | | daemon WorkspaceRuntime (D12) |
| `sandbox.isolation.enter` | `IsolationEnterInput { caller, layer_stack_root }` | `IsolationEnterOutput { manifest_version, manifest_root_hash, workspace_handle_id, workspace_root }` | |
| `sandbox.isolation.exit` | `IsolationExitInput { caller, grace_s? }` | `IsolationExitOutput { evicted_upperdir_bytes, lifetime_s, total_ms, phases_ms, inspection }` | |
| `sandbox.isolation.status` | `IsolationStatusInput { caller }` | `IsolationStatusOutput` (enum `Open{…}` \| `Closed`; wire `{open:false}` pinned) | |
| `sandbox.isolation.list_open` | *(unit variant)* | `ListOpenOutput { open_caller_ids }` | |
| `sandbox.isolation.test_reset` | *(unit variant)* | `TestResetOutput { reset, exited_callers }` | |
| **workspace_run/contract.rs** | | | daemon WorkspaceRuntime (D12) |
| `sandbox.run.end` | `RunEndInput { caller, grace_s? }` | `RunEndOutput { caller_id, cancelled_command_sessions, isolated_exited }` | |
| `sandbox.run.cancel_all` | `RunCancelAllInput { grace_s? }` | `RunCancelAllOutput { cancelled_command_sessions, isolated_callers_exited }` | |

## 10. Wire-invariance rules (binding for every phase)

1. **Acceptance bar**: §1.4. Requests + error envelopes byte-identical;
   success responses canonical-equal under all conformance suites; array
   order is contract; `ops.json` byte-identical.
2. **Pinned serde details**: `workspace` rename; explicit `"conflict": null`;
   mutation-source `None` renders `""`; the mutation response's literal
   `"error": null`; `published` is present on file mutation outputs; and
   `applied_edits` is absent for write, present for edit (including `0`).
   These are DTO fields/custom serializers, not daemon renderer branches,
   and P6 pins them against captured pre-refactor outputs. Inputs tolerate
   unknown keys; defaults per §4.
3. **`error_json` byte-compat**: each folded refusal keeps its exact rendered
   shape, including which shape carries `warnings`/`timings` (§7.1).
4. **Additive fixture**: `command_settle_conflict_response.json`, captured
   from PRE-refactor code as P7's first commit (only a conflict-bearing
   settled response pins the `conflict_file` membership question), wired into
   the explicit conformance include lists (both suites use explicit
   `fixture!`/`include_bytes!` lists — the host suite is unaffected).
5. **Trust boundary**: zero diff to `eos-sandbox-host` and
   `eos-sandbox-gateway` in every phase. The host runs **outside** the
   sandbox (v1 §10 inverted this); the no-dep rule survives on drift defense
   and the darwin-host portability constraint. The cross-boundary contract is
   `ops.json` + `contract/` fixtures, as data (F13).
6. **Rollback rule**: every phase is wire-invariant by construction; a
   failing conformance suite reverts the phase, never the fixtures
   (`contract/PROTOCOL.md` immutability rule).

## 11. Adversarial record — consolidated verdicts

| Objection (v1 §10 / round-2 verdicts) | v3 verdict | Deciding mechanism |
|---|---|---|
| "Defaults unavailable at deserialization" | **Overturned** | Wire-pure inputs; policy injects in dispatch arms (`ctx` / process-global config) |
| "Routing precedes validation" | **Overturned** | F11 verified; route-conditional fields `Option`, enforced post-probe with identical text |
| "Sync fn-pointer Handler ABI" | **Overturned** | `OpTable` deleted wholesale; still sync, same `spawn_blocking`; boot panic → compile error |
| "Error shaping differs per family" | **Overturned, typed** | `ArgsError` + family→channel map + `OpResponse`; all three encodings byte-preserved |
| Per-family enums "zero consumers" (F1) | **Overturned whole-surface** | Every `OpRequest` variant has a structural producer (parse arm) and consumer (dispatch arm) on every request's hot path |
| `Operation*` trait layer + markers + schema registry (v2 §5) | **Rejected** | No code generic over the bounds under either dispatcher — F1 at trait granularity; the enum is the binding |
| `MutationCore` flatten composition (v2 D8) | **Adopted** | Canonical bar (§1.4) makes the round-2 byte-order objection moot; round-trip tests pin membership |
| Typed IDs (v2 D7) | **Adopted, narrowed** | `CallerId` owns verified load-bearing semantics; `InvocationId`/`CommandSessionId` ride the typed DTO surface; substrates keep `String` (F14) |
| `CommandStatus` enum (v2 D11) | **Adopted** | Typing becomes possible exactly because the DTO moves above the substrate |
| D12 contract-ownership inversion | **Adopted** | eos-operation owns every daemon-served contract; daemon owns behavior — the clean boundary this spec exists to draw |
| `OpFamily::contracts()` | **Rejected** | Zero consumers |
| Unified outcome mega-struct | **Upheld rejection** | Composition only; `ReadFileOutput`/`CommitOutput`/plugin enums stay distinct; no dead optional fields |
| Shared envelope across the trust boundary | **Upheld rejection** | §10.5 |
| `AuditRecord`/journal revival | **Upheld rejection** | Removed 2026-06-11; live remainder is `MutationSource` |
| Checkpoint `f64` timings split | **Upheld** | Wire-mirror one-to-one |
| Dynamic `plugin.*` typed payloads | **Upheld rejection** | Open namespace is contract |

## 12. Migration plan

Each phase lands independently, wire-invariant under §10, verified before the
next. P4 is split to keep the tree green while the dispatcher swaps.

| Phase | Change | Verification |
|---|---|---|
| P1 | Delete 8 `ops.rs`, the 4 scaffold modules (reborn with `contract.rs` in P4/P6), `file/port.rs`; prune `src/lib.rs` AND family lib.rs decls (`file/lib.rs:15-16`, `command/lib.rs:3`, `checkpoint/lib.rs:19`, `plugin/lib.rs:3`) | `cargo check/test -p eos-operation`; rg gates incl. family-decl coverage; `cargo xtask check-contract` |
| P2 | `core/ops.rs` → `core/catalog.rs`; macro-generated `contract()`; `from_op_name`; drop `#[non_exhaustive]` on `BuiltinOp` AND `eos-layerstack::CommitStatus`; ~55-file import rename | `cargo check --workspace`; identity test (`ops.rs:313-318`); `cargo xtask check-contract` (ops.json byte-identical) |
| P3 | Vocabulary grounding: `core/audit.rs`, `core/error.rs` (with `details`), `core/id.rs`; outcome enums (`WorkspaceKind`, `MutationStatus`, `ChangedPathKind`) retype the existing structs (daemon copies call `.as_str()` interim); `FileBackend` retype; settle literals; `OpError` re-exports + `From`s | `cargo test -p eos-operation -p eos-daemon` (contract tests pin wire strings; kind-comparison test churn named); e2e isolated routing |
| P4a | `core/request.rs` + family `contract.rs` Input DTOs/parse for control/checkpoint/isolation/workspace_run (modules reborn); **parity test** (legacy-vs-new error strings/channels; named pins: edits-before-path, poll id-first, checkpoint `{}`-args, caller-id whitespace ⇒ `""`); temporary legacy-table fallback (named transitional scaffolding, deleted P4c) | `cargo test -p eos-daemon`; e2e registration sweep; isolation tier |
| P4b | files/command/checkpoint rewired through `OpRequest`; `SearchReplaceEdit` home settled; re-point `transport/server.rs:24,341` | daemon unit + phase2/phase3 + contract fixtures; e2e core + command tiers |
| P4c | plugin builtins rewired (typed inputs, F9 closed); delete `OpTable`/`builtin_handlers`/legacy helpers/`request_args` string fns; rewrite dispatcher unit tests | `cargo test -p eos-daemon`; `cargo xtask check-contract`; e2e plugin tier |
| P5 | `OpResponse` funnel; refusals → `OpResponse::Refused(OpError)` (fold `error_json`/`require_arg`); drop the five `unnecessary_wraps` expects | daemon unit + contract; e2e isolation + command NotFound; grep gate: no `error_json` producers remain |
| P6 | Output side: `MutationCore` restructure; serde-is-the-wire for file/checkpoint; `GuardedResponse` + `mutation_response` deleted; typed outputs for control/isolation/workspace_run/plugin (D12); plugin-overlay synthesis reads `MutationCore` (capture-order test); per-op round-trip tests vs captured pre-refactor outputs | `cargo test -p eos-daemon` (rebuilt canonical assertions); `cargo xtask check-contract`; full e2e |
| P7 | **First commit: capture the additive conflict fixture from pre-refactor code.** Then command relocation (§7.3): DTOs, `CommandMetadata`/`CommandStatus`/`CommandSessionId`, `persist_final(&Value)`, `tail_lines` pub, typed settle (exec caller-id overwrite preserved), helper home, session tests rebuilt | all three suites; round-trip vs both fixtures; `final.json` canonical test; e2e command + isolated tiers |
| P8 *(opt, severable)* | schemars: `input_schema`/`output_schema` per op emitted additively into `ops.json` → gateway validation / TypeScript `@eos/contracts` codegen — the schema layer's named future consumer | extended `xtask check-contract` drift gate |

## 13. Honest accounting

- **LOC**: the −242 deletion (P1) is severable and credits no design. Against
  live code, v3 is the largest of the three shapes: core/request.rs (~150) +
  seven family `contract.rs` files (~60–120 each, parse fns + typed outputs)
  + vocabulary/id/error modules (~150) against the deletion of all daemon
  hand-parsing, `GuardedResponse`, `error_json`/`require_arg`, and
  `request_args.rs`. Net roughly **+350–500 LOC**, none speculative — every
  type has a hot-path producer and consumer. eos-daemon itself ends clearly
  net-negative and behavior-only.
- **Churn**: dispatcher unit tests rewritten; phase2/phase3 entry points
  swapped; session unit tests rebuilt; byte-asserting tests rebuilt as
  canonical assertions. Accepted by directive.
- **Tracing**: one op end-to-end is `dispatcher → parse (family contract.rs)
  → dispatch arm → behavior → typed output → serde`. Every hop is a greppable
  `match` or a named type; zero `HashMap` indirection, zero hand-rolled
  renderers, zero stringly round-trips.
- **What the purchase buys**: compile-time catalog↔handler totality; one
  parse site; one render rule (serialization is the wire); one error
  boundary; one owner per wire spelling; typed vocabulary end-to-end; the
  contract for all 28 daemon ops owned by one crate; and a catalog ready to
  emit schemas (P8) without restructuring.
