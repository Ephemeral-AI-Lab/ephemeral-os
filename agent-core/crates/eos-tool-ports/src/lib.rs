//! Shared model-facing tool contracts.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod core;
pub mod hooks;
pub mod registry;
pub mod runtime;
pub mod services;

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
pub use registry::tool_registry::ToolRegistry;
pub use runtime::executor::{RegisteredTool, ToolExecutor};
pub use services::{
    CommandSessionToolService, HookServices, IsolatedWorkspaceToolService, SubagentToolService,
    WorkflowToolService,
};
