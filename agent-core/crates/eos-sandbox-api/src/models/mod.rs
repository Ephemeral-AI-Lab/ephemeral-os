//! Request/result DTOs, caller identity, and intent for the host-facing sandbox
//! protocol (ported from `sandbox/shared/models.py`).
//!
//! These are wire types: every DTO derives `Serialize`/`Deserialize`/`JsonSchema`
//! (`api-common-traits`). Composition is by embedding `SandboxRequestBase` /
//! `SandboxResultBase` as a flattened field rather than class inheritance. The
//! request structs are never serialized straight to the daemon — the
//! `tool_api` helpers build each daemon payload field-by-field — so the derived
//! serde shape only backs schema snapshots and round-trip tests.
//!
//! Source-driven cleanup from the Python module: one-field identity wrappers and
//! `RawExecResult` are dropped; daemon requests carry a direct, opaque
//! `caller_id`.

mod command;
mod common;
mod file;
mod lifecycle;
mod tool_call;

pub use command::{
    CommandOutput, CommandSessionCancelRequest, CommandStatusView, ExecCommandRequest,
    ExecCommandResult, ExecStdinRequest, KnownCommandStatus,
};
pub use common::{ConflictInfo, Intent, SandboxRequestBase, SandboxResultBase, Workspace};
pub use file::{
    EditFileRequest, EditFileResult, ReadFileRequest, ReadFileResult, SearchReplaceEdit,
    WriteFileRequest, WriteFileResult,
};
pub use lifecycle::{
    EnterIsolatedWorkspaceRequest, EnterIsolatedWorkspaceResult, ExitIsolatedWorkspaceRequest,
    ExitIsolatedWorkspaceResult, LifecycleError, LifecycleResultBase,
};
pub use tool_call::ToolCallRequest;
