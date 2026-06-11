# eos-operation Unified Op I/O Spec — Catalog-Derived `OpRequest`/`OpReply`

Status: **proposed** — design produced and adversarially verified read-only
(three competing designs, each refuted under daemon-feasibility, wire-compat,
and simplicity lenses; this spec is the wire-schema-first design with every
verifier amendment folded in). No code has been moved yet.

Relationship to `SPEC-eos-operation-core.md` (v1): supersedes its §9/§10
input/output posture and absorbs its adopted parts (scaffold deletions,
`MutationSource`, `OpError`, command contract relocation). v1's §10 rejections
are individually re-litigated in §9 below; rows that stand are restated there.

Decision record: the selected direction is **maximal unification regardless of
refactoring churn** — a single core-owned request enum, one parse site, one
reply funnel, `OpTable` deleted. The cheaper envelope-composition alternative
(keep the dispatcher, unify only the contract layer) was scored and rejected
by this directive, not on feasibility.

---

## 1. Philosophy, and the honest version of it

Model the daemon op surface as core-owned tagged enums derived from the same
source as `ops.json`; parse once at the edge, serialize once at the exit.
After confronting the codebase:

| Directive | What ships | Why the delta |
|---|---|---|
| `OpRequest` tagged enum, one variant per op | Yes — 28 variants, one per `ServedBy::Daemon` builtin, in `core/request.rs` | The 4 host ops cannot have variants (trust boundary, §8); dynamic `plugin.*` stays the untyped fallback (the open namespace is contract) |
| `#[serde(tag = "op")]` | No — `BuiltinOp::from_op_name` + hand-written per-op parse fns over shared helpers | Serde derive cannot reproduce the five wire-pinned string semantics (`is required` / `must be a string` / `must be non-empty` / bytes-preserving / trim-folding), per-op key-check order, or ignore-unknown-keys with caller args left untouched |
| Macro derives metadata AND variants | Macro derives metadata, `from_op_name`, and `contract()`; variant totality is enforced by within-crate exhaustive `match BuiltinOp` | Equal drift-proofing without a push-down-accumulation macro that still couldn't generate parse bodies. Honesty note (verifier): the totality guarantee is weak-form on the daemon side — a new catalog row always forces a `from_op_name` + parse arm (compile error), but forces a new dispatch arm only if the parse arm introduces a NEW `OpRequest` variant; mapping a new row onto an existing variant compiles silently. The conformance suites remain the behavioral gate |
| `OpResponse` enum + shared error envelope, core-owned | `OpReply` channel enum (daemon) + `MutationWire` renderer (core) + typed `CommandMetadata` (eos-operation) | The error envelope stays deliberately duplicated across the trust boundary (§8); success payloads stay per-op typed DTOs; what unifies is the *channel* and the one guarded-mutation envelope three families already share |
| Daemon parses once at the edge | Yes — `OpTable`/`Handler`/`fn_addr_eq` deleted; dispatch = name-resolve → parse → exhaustive match | Replacing `OpTable` wholesale is the accepted cost of the directive |

## 2. Flow, before and after

```
BEFORE
  bytes ─ decode ─ Envelope{op, invocation_id, args:Value}
        ─ OpTable: HashMap<String, fn(&Value, Ctx) -> Result<Value, DaemonError>>
        ─ 7 adapter modules each hand-fish args keys, inject config, route, shape json!
        ─ in-band errors = Ok(error_json(...)) by convention
        ─ dispatcher splices runtime.* timings, encodes

AFTER
  bytes ─ decode ─ Envelope (unchanged)
        ─ envelope shell checks (op non-empty, args is_object) — raw Values, outside the funnel
        ─ BuiltinOp::from_op_name(op)                          ── catalog (macro-generated)
        ─ OpRequest::parse(op, &args) -> typed variant          ── core/request.rs, ONE parse site
        │     Err(Args)            ─ family→channel map ─ OpReply::{Refused | Error}
        │     Err(NotDaemonServed) ─ same fallback path as an unresolved name (below)
        ─ builtin::dispatch(OpRequest, Ctx) -> OpReply          ── exhaustive match, sync
        │     arms: inject config from Ctx / process-global, route isolated-vs-direct,
        │     call behavior DTOs
        ─ OpReply::into_wire()                                  ── ONE channel funnel
        │     Success ── family renderers: MutationWire (core) /
        │                CommandResponse::to_wire_value (eos-operation) /
        │                json! (runtime-state families)
        ─ dispatcher splices runtime.* timings (unchanged), encodes (unchanged)
  miss  ─ "plugin." registry fallback (raw Value, gate-before-route unchanged)
        ─ else unknown_op envelope (unchanged, details {"op": name})
```

## 3. Final layout

```
crates/eos-operation/src/
├── lib.rs                       4 scaffold module decls removed
├── core/
│   ├── lib.rs                   facade
│   ├── catalog.rs               renamed from ops.rs; + from_op_name();
│   │                            contract() macro-generated; #[non_exhaustive] dropped.
│   │                            NO OpFamily::contracts() (zero consumers = F1 repeat)
│   ├── request.rs               NEW  OpRequest + 24 payload structs + ArgsError +
│   │                            parse helpers + DEFAULT_CALLER_ID + SearchReplaceEdit
│   ├── outcome.rs               + MutationWire + WorkspaceMutationOutcome::into_wire
│   ├── audit.rs                 NEW  MutationSource
│   └── error.rs                 NEW  OpError { kind, message }
├── checkpoint/  lib.rs, commit.rs                − ops.rs deleted
├── command/
│   ├── contract.rs              NEW  op DTOs moved from eos-command-session +
│   │                            CommandMetadata + WorkspaceMode
│   └── lib.rs, outcome.rs, prepare.rs,
│       registry.rs, runtime.rs, service.rs,
│       settle.rs                                 − ops.rs deleted
├── file/        lib.rs, direct.rs, isolated.rs,
│                tests.rs                         − ops.rs, port.rs deleted
└── plugin/      (unchanged except)               − ops.rs deleted

DELETED ENTIRELY: control/  sandbox/  isolation/  workspace_run/  (scaffold-only)
DELETED IN eos-daemon: dispatch table (OpTable/Handler/fn_addr_eq/boot panic),
  builtin_handlers.rs, runtime/request_args.rs string helpers, GuardedResponse
```

| Grounded item | From | Why core |
|---|---|---|
| `catalog.rs` rename + `from_op_name` + generated `contract()` | `core/ops.rs` (hand index map at :224-263) | Routing join key; deletes the positional-table coupling |
| `request.rs`: `OpRequest`, payloads, `ArgsError`, parse helpers, `DEFAULT_CALLER_ID` | `eos-daemon/src/runtime/request_args.rs:10-86` + parse blocks in 7 `op_adapter/*` modules + `op_adapter/mod.rs:44-50` | The wire-args vocabulary IS the op contract; one parse site, compile-time totality |
| `SearchReplaceEdit` | `file/lib.rs:111-117` (re-export kept in `file`) | The only serde-derived wire-args shape; belongs with the wire-args layer |
| `outcome.rs::MutationWire` + `WorkspaceMutationOutcome::into_wire` | `eos-daemon/src/runtime/response.rs:94-131` + `op_adapter/files.rs:251-274` | v1 §9 rule 3's "one core-owned shape" finally rendered by core, not a daemon-private struct |
| `audit.rs::MutationSource` | producers `file/direct.rs:32-37`, `file/isolated.rs:28-30`, `command/settle.rs:74,119` | Cross-module `"isolated_workspace"` collision; `"plugin_overlay"` stays a daemon literal |
| `error.rs::OpError` | `file/lib.rs:21-35`, `command/outcome.rs:6-28` (byte-identical `struct(String)` twins whose `new(kind, _)` discards `kind`) | One request-error shape; `kind` stops being discarded and is asserted by the parity/round-trip tests |
| `command/contract.rs` (not core, typed against core) | `eos-command-session/src/contract.rs` | Kills the v1-F6 stringly round-trip; substrate consumes a pre-rendered `Value` only |

## 4. Unified input — `core/request.rs`

```rust
//! Typed wire args for daemon-served builtin ops. Payloads are wire
//! projections: no config-injected fields; `Option` for everything the
//! route or runtime config decides. Parsing reads `args` by key and never
//! re-serializes it (unknown keys ignored — the host stamps
//! `_eos_daemon_protocol_version`, e2e injects extra keys).

pub const DEFAULT_CALLER_ID: &str = "default";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgsError {
    pub key: &'static str,
    pub problem: ArgProblem,
}

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
    /// Host-served catalog op. The daemon has NO served_by pre-guard; parse is
    /// the single authority, and the daemon routes this through the same
    /// fallback path as an unresolved name (plugin registry, then unknown_op)
    /// — byte-identical to today, where host names miss the OpTable.
    NotDaemonServed(BuiltinOp),
    Args(ArgsError),
}

#[derive(Debug)]
pub enum OpRequest {
    RuntimeReady(RuntimeReadyArgs),
    InvocationHeartbeat(HeartbeatArgs),
    InvocationCancel(CancelInvocationArgs),
    InflightCount(InflightCountArgs),
    LayerMetrics(LayerStackRootArgs),
    EnsureWorkspaceBase(WorkspaceBaseArgs),
    BuildWorkspaceBase(BuildWorkspaceBaseArgs),
    CommitToWorkspace(WorkspaceBaseArgs),
    CommitToGit(CommitToGitArgs),
    WorkspaceBinding(LayerStackRootArgs),
    ReadFile(ReadFileArgs),
    WriteFile(WriteFileArgs),
    EditFile(EditFileArgs),
    PluginEnsure(PluginEnsureArgs),
    PluginStatus(PluginStatusArgs),
    IsolatedWorkspaceEnter(IsolatedEnterArgs),
    IsolatedWorkspaceExit(IsolatedExitArgs),
    IsolatedWorkspaceStatus(IsolatedStatusArgs),
    IsolatedWorkspaceListOpen,
    IsolatedWorkspaceTestReset,
    ExecCommand(ExecCommandArgs),
    WriteStdin(WriteStdinArgs),
    CommandReadProgress(ReadProgressArgs),
    CommandCancel(SessionIdArgs),
    CommandCollectCompleted(CollectCompletedArgs),
    CommandSessionCount(SessionCountArgs),
    CancelWorkspaceRunsByCaller(CancelRunsByCallerArgs),
    CancelWorkspaceRuns(CancelAllRunsArgs),
}
```

`OpRequest` is **not** `#[non_exhaustive]`: eos-daemon (a foreign crate to
eos-operation) must match it exhaustively — that match IS the totality
guarantee. `BuiltinOp` drops its `#[non_exhaustive]` in the same spirit (P2):
all consumers are in-workspace (e2e uses it as a SKIP slice + iteration,
verified), so the attribute protects nobody and only forces wildcard arms.

```rust
impl OpRequest {
    /// Within-crate exhaustive match over BuiltinOp (no `_` arm): adding a
    /// catalog row fails to compile until it gains a parse arm here.
    pub fn parse(op: BuiltinOp, args: &serde_json::Value) -> Result<Self, RequestError> {
        match op {
            BuiltinOp::SandboxAcquire | BuiltinOp::SandboxRelease
            | BuiltinOp::SandboxStatus | BuiltinOp::SandboxList => {
                Err(RequestError::NotDaemonServed(op))
            }
            BuiltinOp::ReadFile => Ok(Self::ReadFile(ReadFileArgs::parse(args)?)),
            BuiltinOp::WriteFile => Ok(Self::WriteFile(WriteFileArgs::parse(args)?)),
            // ... one arm per remaining variant, no wildcard ...
        }
    }
}
```

Representative payloads (the rest are 1:1 transcriptions of their adapter's
current key reads, pinned by the P3 parity test):

```rust
#[derive(Debug, Clone)]
pub struct WriteFileArgs {
    pub path: String,                       // require_string semantics (trim + require)
    pub content: String,                    // require_raw_string (exact bytes)
    pub overwrite: bool,                    // default true
    pub caller_id: Option<String>,          // see caller_id rules below
    pub layer_stack_root: Option<PathBuf>,  // route-conditional: NOT required at parse
}

#[derive(Debug, Clone)]
pub struct EditFileArgs {
    /// Parsed BEFORE `path` — preserves today's error precedence
    /// (edit_request parses edits first, op_adapter/files.rs:218-238).
    pub edits: Vec<SearchReplaceEdit>,
    pub path: String,
    pub caller_id: Option<String>,
    pub layer_stack_root: Option<PathBuf>,
}

#[derive(Debug, Clone)]
pub struct ExecCommandArgs {
    pub cmd: String,                        // require_command_string: non-blank, bytes kept
    pub invocation_id: Option<String>,      // args-level quirk preserved; arm defaults "exec_command"
    pub caller_id: Option<String>,
    pub layer_stack_root: Option<PathBuf>,
    pub timeout: Option<u64>,               // "timeout" | "timeout_seconds" alias, in parse
    pub yield_time_ms: Option<u64>,         // default injected in the dispatch arm
}

/// Plugin builtins are opaque by design (ParsedEnsure::from_args + package
/// re-reads the whole object): typed envelope around a raw Value.
/// Defaults verified against live code: start_services defaults FALSE
/// (op_ensure); probe_services FALSE + probe_timeout_ms are STATUS-only args.
#[derive(Debug, Clone)]
pub struct PluginEnsureArgs {
    pub start_services: bool,               // default false; infallible lift
    pub raw: serde_json::Value,             // one clone; PPC gate runs on it in the arm
}

#[derive(Debug, Clone)]
pub struct PluginStatusArgs {
    pub probe_services: bool,               // default false
    pub probe_timeout_ms: Option<u64>,
    pub raw: serde_json::Value,
}

impl WriteFileArgs {
    #[must_use]
    pub fn caller_id(&self) -> &str {
        self.caller_id.as_deref().unwrap_or(DEFAULT_CALLER_ID)
    }
}
```

**caller_id rules (wire-pinned routing semantics, default-BEFORE-trim):**
absent or non-string → `None` (accessor yields `"default"`); present strings
are trimmed and stored as-is — a present whitespace-only value becomes
`Some("")`, which is a DIFFERENT isolated-binding routing key than
`"default"`. Parse must never normalize `Some("")` to `None`. Pinned by a P3
unit test, plus the echo cases (`inflight_count`, `command.count` echo the
trimmed string back on the wire).

**Parse-fidelity rules (binding for every payload):** per-op key-check ORDER
is transcribed verbatim from the adapter, never reordered to struct-field
order. Named pins in the P3 parity table: `edit_file` parses `edits` before
`path` (`{}` args ⇒ "edits must be a list"); `command.poll` checks
`command_session_id` before the `last_n_lines` "too large" conversion;
checkpoint `{}`-args ⇒ "layer_stack_root is required".

**Per-op error-channel ownership** (today's two conventions, made data):

| Family | Parse-failure channel | Bytes |
|---|---|---|
| IsolatedWorkspace, WorkspaceRun | in-band `{"success":false,"error":{"kind":"invalid_argument","message":"{key} is required","details":{"key":...}}}` | identical to `op_adapter::require_arg` |
| Control, Checkpoint, Files, CommandSession, Plugins | error envelope, `invalid_envelope` | wrapped as `DaemonError::InvalidEnvelope(helper string)` so the `invalid envelope: ` Display prefix is preserved — today's handler-path parse errors carry it |

## 5. Catalog changes — `core/catalog.rs`

```rust
impl BuiltinOp {
    /// Resolve a canonical wire spelling. Replaces the OpTable lookup.
    #[must_use]
    pub fn from_op_name(name: &str) -> Option<Self> { /* macro: $name => Self::$variant */ }

    /// Generated per-row (replaces the hand-indexed 38-line positional match
    /// at ops.rs:224-263, pinned identical by the existing identity test
    /// ops.rs:313-318).
    #[must_use]
    pub fn contract(self) -> &'static OpContract { /* generated */ }
}
```

`BUILTIN_OPS`, the 32 name constants, `PROTOCOL_VERSION`, and
`ops_json_document()` are byte-identical. `ops_json_document()` and the
`ops.json` artifact name are deliberately frozen (`eosd dump-ops`,
`cargo xtask check-contract`, gateway `include_str!`). `ops.json` does not
change in any phase.

## 6. Daemon edge — `OpTable` deleted

```rust
// eos-daemon/src/dispatch/dispatcher.rs
pub fn dispatch_with_context(request: &Request, context: DispatchContext<'_>) -> Value {
    // Envelope shell checks unchanged and OUTSIDE the funnel: they early-return
    // raw error_envelope Values through finalize with today's raw messages
    // ("op is required" / "args must be an object" — no DaemonError Display prefix).
    let reply = match BuiltinOp::from_op_name(&request.op) {
        Some(op) => match OpRequest::parse(op, &request.args) {
            Ok(parsed) => builtin::dispatch(parsed, context),
            Err(RequestError::Args(err)) => parse_error_reply(op, err),
            // Host-served names: same fallback as an unresolved name.
            Err(RequestError::NotDaemonServed(_)) => {
                return finalize(plugin_fallback_or_unknown(request, context));
            }
        },
        // Unknown names: "plugin." registry fallback first (raw Value,
        // gate-before-route unchanged), then the unknown_op envelope with
        // details {"op": name} — both built OUTSIDE the funnel, as today.
        None => return finalize(plugin_fallback_or_unknown(request, context)),
    };
    finalize(reply.into_wire())   // runtime.* timings splice unchanged
}
```

```rust
// eos-daemon/src/dispatch/builtin.rs — replaces builtin_handlers.rs
pub(crate) fn dispatch(request: OpRequest, context: DispatchContext<'_>) -> OpReply {
    match request {
        OpRequest::ReadFile(args)  => files::read_file(args, context).into(),
        OpRequest::WriteFile(args) => files::write_file(args, context).into(),
        OpRequest::IsolatedWorkspaceEnter(args) => isolation::enter(args, context),
        // ... exhaustive, no wildcard; sync; same spawn_blocking ...
    }
}
```

| Old mechanism | New mechanism |
|---|---|
| `HashMap<String, Handler>` lookup (`dispatcher.rs:26,33-35,102`) | `from_op_name` match, generated from the catalog table |
| boot-time panic: daemon op without handler (`dispatcher.rs:45-46`) | compile error: catalog row without a parse arm; dispatch arm forced when the row adds a variant (weak-form caveat in §1) |
| `fn_addr_eq` registration dedup (`dispatcher.rs:58-64`) | no registrations to police; deleted |
| sync fn pointer in `spawn_blocking` | sync free fn in the same `spawn_blocking` |
| `plugin.*` fallback after table miss (`dispatcher.rs:102-118`) | identical fallback after name-resolve miss / `NotDaemonServed` |

Dispatch arms keep all behavior: config injection — file byte caps from
`DispatchContext.file_limits()`; command timeout/yield defaults from the
process-global `eos_operation::command::command_session_config()` (NOT
`DispatchContext` — verifier correction); isolated-vs-direct routing on the
caller's binding **before** `MissingLayerStackRoot` with identical error text;
post-op `workspace.touch`; the exec isolated branch's explicit
`binding.caller_id` overwrite of the parsed value (one post-parse line, as
today, `op_adapter/command.rs:103-113`); family error mapping; plugin PPC gate
before any use of lifted fields (the lifts are infallible bools, so lifting at
parse is unobservable).

## 7. Unified output

### 7.1 `OpReply` — the channel funnel (daemon)

```rust
pub(crate) enum OpReply {
    /// Success payload object; dispatcher splices runtime.* timings after.
    Success(Value),
    /// In-band refusal returned as a successful dispatch — byte-identical to
    /// today's error_json convention (isolation/workspace_run arg misses,
    /// IsolatedError kinds with variant detail fields, the forbidden test gate).
    Refused { kind: &'static str, message: String, details: Value },
    /// Envelope failure rendered through DaemonError::wire_kind().
    Error(DaemonError),
}

impl From<Result<Value, DaemonError>> for OpReply { /* mechanical arm migration */ }
```

Deliberately NOT in the funnel (verifier-verified expressiveness gaps): the
envelope shell-check errors (raw messages, no Display prefix), the unknown_op
envelope (carries `details {"op": name}`, byte-pinned by fixture), and the
`plugin.*` registry fallback — all early-return finished `Value`s through
`finalize`, exactly as today. The command `NotFound` sentinel stays a
success-shaped output (it was never an error envelope). The **five**
`#[expect(clippy::unnecessary_wraps)]` handlers (control.rs
`op_cancel`/`op_heartbeat`/`op_inflight_count`, command.rs
`op_command_collect_completed`/`op_command_session_count`) become honest
infallible returns.

### 7.2 `MutationWire` — the one guarded-write renderer (core)

`GuardedResponse` relocates verbatim into `core/outcome.rs` — same fields,
same insertion order (`preserve_order` is enabled in both crates; insertion
order is wire order), same conditional key presence (`published` omitted when
`None`, `conflict`/`conflict_reason` explicit null, `"error": null` always,
`applied_edits` only when `Some`):

```rust
pub struct MutationWire {
    pub success: bool,
    pub published: Option<bool>,   // None => key omitted (changeset paths)
    pub workspace: String,
    pub changed_paths: Value,      // Value, NOT Vec — overlay capture order is contract
    pub changed_path_kinds: Value, // Value, NOT BTreeMap — same reason
    pub mutation_source: String,   // open String: daemon stamps "plugin_overlay"
    pub status: String,
    pub conflict: Option<Value>,   // this path renders conflict_file ALWAYS present
    pub conflict_reason: Option<String>,
    pub timings: Value,            // mutable until render (enrich_direct_timings)
    pub applied_edits: Option<i64>,
}

impl MutationWire {
    #[must_use] pub fn into_value(self) -> Value { /* GuardedResponse::into_json, byte-for-byte */ }
}

impl WorkspaceMutationOutcome {
    /// Direct file-op rendering (absorbs mutation_response + conflict_value).
    #[must_use] pub fn into_wire(self, applied_edits: Option<i64>) -> Value { /* ... */ }
}
```

The daemon's `guarded_changeset_response` (plugin overlay path) constructs
`core::MutationWire` as an **internal helper** — the existing post-splices
(runner shell fields, `changed_path_kinds` override, `plugin_result` /
`plugin_overlay` keys, status rewrite) mutate the returned `Value` in today's
order; `MutationWire` is never the terminal output there. Its dead
write/edit/exec_command verb-table arms (`response.rs:310-318`) are dropped —
the sole live caller passes `"plugin_overlay"`. The command-settle path keeps
its serde-embedded `WorkspaceConflict` (`conflict_file` skipped when `None`);
both conflict renderings now have exactly one documented owner each.
`#[serde(flatten)]` composition was considered and rejected: key order and
membership must stay byte-provable, not emergent from serde internals.

Explicitly NOT consolidated: `plugin_overlay.changed_paths` ordering. The
upperdir capture walk emits files before sibling dirs per level
(`eos-overlay` sorts per-directory by file name), so capture order ≠
lexicographic; that array's order is wire contract and stays a capture-ordered
`Vec<(String, String)>` in `PluginOverlayOutcome` — no `BTreeMap` retype.

### 7.3 Command contract — `command/contract.rs`

The op DTOs move from `eos-command-session/src/contract.rs` (the substrate
never constructs them; its only contact is `persist_final`, `session.rs:242,295`):

```rust
pub enum WorkspaceMode { Ephemeral, Isolated }   // "ephemeral" / "isolated"

pub struct CommandMetadata {
    pub workspace: WorkspaceMode,                // workspace-without-metadata unrepresentable
    pub success: bool,
    pub changed_paths: Vec<String>,
    pub changed_path_kinds: ChangedPathKinds,
    pub mutation_source: Option<MutationSource>, // None renders "" (discarded settlements)
    pub conflict: Option<WorkspaceConflict>,
    pub conflict_reason: Option<String>,
    pub timings: WorkspaceTimings,
    /// Spliced into the response top level (today's nested "metadata" object:
    /// isolated_workspace + warnings keys, settle.rs:103-112).
    pub extras: serde_json::Map<String, serde_json::Value>,
}

pub struct CommandResponse {
    pub status: String,                          // CommandStatus enum deferred (no consumer forces it)
    pub exit_code: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    pub command_session_id: Option<String>,
    pub settled: Option<CommandMetadata>,        // replaces {workspace: Option<String>, metadata: Value}
}
```

- `to_wire_value()` becomes serialization instead of key-fishing
  (`contract.rs:120-177` today re-reads the `json!` blob written by
  `settle.rs:166-175` by string key with per-key fallbacks); key assignment
  order matches the pre-refactor function exactly — crash-recovery
  `final.json` bytes are pinned.
- Substrate re-cut: `persist_final(final_wire: &serde_json::Value)`; the
  caller in `eos-operation::command` renders. `tail_lines` widens to `pub` but
  **stays in eos-command-session** (`transcript.rs` consumes it; moving it
  creates a crate cycle). `CommandSessionError` **stays in the substrate**
  (`session.rs` uses it internally at :15,156,291; moving it forces a reverse
  dep). The daemon's `NotFound` special-case keeps working.
- `u64_to_f64_saturating`: the private `settle.rs:365-370` copy moves to
  `command/contract.rs` as `pub(crate)`; the daemon keeps its own
  (`response.rs:10-15` has other consumers). No third copy.
- The round-trip test must cover: the `metadata.metadata` extras splice to top
  level, the `mutation_source` `""` (None) default, the discarded-response
  Null inner metadata, and `final.json` `to_vec_pretty` bytes — the existing
  session unit test asserts only `output.stdout`.
- `eos-command-session/tests/unit/session.rs` is **rebuilt**: it constructs
  the exact workspace-Some/metadata-Null state `CommandMetadata` makes
  unrepresentable, and relies on a `to_wire_value` success fallback that is
  dead in production.

### 7.4 `core/audit.rs` and `core/error.rs`

```rust
/// Canonical mutation_source strings recorded on mutation outcomes; wire
/// spellings asserted byte-for-byte by daemon contract tests. The daemon
/// additionally synthesizes "plugin_overlay" at its response layer; that
/// spelling is daemon-owned and intentionally absent here.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum MutationSource { ApiWrite, ApiEdit, IsolatedWorkspace, OverlayCapture }
// as_str(): "api_write" / "api_edit" / "isolated_workspace" / "overlay_capture"
// FileBackend::mutation_source returns MutationSource (trait file/lib.rs:88 +
// 3 impls); settle takes Option<MutationSource> — None renders today's "".
// Wire fields stay String.

/// One request-error shape. Display prints message only, matching both
/// predecessor types, so daemon error envelopes are byte-unchanged.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{message}")]
pub struct OpError { pub kind: &'static str, pub message: String }
// Re-exports: pub use core::OpError as FileOpsError (file/lib.rs),
//             as WorkspaceApiError (command/outcome.rs).
// From<OpError> for DaemonError => InvalidEnvelope(err.to_string());
// From<OpError> for CommandSessionError stays in command/outcome.rs
// (orphan-clean: OpError is crate-local). kind goes live: parity and
// round-trip tests assert it, and error-equality assertions now compare it
// (named test churn).
```

### 7.5 What deliberately stays per-op

`ReadFileOutcome`, `CommitOutcome` (wire-exact, `f64` timings — the type split
mirrors its wire shape one-to-one), plugin outcome enums, and the
runtime-state families' `json!` shapes (control/isolation/workspace_run)
remain distinct. No payload mega-struct, no dead optional fields. Module error
enums (`CheckpointError`, `PluginRuntimeError`/`PpcError`, `IsolatedError`)
are NOT unified — variant identity is load-bearing for `wire_kind()`
classification; only the boundary vocabulary (`OpError`) unifies.

## 8. The trust boundary

Host ops (`sandbox.acquire/release/status/list`) get **no** `OpRequest`
variants — the macro keeps them metadata-only catalog rows. Nothing host-side
changes in any phase: the gateway keeps `include_str!`-ing the committed
`ops.json` (byte-identical), `eos-sandbox-host` keeps its independent envelope
copy and its own `PROTOCOL_VERSION` (asserted `== 1` in its contract test),
and the conformance suites keep policing both sides as data. `OpRequest` is
the *in-box projection* of the same catalog rows. One v1 correction recorded:
`eos-sandbox-host` runs **outside** the sandbox (v1 §10 inverts this); the
no-dep rule survives on its real legs — no compiled code across the boundary,
and eos-operation's Linux dep tree cannot live in a darwin host binary.

## 9. v1 §10 rejections, re-litigated

| v1 §10 objection | Verdict | Mechanism |
|---|---|---|
| Defaults come from config/`DispatchContext`, unavailable at deserialization | **Overridden** | Payloads are wire projections: every defaultable field is `Option`; injection stays in the dispatch arm with `ctx` (file caps) or the process-global command config in scope. Only `DEFAULT_CALLER_ID` (a literal, never config) moves into core |
| Routing precedes validation (isolated-vs-direct, then `layer_stack_root`) | **Overridden** | Parse never enforces route-conditional presence: `layer_stack_root: Option<PathBuf>`; `MissingLayerStackRoot` raised in the arm after the binding lookup, same string, same ordering. Parse enforces only what adapters enforced unconditionally pre-routing. (`CommitToGit` legitimately requires it at parse — that op has no route decision) |
| `Handler` is a sync fn-pointer ABI | **Overridden** | `OpTable` replaced wholesale (the accepted churn). Still sync, same `spawn_blocking`. `fn_addr_eq` policed a table that no longer exists; the boot-time totality panic upgrades to compile-time exhaustiveness. `plugin.*` fallback untouched |
| Error shaping differs per family | **Overridden, typed** | Channel-agnostic `ArgsError` + one family→channel map + `OpReply::Refused`; message bytes reproduced by `ArgsError::Display`, pinned by the parity test. All three failure *encodings* survive verbatim |
| Unified outcome adds dead optional fields | **Upheld as shape, overridden as seam** | No payload mega-struct. `OpReply` variants carry no per-op fields; `MutationWire`'s field set is exactly today's `GuardedResponse`, every field live on both consuming paths |
| Per-family typed enums had zero consumers (F1) | **Overridden, whole-surface** | `OpRequest` is structurally incapable of the FAMILY_OPS failure: every variant has exactly one producer (its parse arm) and one consumer (its dispatch arm), both on the hot path of every request; deleting an op deletes variant + both arms or the workspace does not compile; per-variant payloads mean no field exists "for another op" |
| `core/envelope.rs` shared across the trust boundary | **Upheld** | §8. The shared artifacts remain `ops.json` + `contract/` fixtures |
| `CallerId`/`InvocationId` newtypes | **Upheld** | No consumer demands them; substrate crates keep `String` fields; the one call-site convention is the documented `caller_id()` accessor |
| `AuditRecord`/journal revival | **Upheld** | Removed 2026-06-11; the live remainder is exactly `MutationSource` |
| v1's own `OpFamily::contracts()` | **Upheld and extended** | Zero consumers = F1 at accessor granularity; not added |

## 10. Wire compatibility statement

- Request and error-envelope fixtures: **byte-identical** (host encoders untouched).
- Success responses: **canonical-equal**; mutation/commit/`final.json` paths
  additionally **byte-order-identical** (renderers relocated verbatim,
  `preserve_order` in both crates).
- `ops.json`: **byte-identical**, gated by `cargo xtask check-contract` every phase.
- Pinned strings preserved by construction: `mutation_source` spellings,
  status literals, both conflict spellings, `error.kind` vocabulary, all
  `request_args` message spellings (one owner: `ArgsError`), `"{key} is
  required"` in-band details, the `invalid envelope: ` Display prefix on
  handler-path parse errors, and raw shell-check messages without it.
- Fixture budget: zero existing fixtures change. **One additive fixture**:
  `command_settle_conflict_response.json`, captured from PRE-refactor code as
  the first commit of P7 — only a conflict-bearing settled response pins the
  `conflict_file` key-membership question — wired into the explicit
  conformance include list (both contract suites use explicit `fixture!` /
  `include_bytes!` lists, not directory globs, so the host suite is unaffected).
- The one tempting wire cleanup (collapsing the dual
  `conflict.reason`/`conflict_reason` spelling) does not pay for its
  ~50-test-file blast radius and is not proposed.

## 11. Contract/field changes and blast radius

| # | Change | Blast radius | Wire |
|---|---|---|---|
| 1 | Delete `OpTable`/`Handler` (`dispatcher.rs:26-125`) incl. `fn_addr_eq` dedup + boot panic; delete `builtin_handlers.rs` → `dispatch/builtin.rs` exhaustive match | `transport/server.rs` (`Arc<OpTable>` → free-fn dispatch); `tests/unit/dispatcher` rewritten as parse/dispatch tests; `phase2_read_paths.rs:14`, `phase3_write_paths.rs:8` (`with_builtins()` call sites); `eos-daemon/src/lib.rs:31` export | none |
| 2 | Adapter signatures `(&Value, Ctx) -> Result<Value, DaemonError>` → `(TypedArgs, Ctx) -> OpReply` (or `Result` + `From`) | 7 `op_adapter/` modules + co-located unit tests; the **five** `clippy::unnecessary_wraps` expects deleted | none (parse blocks move; bodies keep routing/config/output logic) |
| 3 | `request_args.rs` string helpers + `optional_u64`/`optional_path` DELETED (superseded by `core::request` helpers); `binding_to_value`/`timings_to_value_map` stay daemon-side | op_adapter modules; **`transport/server.rs:24,341`** (`trimmed_string` for registry caller-id tracking — re-point or inline; unstated in the original design, the workspace does not compile without it) | message spellings preserved verbatim, parity-pinned |
| 4 | `core::ops` → `core::catalog` rename; `from_op_name`; generated `contract()`; `BuiltinOp` drops `#[non_exhaustive]` | ~55 files: `dispatcher.rs:9`, `builtin_handlers.rs:3`, `wire/mod.rs:15`, `eosd/src/main.rs:55`, ~52 e2e files incl. `src/pool.rs` — mechanical; e2e's SKIP-slice + iteration usage survives | none (`ops.json` byte-identical) |
| 5 | `SearchReplaceEdit` → `core/request.rs` with `pub use` from `file` | `op_adapter/files.rs` import compiles via re-export | none |
| 6 | `GuardedResponse` → `core::MutationWire`; `mutation_response`/`conflict_value` collapse into `WorkspaceMutationOutcome::into_wire`; overlay post-splices applied after `into_value`, order preserved | `response.rs`, `files.rs`, daemon response unit tests | byte-order identical (pinned: `phase3_write_paths.rs:38-47,170-178`, `read_file_response` canonical bar) |
| 7 | `FileBackend::mutation_source` → `MutationSource`; settle literals → `as_str()`, discarded → `Option::None` | trait + 3 impls + settle | byte-identical (`phase3_write_paths.rs:41,176`; e2e `isolated_workspace_tool_routing.rs:116`) |
| 8 | `FileOpsError`/`WorkspaceApiError` → `core::OpError` re-exports; `kind` live | ~10 eos-operation files mechanical; daemon `#[from]` survives via re-export; error-equality assertions now compare `kind` (named test churn) | none (Display = message only) |
| 9 | Command DTOs relocate; `CommandResponse{workspace, metadata}` → `settled: Option<CommandMetadata>` + `WorkspaceMode`; `persist_final(&Value)`; `tail_lines` pub (stays substrate); `CommandSessionError` stays substrate; `u64_to_f64_saturating` home fixed | substrate 3 files + **session unit tests rebuilt**; eos-operation `command/*`; daemon command adapter + `tests/unit/command` (constructs `CommandResponse` field-by-field) | canonical-equal; `final.json` bytes identical; conflict-key membership pinned by the additive fixture |
| 10 | Deletions: 8 per-module `ops.rs` (229 LOC), 4 scaffold modules, `file/port.rs` (v1 §2 wrongly retains it); prune `src/lib.rs` AND family `lib.rs` decls (`file/lib.rs:15-16`, `command/lib.rs:3`, `checkpoint/lib.rs:19`, `plugin/lib.rs:3`) | zero consumers, triple-verified; rg gates must cover the family lib.rs decls | none |

**Untouched in every phase:** wire envelope codec, error envelope shape,
`DispatchContext`, the 16MiB/30s transport policy, all existing fixtures,
`ops.json` bytes, `eosd dump-ops`, eos-sandbox-host, eos-sandbox-gateway,
plugin ensure/status defaults (**false** — verified against
`plugin.rs:27-53`), dynamic `plugin.*` gate-before-route ordering, the
runtime.* timings splice.

## 12. Migration plan

Each phase lands independently, wire-invariant by construction; a failing
conformance suite reverts the phase, never the fixtures
(`contract/PROTOCOL.md` immutability rule).

| Phase | Change | Verification |
|---|---|---|
| P1 | Delete 8 `ops.rs`, 4 scaffold modules, `file/port.rs`; prune `src/lib.rs` + family lib.rs decls | `cargo check/test -p eos-operation`; rg gates (no `FAMILY_OPS`/family enums/`eos_operation::{control,sandbox,isolation,workspace_run}`/`file::port`); `cargo xtask check-contract` |
| P2 | `catalog.rs` rename; generated `contract()`; `from_op_name`; drop `#[non_exhaustive]`; ~55-file import rename | `cargo check --workspace`; `builtin_contracts_are_returned_by_ops` identity test; `cargo xtask check-contract` (ops.json byte-identical) |
| P3a | `core/request.rs` (all 28 parse fns + payloads + `ArgsError`) + **parity test** (legacy-vs-new error strings/channels, table-driven; named pins: edits-before-path, poll id-before-last_n_lines, checkpoint `{}`-args precedence, caller_id whitespace-only ⇒ `Some("")`); rewire control/checkpoint/isolation/workspace_run behind a temporary legacy-table fallback (named transitional scaffolding, deleted in P3c) | `cargo test -p eos-daemon`; e2e registration sweep (`{}` args → non-`unknown_op`); isolation e2e tier |
| P3b | Rewire files/command; move `SearchReplaceEdit`; re-point `transport/server.rs` | daemon unit + phase2/phase3 + contract fixtures; e2e core + command tiers |
| P3c | Rewire plugin builtins; delete `OpTable`/`builtin_handlers`/legacy helpers/fallback; rewrite dispatcher unit tests | `cargo test -p eos-daemon`; `cargo xtask check-contract`; e2e plugin tier |
| P4 | `OpReply` channel enum; convert arms; drop the five `unnecessary_wraps` expects | daemon unit + contract; e2e isolation + command NotFound cases |
| P5 | `MutationWire` + `WorkspaceMutationOutcome::into_wire` to core; rewrite `guarded_changeset_response` (post-splices after `into_value`, dead verb arms dropped) | `phase3_write_paths` (api_write/api_edit/key order); `read_file_response` canonical; e2e file + plugin overlay (overlay `changed_paths` order asserted) |
| P6 | `core/audit.rs` `MutationSource` (+ `FileBackend` retype) and `core/error.rs` `OpError` + re-exports + `From`s | daemon contract tests pin wire strings; e2e isolated routing; kind-comparison test churn |
| P7 | **First commit: capture the additive conflict fixture from pre-refactor code**, wire into explicit include lists. Then: command contract relocation — DTOs, `CommandMetadata`/`WorkspaceMode`, `persist_final(&Value)`, `tail_lines` pub, typed settle (exec isolated-branch caller_id overwrite preserved), `u64_to_f64_saturating` home, session unit tests rebuilt | `cargo test -p eos-command-session -p eos-operation -p eos-daemon`; round-trip vs both fixtures (conflict + extras-splice/discarded cases); `final.json` byte test; `cargo xtask check-contract` (all suites); e2e command-session + isolated tiers |

Deferred, named: `CommandStatus` enum (vocabulary co-owned with the substrate
exit-code mapping; no consumer forces it); relocating `path_changes_to_wire`
out of eos-workspace (correct ownership, ~6 lines, out of scope — and its
plugin-overlay call site must keep capture order regardless, §7.2).

## 13. Risks, named edges, and honest accounting

- **Parse-order fidelity** is the main regression surface; mechanical (each
  parse fn transcribes its adapter block) and double-pinned: the P3 parity
  test plus the e2e registration sweep.
- **`core/request.rs` size**: ~600-700 LOC (28 parse fns + ~24 payload
  structs + error types + helpers) approaches the repo's 800-1000 review-smell
  line. It qualifies under the carve-out for mechanically cohesive parser
  code; if transcription pushes it past ~800, split per family while keeping
  the single `OpRequest::parse` entry point.
- **Plugin args clone**: `PluginEnsureArgs.raw`/`PluginStatusArgs.raw` clone
  the args object once per ensure/status call; manifests are small, and the
  dynamic `plugin.*` hot path is untouched.
- **Test churn is real and accepted**: `tests/unit/dispatcher`
  (registration/collision semantics) is rewritten around parse/dispatch;
  phase2/phase3 swap `OpTable::with_builtins()` for the new entry point;
  session unit tests rebuilt. These are tests, not fixtures.
- **LOC, honestly**: the −242 LOC P1 deletion is severable and credits no
  design. Against live code the reshape is roughly **+250-350 LOC net**
  (core/request.rs dominates), none speculative; the P3a legacy-table fallback
  is named transitional scaffolding, built and then deleted in P3c.
  `write_file` end-to-end tracing goes from 4 stops / 5 files / 1 crate with
  an opaque `HashMap` hop to 5 stops / 6 files / 2 crates with every hop a
  greppable `match`. The purchase: compile-time totality replacing the
  wildcard-plus-boot-panic hole, deletion of fn-pointer registration policing,
  one parse site, one reply funnel, one owner per wire spelling, and a typed
  command-settlement contract — with fixtures, `ops.json`, and the trust
  boundary byte-for-byte where they were.
