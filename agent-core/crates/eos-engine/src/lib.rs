//! `eos-engine` — one agent query loop, tool dispatch, background
//! session accounting, notifications, prompt reports, and the provider-stream seam.
#![forbid(unsafe_code)]

pub mod agent_loop;
pub mod background;
mod notifications;
pub mod provider_stream;
pub mod run_output;
mod support;
mod telemetry;
pub mod tool_call;

pub use agent_loop::{
    AgentLoopToolRegistryBuildInput, AgentLoopToolRegistryFactory, BackgroundSessionRuntimeFactory,
    EngineRuntimeConfig, ExecutionMetadataBuildInput, TokioAgentLoopLauncher,
    ToolExecutionMetadataReader, DEFAULT_BACKGROUND_COMPLETION_POLL_INTERVAL_MS,
};
pub use background::{
    BackgroundCompletion, BackgroundNotificationEmitter, BackgroundSessionRuntime,
    BackgroundSessionStatus, BackgroundSessionTeardown,
};
pub use notifications::{
    make_default_notification_rules, EngineNotificationQueue, NotificationRule,
    NotificationRuleContext,
};
pub use provider_stream::{
    EngineStream, LlmProviderStreamSource, ProviderStreamSource, ProviderStreamSourceFactory,
};
pub use run_output::{
    stamp_identity, AgentRunOutputs, AgentRunRecordStore, AgentRunStreamEvent, AgentRunStreamSink,
    AgentRunStreamSinkFactory, AssistantMessageComplete,
};
pub use support::EngineError;
pub use telemetry::PromptReportRecorder;
