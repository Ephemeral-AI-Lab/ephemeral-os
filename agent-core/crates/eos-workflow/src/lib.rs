//! `eos-workflow` — delegated workflow lifecycle, per-attempt orchestration,
//! run-stage scheduling, launch context composition, and workflow-context
//! packets.
//!
//! The crate depends on store traits and downstream-state ports, not concrete
//! persistence or engine crates. It owns only delegated workflow state; root
//! requests remain direct root tasks.
#![forbid(unsafe_code)]

mod attempt;
mod context;
mod error;
mod ids;
mod iteration;
mod lifecycle;
mod ports;
mod starter;
mod util;

#[cfg(test)]
mod testsupport;

pub use attempt::{
    AgentLaunch, AgentRunReport, AgentRunner, AttemptDeps, AttemptOrchestratorRegistry,
    ExecutionLaunch, PlannerLaunch,
};
pub use context::{
    render_context_xml, render_task_guidance, AgentContext, AgentEntryComposer, AgentEntryMessages,
    ContextEngine, ContextEngineDeps, ContextRole, ContextScope, ContextSection,
};
pub use error::{Result, WorkflowError};
pub use ids::{generator_task_id, planner_task_id, reducer_task_id, WorkflowLifecycleConfig};
pub use iteration::OpenIterationCoordinatorRegistry;
pub use ports::{PlanSubmissionAdapter, WorkflowControlAdapter};
pub use starter::{StartedWorkflow, WorkflowStarter};
