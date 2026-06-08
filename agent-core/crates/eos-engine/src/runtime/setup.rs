//! Per-agent setup before entering the query loop.

use std::path::PathBuf;
use std::sync::Arc;

use eos_agent_def::AgentDefinition;
use eos_llm_client::DEFAULT_MAX_TOKENS;
use eos_tools::{build_default_registry_with_services, CallerScope, ExecutionMetadata};
use eos_types::{AgentRunId, TaskId};

use crate::agent::{build_query_context, BuildQueryContextInput};
use crate::background::{BackgroundSessionFinalizer, BackgroundTeardownService};
use crate::notifications::NotificationService;
use crate::query::QueryContext;
use crate::EngineError;

use super::control::AgentRunCancellation;
use super::foreground::ForegroundExecutor;
use super::types::{AgentToolRegistryServices, EngineRunHandles};

pub(super) struct AgentRunSetupInput {
    pub(super) agent: AgentDefinition,
    pub(super) task_id: Option<TaskId>,
    pub(super) agent_run_id: AgentRunId,
    pub(super) tool_metadata: ExecutionMetadata,
    pub(super) tool_registry: eos_tools::ToolRegistry,
    pub(super) background_teardown: Option<BackgroundTeardownService>,
    pub(super) notifier: NotificationService,
    pub(super) cancellation: AgentRunCancellation,
    pub(super) foreground: Arc<ForegroundExecutor>,
}

pub(super) struct PreparedAgentRun {
    pub(super) ctx: QueryContext,
    pub(super) background_teardown_finalizer: BackgroundSessionFinalizer,
}

pub(super) fn prepare_agent_run_context(
    handles: &EngineRunHandles,
    input: AgentRunSetupInput,
) -> Result<PreparedAgentRun, EngineError> {
    let AgentRunSetupInput {
        agent,
        task_id,
        agent_run_id,
        tool_metadata,
        tool_registry,
        background_teardown,
        notifier,
        cancellation,
        foreground,
    } = input;

    let model = agent.model.clone().unwrap_or_default();
    let event_source = handles
        .event_source_factory
        .as_ref()
        .map(|factory| factory(&agent));

    let background_teardown_finalizer = BackgroundSessionFinalizer::new(background_teardown);
    let ctx = build_query_context(BuildQueryContextInput {
        agent,
        model,
        client: Some(handles.llm_client.clone()),
        event_source,
        registry: tool_registry,
        base_system_prompt: String::new(),
        max_tokens: DEFAULT_MAX_TOKENS,
        cwd: PathBuf::from(&handles.workspace_root),
        agent_run_id,
        task_id,
        tool_metadata,
        notifier,
        cancellation,
        foreground,
        audit: Some(handles.audit.clone()),
        run_handles: Some(handles.clone()),
    })?;

    Ok(PreparedAgentRun {
        ctx,
        background_teardown_finalizer,
    })
}

/// Build one run's tool registry before entering the engine loop.
#[must_use]
pub fn build_agent_tool_registry(
    handles: &EngineRunHandles,
    agent: &AgentDefinition,
    services: AgentToolRegistryServices,
) -> eos_tools::ToolRegistry {
    let caller_scope = caller_scope_for(handles, agent);
    let mut registry = build_default_registry_with_services(
        &handles.tool_config,
        &caller_scope,
        handles.sandbox_service.clone(),
        handles.root_submission.clone(),
        services.attempt_submission,
        services.agent_run_service,
        services.subagent_sessions,
        services.workflow_service,
        services.workflow_sessions,
        services.command_sessions,
        handles.skill_service.clone(),
    );
    if let Some(extender) = &handles.tool_registry_extender {
        extender(&mut registry);
    }
    registry
}

fn caller_scope_for(handles: &EngineRunHandles, agent: &AgentDefinition) -> CallerScope {
    CallerScope {
        dispatchable_subagents: handles
            .agent_registry
            .dispatchable_subagent_names()
            .iter()
            .map(|name| name.as_str().to_owned())
            .collect(),
        // The bound agent's own skill folder name scopes `load_skill_reference`.
        skill_slug: agent
            .skill
            .as_deref()
            .and_then(|p| p.parent())
            .and_then(|p| p.file_name())
            .map(|s| s.to_string_lossy().into_owned()),
    }
}
