//! Reader-side normalization for Rust audit/observability consumers.
//!
//! Producers keep their local mechanics: agent-core writes normalized JSONL and
//! the sandbox daemon exposes its bounded native ring. This crate is the small
//! collector boundary that turns both inputs into [`eos_obs_contract::ObsEnvelope`]
//! rows for future runner gates.

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
    ObsCollectorError, SandboxAuditLoss, SandboxPullBatch,
};
