//! Backend-owned observability collection, persistence, and stats.
//!
//! This crate owns the reader-side boundary for Rust audit/observability:
//!
//! - [`normalization`]/[`gates`]: normalize agent JSONL and sandbox audit
//!   pulls into [`ObsEnvelope`] rows and evaluate the Rust runner
//!   audit/observability gates. Producers keep their local mechanics (agent-core
//!   writes normalized JSONL; the daemon exposes a bounded native ring).
//! - [`PersistingSink`]: the backend [`AuditSink`] that
//!   async-drains agent-core audit events into `obs_event` without blocking the
//!   engine hot path (AC6).
//! - [`AuditIngestor`]: ingest a daemon `api.audit.pull` response into `obs_event`,
//!   join daemon-facing identities to model-facing ids through
//!   `sandbox_call_correlation`, and track the per-sandbox `audit_cursor` across
//!   daemon reboots (AC7, AC8).
//! - [`StatsReader`]: assemble the `/api/stats/*` summaries from `obs_event` and
//!   `audit_cursor`.

#![forbid(unsafe_code)]

mod contract;
mod gates;
mod ingestor;
mod normalization;
mod sink;
mod stats;

pub use contract::{
    canonical_event_type, from_jsonl_line, to_jsonl_line, AuditError, AuditEvent, AuditNode,
    AuditNodeBuilder, AuditSink, AuditSource, JsonObject, NoopAuditSink, ObsEnvelope, ObsIds,
    ObsSource, AGENT_RUN_COMPLETED, OS_RESOURCE_SAMPLED, SCHEMA, TOOL_CALL_COMPLETED,
};
pub use gates::{
    evaluate_runner_gate_batches, evaluate_runner_gate_sources, evaluate_runner_gates,
    ExpectedToolUse, RunnerCorrectnessEvidence, RunnerGateBatchInput, RunnerGateFailure,
    RunnerGateFailureKind, RunnerGateInput, RunnerGateMetrics, RunnerGateReport,
    RunnerGateSettings, RunnerGateSourceInput,
};
pub use ingestor::{AuditIngestor, IngestError, IngestReport, UNMATCHED_MARKER};
pub use normalization::{
    normalize_agent_core_jsonl_line, normalize_sandbox_event, normalize_sandbox_pull_response,
    ObsNormalizationError, SandboxAuditLoss, SandboxPullBatch,
};
pub use sink::{PersistingSink, PersistingSinkShutdown, SinkLoss};
pub use stats::StatsReader;
