//! [`ToolError`] — the single framework-fault error enum (`err-thiserror-lib`).
//!
//! **Err vs in-band (§8.2, a deliberate divergence from Rust).** `ToolError`
//! (`Result::Err`) is reserved for **framework faults**: an unknown tool, a
//! required port not wired, a missing required execution-context id, an upstream
//! store/sandbox transport failure, or an internal invariant break. Tool-domain
//! failures — bad arguments, a hook `Deny`, or a tool that "said no" — are
//! **in-band** [`ToolResult`](crate::ToolResult) values with `is_error = true`,
//! returned as `Ok`. The engine renders in-band errors back to the model and
//! surfaces `Err` to triage. Rust returned the internal-validation branch
//! in-band; the Rust ACs (AC-tools-02..04) encode this new boundary.

use eos_sandbox_port::SandboxPortError;
use eos_types::CoreError;

/// A framework fault during tool execution. Tool-domain failures are in-band
/// [`ToolResult`](crate::ToolResult)s, not variants here.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum ToolError {
    /// The dispatched tool name is not registered.
    #[error("unknown tool: {0}")]
    UnknownTool(String),

    /// A required execution-context value (e.g. `task_id`, `sandbox_id`) was
    /// absent where the tool requires it.
    #[error("missing required execution context: {0}")]
    MissingContext(&'static str),

    /// A required downstream-state port was not wired at the composition root.
    #[error("required port not wired: {0}")]
    MissingPort(&'static str),

    /// An upstream `Store` operation failed.
    #[error("store error: {0}")]
    Store(#[from] CoreError),

    /// A sandbox transport / daemon RPC failed at the framework level.
    #[error("sandbox error: {0}")]
    Sandbox(#[from] SandboxPortError),

    /// An internal invariant broke (should not happen in correct wiring).
    #[error("internal tool error: {0}")]
    Internal(String),
}
