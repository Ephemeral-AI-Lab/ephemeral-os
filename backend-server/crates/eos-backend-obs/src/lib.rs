//! Backend-owned observability collection and persistence.
//!
//! This crate owns the reader-side normalization boundary for Rust
//! audit/observability consumers plus the runner gates. Producers keep their
//! local mechanics: agent-core writes normalized JSONL and the sandbox daemon
//! exposes its bounded native ring. The backend normalizes both inputs into
//! [`eos_audit::ObsEnvelope`] rows before future sink, ingestor, and stats code
//! persists or reports them.

#![forbid(unsafe_code)]

mod gates;
mod normalization;

pub use gates::{
    evaluate_runner_gate_batches, evaluate_runner_gate_sources, evaluate_runner_gates,
    ExpectedToolUse, RunnerCorrectnessEvidence, RunnerGateBatchInput, RunnerGateFailure,
    RunnerGateFailureKind, RunnerGateInput, RunnerGateMetrics, RunnerGateReport,
    RunnerGateSettings, RunnerGateSourceInput,
};
pub use normalization::{
    normalize_agent_core_jsonl_line, normalize_sandbox_event, normalize_sandbox_pull_response,
    ObsNormalizationError, SandboxAuditLoss, SandboxPullBatch,
};
