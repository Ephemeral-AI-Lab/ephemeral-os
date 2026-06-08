//! Runtime implementations for engine agent-loop composition contracts.

use std::sync::{Arc, OnceLock};

use async_trait::async_trait;
use eos_agent_ports::{
    AgentExecutionMetadataService, AgentLoopLauncher, AgentPortError, AgentRunApi, AgentRunError,
    AgentRunOutcome, AgentState, AuditNodeBuildInput, ExecutionMetadataBuildInput,
};
use eos_audit::AuditNode;
use eos_engine::{
    AgentLoopBackgroundDependencies, AgentLoopToolRegistryBuildInput, AgentLoopToolRegistryFactory,
    EventCallback, ProviderEventSource, TokioAgentLoopLauncher,
};
use eos_sandbox_port::SandboxCommandService;
use eos_tool_ports::{ExecutionMetadata, ToolRegistry};
use eos_tools::{
    build_default_registry_with_services, AttemptSubmissionService, CallerScope,
    RootSubmissionService, SandboxToolService, SkillToolService,
};
use eos_types::WorkflowApi;

use super::RuntimeServices;
use crate::plugin_tools::register_plugin_tools;

/// Shared cell used to break the runner -> launcher -> tools -> runner cycle.
pub(crate) type AgentRunApiCell = Arc<OnceLock<Arc<dyn AgentRunApi>>>;

/// Build a production agent-loop launcher plus the cell that must be filled with
/// the lifecycle service after it is constructed.
pub(crate) fn build_agent_loop_launcher(
    services: &RuntimeServices,
    attempt_submission: Option<AttemptSubmissionService>,
    workflow_service: Arc<OnceLock<Arc<dyn WorkflowApi>>>,
    event_callback: Option<EventCallback>,
) -> (Arc<dyn AgentLoopLauncher>, AgentRunApiCell) {
    let agent_run_api = Arc::new(OnceLock::new());
    let metadata_service = Arc::new(RuntimeExecutionMetadataService::new(services.clone()));
    let registry_factory = Arc::new(RuntimeToolRegistryFactory {
        services: services.clone(),
        attempt_submission,
        workflow_service,
        agent_run_api: agent_run_api.clone(),
    });
    let background_dependencies = AgentLoopBackgroundDependencies::new(
        Arc::new(LateBoundAgentRunApi::new(agent_run_api.clone())),
        Arc::new(SandboxCommandService::new(
            services.sandbox.transport.clone(),
        )),
        services.engine.command_session_completion_poll_interval(),
        registry_factory.workflow_service.clone(),
    );
    let launcher_impl = match services.engine.event_source_factory.clone() {
        Some(factory) => TokioAgentLoopLauncher::with_event_source_factory(
            factory,
            registry_factory.clone(),
            metadata_service.clone(),
        ),
        None => TokioAgentLoopLauncher::new(
            Arc::new(ProviderEventSource::new(services.engine.llm_client.clone())),
            registry_factory,
            metadata_service,
        ),
    }
    .with_background_dependencies(background_dependencies)
    .with_event_callback(event_callback);
    let launcher: Arc<dyn AgentLoopLauncher> = Arc::new(launcher_impl);
    (launcher, agent_run_api)
}

#[derive(Clone)]
struct LateBoundAgentRunApi {
    cell: AgentRunApiCell,
}

impl LateBoundAgentRunApi {
    fn new(cell: AgentRunApiCell) -> Self {
        Self { cell }
    }

    fn service(&self) -> Result<Arc<dyn AgentRunApi>, AgentRunError> {
        self.cell
            .get()
            .cloned()
            .ok_or_else(|| AgentRunError::Internal("agent-run API not initialized".to_owned()))
    }
}

impl std::fmt::Debug for LateBoundAgentRunApi {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LateBoundAgentRunApi")
            .finish_non_exhaustive()
    }
}

#[async_trait]
impl AgentRunApi for LateBoundAgentRunApi {
    async fn spawn_agent(
        &self,
        request: eos_agent_ports::SpawnAgentRequest,
    ) -> Result<eos_types::AgentRunId, AgentRunError> {
        self.service()?.spawn_agent(request).await
    }

    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &eos_types::AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        self.service()?.wait_for_agent_outcome(agent_run_id).await
    }

    async fn poll_agent_run_outcome(
        &self,
        agent_run_id: &eos_types::AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError> {
        self.service()?.poll_agent_run_outcome(agent_run_id).await
    }

    async fn cancel_agent_run(
        &self,
        agent_run_id: &eos_types::AgentRunId,
        reason: &str,
    ) -> Result<(), AgentRunError> {
        self.service()?.cancel_agent_run(agent_run_id, reason).await
    }
}

struct RuntimeToolRegistryFactory {
    services: RuntimeServices,
    attempt_submission: Option<AttemptSubmissionService>,
    workflow_service: Arc<OnceLock<Arc<dyn WorkflowApi>>>,
    agent_run_api: AgentRunApiCell,
}

impl std::fmt::Debug for RuntimeToolRegistryFactory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RuntimeToolRegistryFactory")
            .finish_non_exhaustive()
    }
}

impl AgentLoopToolRegistryFactory for RuntimeToolRegistryFactory {
    fn build_tool_registry(
        &self,
        input: AgentLoopToolRegistryBuildInput,
    ) -> Result<ToolRegistry, eos_engine::EngineError> {
        let sandbox_service = SandboxToolService::new(self.services.sandbox.transport.clone())
            .with_isolated_workspace_service(
                self.services.agent_state.isolated_workspace_tool_service(),
            );
        let plugin_sandbox_service = sandbox_service.clone();
        let caller = CallerScope {
            dispatchable_subagents: self
                .services
                .agent_core
                .agent_registry
                .dispatchable_subagent_names()
                .iter()
                .map(|name| name.as_str().to_owned())
                .collect(),
            skill_slug: None,
        };
        let mut registry = build_default_registry_with_services(
            &self.services.agent_core.tool_config,
            &caller,
            sandbox_service,
            Some(RootSubmissionService::new(
                self.services.db.task_store.clone(),
                self.services.db.request_store.clone(),
            )),
            self.attempt_submission.clone(),
            self.agent_run_api.get().cloned(),
            Some(input.subagent_sessions),
            self.workflow_service.get().cloned(),
            Some(input.workflow_sessions),
            Some(input.command_sessions),
            SkillToolService::new(self.services.agent_core.skill_registry.clone()),
        );
        register_plugin_tools(&mut registry, &plugin_sandbox_service);
        Ok(registry)
    }
}

#[derive(Clone)]
struct RuntimeExecutionMetadataService {
    services: RuntimeServices,
}

impl RuntimeExecutionMetadataService {
    fn new(services: RuntimeServices) -> Self {
        Self { services }
    }

    async fn load_agent_state(
        &self,
        agent_run_id: &eos_types::AgentRunId,
    ) -> Result<AgentState, AgentPortError> {
        let runtime_state = self.services.agent_state.get(agent_run_id);
        let run = self
            .services
            .db
            .agent_run_store
            .get(agent_run_id)
            .await
            .map_err(|err| AgentPortError::Internal(err.to_string()))?;
        let agent_name = run
            .as_ref()
            .map(|run| run.agent_name.clone())
            .or_else(|| runtime_state.as_ref().map(|state| state.agent_name.clone()))
            .ok_or_else(|| AgentPortError::Internal(format!("agent run {agent_run_id} missing")))?;
        let task_id = run
            .as_ref()
            .and_then(|run| run.task_id.clone())
            .or_else(|| {
                runtime_state
                    .as_ref()
                    .and_then(|state| state.task_id.clone())
            });
        let task = match &task_id {
            Some(task_id) => self
                .services
                .db
                .task_store
                .get(task_id)
                .await
                .map_err(|err| AgentPortError::Internal(err.to_string()))?,
            None => None,
        };
        let request_id = task
            .as_ref()
            .map(|task| task.request_id.clone())
            .or_else(|| {
                runtime_state
                    .as_ref()
                    .and_then(|state| state.request_id.clone())
            });
        let request = match &request_id {
            Some(request_id) => self
                .services
                .db
                .request_store
                .get(request_id)
                .await
                .map_err(|err| AgentPortError::Internal(err.to_string()))?,
            None => None,
        };
        let runtime_workspace_root = runtime_state
            .as_ref()
            .map(|state| state.workspace_root.as_str())
            .filter(|workspace_root| !workspace_root.trim().is_empty());

        Ok(AgentState {
            agent_run_id: agent_run_id.clone(),
            agent_name,
            request_id,
            task_id,
            workflow_id: task
                .as_ref()
                .and_then(|task| task.workflow_id.clone())
                .or_else(|| {
                    runtime_state
                        .as_ref()
                        .and_then(|state| state.workflow_id.clone())
                }),
            iteration_id: task
                .as_ref()
                .and_then(|task| task.iteration_id.clone())
                .or_else(|| {
                    runtime_state
                        .as_ref()
                        .and_then(|state| state.iteration_id.clone())
                }),
            attempt_id: task
                .as_ref()
                .and_then(|task| task.attempt_id.clone())
                .or_else(|| {
                    runtime_state
                        .as_ref()
                        .and_then(|state| state.attempt_id.clone())
                }),
            sandbox_id: runtime_state
                .as_ref()
                .and_then(|state| state.sandbox_id.clone())
                .or_else(|| {
                    request
                        .as_ref()
                        .and_then(|request| request.sandbox_id.clone())
                }),
            workspace_root: runtime_workspace_root.map_or_else(
                || request.map_or_else(String::new, |request| request.cwd),
                str::to_owned,
            ),
            is_isolated_workspace_mode: runtime_state
                .as_ref()
                .is_some_and(|state| state.is_isolated_workspace_mode),
        })
    }
}

#[async_trait]
impl AgentExecutionMetadataService for RuntimeExecutionMetadataService {
    async fn agent_state(
        &self,
        agent_run_id: &eos_types::AgentRunId,
    ) -> Result<AgentState, AgentPortError> {
        self.load_agent_state(agent_run_id).await
    }

    async fn build_execution_metadata(
        &self,
        input: ExecutionMetadataBuildInput,
    ) -> Result<ExecutionMetadata, AgentPortError> {
        let state = self.load_agent_state(&input.agent_run_id).await?;
        Ok(ExecutionMetadata {
            agent_name: state.agent_name,
            agent_run_id: Some(state.agent_run_id),
            request_id: state.request_id,
            task_id: state.task_id,
            attempt_id: state.attempt_id,
            workflow_id: state.workflow_id,
            tool_use_id: Some(input.tool_use_id),
            sandbox_invocation_id: None,
            sandbox_id: state.sandbox_id,
            is_isolated_workspace_mode: state.is_isolated_workspace_mode,
            workspace_root: state.workspace_root,
            conversation: input.conversation,
        })
    }

    async fn build_audit_node(
        &self,
        input: AuditNodeBuildInput,
    ) -> Result<AuditNode, AgentPortError> {
        let state = self.load_agent_state(&input.agent_run_id).await?;
        let mut builder = AuditNode::builder()
            .agent_run_id(state.agent_run_id)
            .agent_name(state.agent_name);
        if let Some(request_id) = state.request_id {
            builder = builder.request_id(request_id);
        }
        if let Some(task_id) = state.task_id {
            builder = builder.task_id(task_id);
        }
        if let Some(workflow_id) = state.workflow_id {
            builder = builder.workflow_id(workflow_id);
        }
        if let Some(iteration_id) = state.iteration_id {
            builder = builder.iteration_id(iteration_id);
        }
        if let Some(attempt_id) = state.attempt_id {
            builder = builder.attempt_id(attempt_id);
        }
        if let Some(sandbox_id) = state.sandbox_id {
            builder = builder.sandbox_id(sandbox_id);
        }
        if let Some(tool_name) = input.tool_name {
            builder = builder.tool_name(tool_name.as_str());
        }
        if let Some(tool_use_id) = input.tool_use_id {
            builder = builder.tool_use_id(tool_use_id);
        }
        Ok(builder.build())
    }
}
