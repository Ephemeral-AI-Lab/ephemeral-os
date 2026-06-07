//! Per-agent setup before entering the query loop.

use std::path::PathBuf;
use std::sync::Arc;

use eos_agent_def::AgentDefinition;
use eos_llm_client::DEFAULT_MAX_TOKENS;
use eos_tools::{
    build_default_registry_with_services, AttemptSubmissionService, BackgroundSupervisorPort,
    CallerScope, CommandSessionSupervisorPort, ExecutionMetadata, WorkflowControlPort,
};
use eos_types::{AgentRunId, TaskId};

use crate::agent::{build_query_context, BuildQueryContextInput};
use crate::background::BackgroundRunFinalizer;
use crate::notifications::NotificationService;
use crate::query::QueryContext;
use crate::EngineError;

use super::control::AgentRunCancellation;
use super::foreground::ForegroundExecutor;
use super::types::EngineRunHandles;

pub(super) struct AgentRunSetupInput {
    pub(super) agent: AgentDefinition,
    pub(super) task_id: Option<TaskId>,
    pub(super) agent_run_id: AgentRunId,
    pub(super) tool_metadata: ExecutionMetadata,
    pub(super) attempt_submission: Option<AttemptSubmissionService>,
    pub(super) workflow_control: Option<Arc<dyn WorkflowControlPort>>,
    pub(super) background_supervisor: Option<Arc<dyn BackgroundSupervisorPort>>,
    pub(super) command_session_supervisor: Option<Arc<dyn CommandSessionSupervisorPort>>,
    pub(super) notifier: NotificationService,
    pub(super) cancellation: AgentRunCancellation,
    pub(super) foreground: Arc<ForegroundExecutor>,
}

pub(super) struct PreparedAgentRun {
    pub(super) ctx: QueryContext,
    pub(super) background_finalizer: BackgroundRunFinalizer,
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
        attempt_submission,
        workflow_control,
        background_supervisor,
        command_session_supervisor,
        notifier,
        cancellation,
        foreground,
    } = input;

    let model = agent.model.clone().unwrap_or_default();
    let event_source = handles
        .event_source_factory
        .as_ref()
        .map(|factory| factory(&agent));
    let caller_scope = caller_scope_for(handles, &agent);
    let agent_run_ids = tool_metadata.agent_run_id.iter().cloned().collect();
    let mut registry = build_default_registry_with_services(
        &handles.tool_config,
        &caller_scope,
        handles.sandbox_service.clone(),
        handles.root_submission.clone(),
        attempt_submission,
        workflow_control.clone(),
        background_supervisor.clone(),
        command_session_supervisor,
        handles.skill_service.clone(),
    );
    if let Some(extender) = &handles.tool_registry_extender {
        extender(&mut registry);
    }

    let background_finalizer =
        BackgroundRunFinalizer::new(background_supervisor, workflow_control, agent_run_ids);
    let ctx = build_query_context(BuildQueryContextInput {
        agent,
        model,
        client: Some(handles.llm_client.clone()),
        event_source,
        registry,
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
        background_finalizer,
    })
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
