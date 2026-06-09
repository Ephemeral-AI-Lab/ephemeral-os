use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{
    AgentDefinition, AgentName, AgentRegistry, AgentRunId, AgentRunStore, AgentType, Attempt,
    AttemptStore, IterationStore, RequestId, WorkItemSpec, WorkflowStore,
};

use crate::config::WorkflowLifecycleConfig;
use crate::context::{
    render_context_xml, render_planner_agent_context, render_task_guidance,
    render_worker_agent_context, wrap_task_guidance,
};
use crate::{Result, WorkflowError};

use super::{ActiveAttemptRuns, OpenIterationCoordinatorRegistry};

/// Result of one agent run at the workflow seam.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct AgentRunReport {
    /// A framework-fault summary if the engine run broke; `None` on a clean run.
    pub failure_summary: Option<String>,
}

impl AgentRunReport {
    /// A clean run.
    #[must_use]
    pub fn ok() -> Self {
        Self {
            failure_summary: None,
        }
    }

    /// A run that broke with `summary`.
    #[must_use]
    pub fn failed(summary: impl Into<String>) -> Self {
        Self {
            failure_summary: Some(summary.into()),
        }
    }
}

/// Runtime adapter seam over the engine's agent runner.
#[async_trait]
pub trait AgentRunner: Send + Sync {
    /// Run one launched agent to completion.
    async fn run(&self, launch: AgentLaunch) -> Result<AgentRunReport>;
}

/// Launch descriptor for one workflow agent.
#[derive(Debug, Clone, PartialEq)]
pub struct AgentLaunch {
    /// Opaque agent-run id.
    pub agent_run_id: AgentRunId,
    /// Owning request.
    pub request_id: RequestId,
    /// Bound profile name.
    pub agent_name: AgentName,
    /// Persisted task instruction.
    pub instruction: String,
    /// Rendered context row.
    pub context: String,
    /// Rendered task guidance row.
    pub task_guidance: Option<String>,
    /// Resolved definition.
    pub agent_def: AgentDefinition,
}

impl AgentLaunch {
    /// Agent-run id.
    #[must_use]
    pub const fn agent_run_id(&self) -> &AgentRunId {
        &self.agent_run_id
    }
}

/// Per-attempt dependency bundle.
#[derive(Clone)]
pub struct AttemptResources {
    /// Workflow store.
    pub(crate) workflow_store: Arc<dyn WorkflowStore>,
    /// Iteration store.
    pub(crate) iteration_store: Arc<dyn IterationStore>,
    /// Attempt store.
    pub(crate) attempt_store: Arc<dyn AttemptStore>,
    /// Agent-run store.
    pub(crate) agent_run_store: Arc<dyn AgentRunStore>,
    /// Agent registry.
    pub(crate) agent_registry: Arc<AgentRegistry>,
    /// Active attempt registry.
    pub(crate) active_attempt_runs: Arc<ActiveAttemptRuns>,
    /// Open iteration coordinator registry.
    pub(crate) iteration_coordinators: Option<Arc<OpenIterationCoordinatorRegistry>>,
    /// Lifecycle knobs.
    pub(crate) lifecycle_config: WorkflowLifecycleConfig,
    /// Agent runner seam.
    pub(crate) runner: Arc<dyn AgentRunner>,
    /// Per-attempt worker run cap.
    pub(crate) max_concurrent_worker_runs: usize,
}

impl std::fmt::Debug for AttemptResources {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AttemptResources")
            .field(
                "max_concurrent_worker_runs",
                &self.max_concurrent_worker_runs,
            )
            .field(
                "has_iteration_coordinators",
                &self.iteration_coordinators.is_some(),
            )
            .finish_non_exhaustive()
    }
}

impl AttemptResources {
    /// Create deps with sane workflow defaults.
    #[must_use]
    pub fn new(
        workflow_store: Arc<dyn WorkflowStore>,
        iteration_store: Arc<dyn IterationStore>,
        attempt_store: Arc<dyn AttemptStore>,
        agent_run_store: Arc<dyn AgentRunStore>,
        agent_registry: Arc<AgentRegistry>,
        runner: Arc<dyn AgentRunner>,
    ) -> Self {
        Self {
            workflow_store,
            iteration_store,
            attempt_store,
            agent_run_store,
            agent_registry,
            runner,
            active_attempt_runs: Arc::new(ActiveAttemptRuns::new()),
            iteration_coordinators: None,
            lifecycle_config: WorkflowLifecycleConfig::default(),
            max_concurrent_worker_runs: 8,
        }
    }

    /// Use a caller-owned active attempt registry.
    #[must_use]
    pub fn with_active_attempt_runs(mut self, registry: Arc<ActiveAttemptRuns>) -> Self {
        self.active_attempt_runs = registry;
        self
    }

    /// Use a caller-owned open-iteration coordinator registry.
    #[must_use]
    pub fn with_iteration_coordinators(
        mut self,
        registry: Arc<OpenIterationCoordinatorRegistry>,
    ) -> Self {
        self.iteration_coordinators = Some(registry);
        self
    }

    /// Use caller-supplied lifecycle knobs.
    #[must_use]
    pub fn with_lifecycle_config(mut self, config: WorkflowLifecycleConfig) -> Self {
        self.lifecycle_config = config;
        self
    }

    /// Use a caller-supplied per-attempt worker-run concurrency cap.
    #[must_use]
    pub fn with_max_concurrent_worker_runs(mut self, max: usize) -> Self {
        self.max_concurrent_worker_runs = max;
        self
    }

    pub(crate) async fn request_id_for_attempt(&self, attempt: &Attempt) -> Result<RequestId> {
        let workflow = self
            .workflow_store
            .get(&attempt.workflow_id)
            .await?
            .ok_or_else(|| WorkflowError::not_found("workflow", attempt.workflow_id.as_str()))?;
        Ok(workflow.request_id)
    }
}

/// Role-parametrized launch factory.
#[derive(Debug, Clone)]
pub struct AgentLaunchFactory {
    deps: AttemptResources,
}

impl AgentLaunchFactory {
    /// Create a launch factory.
    #[must_use]
    pub fn new(deps: AttemptResources) -> Self {
        Self { deps }
    }

    pub(crate) async fn for_planner(
        &self,
        attempt: &Attempt,
        agent_run_id: AgentRunId,
    ) -> Result<AgentLaunch> {
        let agent_name = AgentName::new("planner")?;
        let agent_def = self.agent_definition(&agent_name, AgentType::Planner)?;
        let context = render_planner_agent_context(&self.deps, attempt).await?;
        let context_xml = render_context_xml(&context);
        Ok(AgentLaunch {
            agent_run_id,
            request_id: self.deps.request_id_for_attempt(attempt).await?,
            agent_name,
            instruction: context_xml.clone(),
            context: context_xml,
            task_guidance: Some(wrap_task_guidance(
                &render_task_guidance(&context),
                &agent_def,
            )),
            agent_def,
        })
    }

    pub(crate) async fn for_worker(
        &self,
        attempt: &Attempt,
        work_item: &WorkItemSpec,
        agent_run_id: AgentRunId,
    ) -> Result<AgentLaunch> {
        let agent_def = self.agent_definition(&work_item.agent_name, AgentType::Worker)?;
        let context = render_worker_agent_context(&self.deps, attempt, work_item).await?;
        let context_xml = render_context_xml(&context);
        Ok(AgentLaunch {
            agent_run_id,
            request_id: self.deps.request_id_for_attempt(attempt).await?,
            agent_name: work_item.agent_name.clone(),
            instruction: work_item.work_spec.clone(),
            context: context_xml,
            task_guidance: Some(wrap_task_guidance(
                &render_task_guidance(&context),
                &agent_def,
            )),
            agent_def,
        })
    }

    fn agent_definition(
        &self,
        agent_name: &AgentName,
        expected_type: AgentType,
    ) -> Result<AgentDefinition> {
        let agent_def = self
            .deps
            .agent_registry
            .get(agent_name)
            .ok_or_else(|| {
                WorkflowError::AgentDefinition(format!(
                    "workflow agent definition {:?} is not registered",
                    agent_name.as_str()
                ))
            })?
            .as_ref()
            .clone();
        if agent_def.agent_type != expected_type {
            return Err(WorkflowError::invariant(format!(
                "workflow launch is bound to agent {:?} with type {:?}, expected {:?}",
                agent_name.as_str(),
                agent_def.agent_type,
                expected_type
            )));
        }
        Ok(agent_def)
    }
}
