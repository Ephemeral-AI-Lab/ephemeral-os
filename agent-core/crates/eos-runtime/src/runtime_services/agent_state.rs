//! Runtime-only agent state used for per-tool metadata rendering.

use std::collections::HashMap;
use std::sync::{Arc, RwLock};

use eos_agent_ports::{AgentRunError, AgentRunMessageRecordKind, SpawnAgentRequest};
use eos_tool_ports::{IsolatedWorkspaceToolService, ToolError};
use eos_types::{AgentRunId, AttemptId, IterationId, RequestId, SandboxId, TaskId, WorkflowId};

#[derive(Clone, Debug, Default)]
pub(crate) struct RuntimeAgentStateService {
    inner: Arc<RwLock<HashMap<AgentRunId, RuntimeAgentState>>>,
}

#[derive(Clone, Debug)]
pub(crate) struct RuntimeAgentState {
    pub(crate) agent_name: String,
    pub(crate) request_id: Option<RequestId>,
    pub(crate) task_id: Option<TaskId>,
    pub(crate) workflow_id: Option<WorkflowId>,
    pub(crate) iteration_id: Option<IterationId>,
    pub(crate) attempt_id: Option<AttemptId>,
    pub(crate) sandbox_id: Option<SandboxId>,
    pub(crate) workspace_root: String,
    pub(crate) is_isolated_workspace_mode: bool,
}

impl RuntimeAgentStateService {
    pub(crate) fn record_spawn_request(
        &self,
        request: &SpawnAgentRequest,
        agent_run_id: &AgentRunId,
    ) -> Result<(), AgentRunError> {
        let mut states = self
            .inner
            .write()
            .map_err(|_| AgentRunError::Internal("runtime agent state lock poisoned".to_owned()))?;
        states.insert(agent_run_id.clone(), RuntimeAgentState::from(request));
        Ok(())
    }

    pub(crate) fn remove(&self, agent_run_id: &AgentRunId) {
        if let Ok(mut states) = self.inner.write() {
            states.remove(agent_run_id);
        }
    }

    pub(crate) fn get(&self, agent_run_id: &AgentRunId) -> Option<RuntimeAgentState> {
        self.inner
            .read()
            .ok()
            .and_then(|states| states.get(agent_run_id).cloned())
    }

    pub(crate) fn isolated_workspace_tool_service(&self) -> IsolatedWorkspaceToolService {
        let service = self.clone();
        IsolatedWorkspaceToolService::new(move |agent_run_id, is_isolated| {
            let service = service.clone();
            async move { service.set_isolated_workspace_mode(&agent_run_id, is_isolated) }
        })
    }

    fn set_isolated_workspace_mode(
        &self,
        agent_run_id: &AgentRunId,
        is_isolated: bool,
    ) -> Result<(), ToolError> {
        let mut states = self
            .inner
            .write()
            .map_err(|_| ToolError::Internal("runtime agent state lock poisoned".to_owned()))?;
        let Some(state) = states.get_mut(agent_run_id) else {
            return Err(ToolError::Internal(format!(
                "runtime agent state missing for {agent_run_id}"
            )));
        };
        state.is_isolated_workspace_mode = is_isolated;
        Ok(())
    }
}

impl From<&SpawnAgentRequest> for RuntimeAgentState {
    fn from(request: &SpawnAgentRequest) -> Self {
        let (record_workflow_id, record_iteration_id, record_attempt_id) =
            match &request.record_kind {
                AgentRunMessageRecordKind::WorkflowTask {
                    workflow_id,
                    iteration_id,
                    attempt_id,
                    ..
                } => (
                    Some(workflow_id.clone()),
                    Some(iteration_id.clone()),
                    Some(attempt_id.clone()),
                ),
                _ => (None, None, None),
            };
        Self {
            agent_name: request.agent_name.as_str().to_owned(),
            request_id: request.request_id.clone(),
            task_id: request.task_id.clone(),
            workflow_id: request.workflow_id.clone().or(record_workflow_id),
            iteration_id: record_iteration_id,
            attempt_id: request.attempt_id.clone().or(record_attempt_id),
            sandbox_id: request.sandbox_id.clone(),
            workspace_root: request.workspace_root.clone(),
            is_isolated_workspace_mode: request.is_isolated_workspace_mode,
        }
    }
}
