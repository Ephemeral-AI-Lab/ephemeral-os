//! `eos-tools` — concrete model-facing tool construction.
//!
//! Shared tool contracts live in `eos-tool-ports`; engine tool-call policy lives
//! in `eos-engine`. This crate owns concrete tool DTOs, specs, executors, and
//! default registry assembly.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

#[path = "core/mod.rs"]
pub mod core;
#[path = "hooks/mod.rs"]
mod hooks;
#[path = "registry/mod.rs"]
mod registry;
#[path = "runtime/mod.rs"]
mod runtime;
#[path = "tools/mod.rs"]
mod tools;

#[cfg(test)]
#[path = "../tests/support/mod.rs"]
mod support;

pub use core::error::ToolError;
pub use core::intent::ToolIntent;
pub use core::metadata::ExecutionMetadata;
pub use core::name::{ToolKey, ToolName};
pub use core::ports::{
    AttemptSubmissionPort, BackgroundSessionCounts, CancelPort, CancelableResource,
    NotificationSink, PlanReducer, PlanTask, PlannerPlan, Sealed, SubagentLaunchRejection,
    SubagentSessionStatus, SubmissionAck, SystemNotification,
};
pub use core::result::{OutputShape, ToolResult};
pub use hooks::Hook;
pub use registry::config::{ToolConfig, ToolConfigError, ToolConfigSet};
pub use registry::tool_registry::ToolRegistry;
pub use runtime::executor::{RegisteredTool, ToolExecutor};
pub use tools::terminal::{
    descriptor, render_tool_instruction, TerminalDescriptor, TerminalTool, ToolInstructions,
};
pub use tools::{
    build_default_registry, build_default_registry_with_services, AttemptSubmissionService,
    CallerScope, CommandSessionToolService, CommandToolService, HookServices,
    RootSubmissionService, SandboxToolService, SkillToolService, SubagentToolService,
    WorkflowToolService,
};
