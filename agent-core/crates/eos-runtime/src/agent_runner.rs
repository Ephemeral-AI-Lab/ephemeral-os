//! The `eos-workflow` [`AgentRunner`] adapter: runs one delegated-workflow agent
//! (planner / generator / reducer) through the shared engine loop.
//!
//! **Path A-recording (Phase-7 complete).** This runner is a thin engine-run
//! wrapper. The submit tool drives the harness *during* the run: a
//! `submit_planner/generator/reducer_outcome` resolves the wired recording
//! [`AttemptSubmissionPort`] from `ExecutionMetadata.attempt_submission` and records
//! the agent's real submission straight to the per-attempt orchestrator's
//! non-advancing `record_*` variants (materialize / mark task Done|Failed). The
//! runner therefore does not ferry a typed terminal back — it reports only
//! whether the engine run itself broke (`failure_summary = run.error`). The
//! single `advance_run_stage` loop owns launching + closure (D4: exactly one
//! writer), and catches a dead agent (one that never submitted) at join time via
//! the still-RUNNING exhaustion guard. The parent task is never mutated
//! (GC-eos-runtime-03).

use std::sync::{Arc, OnceLock};

use async_trait::async_trait;
use eos_agent_def::AgentRole;
use eos_agent_message_records::{AgentRunRecordKind, WorkflowTaskRole};
use eos_agent_run::{AgentRunApi, SpawnAgentRequest};
use eos_engine::{
    AgentRunControlFactory, AgentRunRegistry, AgentRunService, AgentRunServiceOptions,
};
use eos_llm_client::Message;
use eos_tools::{AttemptSubmissionPort, AttemptSubmissionService};
use eos_types::{AgentRunId, WorkflowApi};
use eos_workflow::{AgentLaunch, AgentRunReport, AgentRunner, Result as WorkflowResult};

use crate::runtime_services::RuntimeServices;

/// Runtime adapter over the shared engine loop, supplied to `AttemptDeps.runner`.
pub(crate) struct RuntimeAgentRunner {
    services: RuntimeServices,
    workspace_root: String,
    /// The recording attempt-submission port (the wired `AttemptSubmissionAdapter`
    /// over the shared attempt registry). Stateless and shared across all runs.
    attempt_submission: Arc<dyn AttemptSubmissionPort>,
    /// The workflow-control port, late-bound at composition (it is built
    /// downstream of this runner via the `starter→attempt_deps→runner` chain).
    /// `get()` is `Some` by the time any run starts, so workflow agents' hooks
    /// can read `workflow_depth` (deferral) and `find_outstanding` (no-inflight).
    workflow_service: Arc<OnceLock<Arc<dyn WorkflowApi>>>,
    /// Request-scoped factory that mints one fresh `AgentRunControl` (notifier,
    /// foreground, background supervisor, heartbeat, cancellation) per run — the
    /// runner stores no per-agent mutable supervisor or notifier.
    control_factory: Arc<AgentRunControlFactory>,
    /// Live-run registry for recursive cancellation.
    agent_run_registry: AgentRunRegistry,
}

impl std::fmt::Debug for RuntimeAgentRunner {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RuntimeAgentRunner").finish_non_exhaustive()
    }
}

impl RuntimeAgentRunner {
    pub(crate) fn new(
        services: RuntimeServices,
        workspace_root: impl Into<String>,
        attempt_submission: Arc<dyn AttemptSubmissionPort>,
        workflow_service: Arc<OnceLock<Arc<dyn WorkflowApi>>>,
        control_factory: Arc<AgentRunControlFactory>,
        agent_run_registry: AgentRunRegistry,
    ) -> Self {
        Self {
            services,
            workspace_root: workspace_root.into(),
            attempt_submission,
            workflow_service,
            control_factory,
            agent_run_registry,
        }
    }
}

#[async_trait]
impl AgentRunner for RuntimeAgentRunner {
    async fn run(&self, launch: AgentLaunch) -> WorkflowResult<AgentRunReport> {
        let agent_run_id = AgentRunId::new_v4();
        let mut prompt = launch.context().to_owned();
        if let Some(guidance) = launch.task_guidance() {
            prompt.push_str("\n\n");
            prompt.push_str(guidance);
        }
        if let Some(skill) = launch.skill() {
            prompt.push_str("\n\n");
            prompt.push_str(skill);
        }

        let agent_runs = AgentRunService::with_run_services(
            self.services.engine_run_handles(&self.workspace_root),
            (*self.control_factory).clone(),
            AgentRunServiceOptions {
                agent_run_registry: Some(self.agent_run_registry.clone()),
                attempt_submission: Some(AttemptSubmissionService::new(
                    self.attempt_submission.clone(),
                )),
                workflow_service: self.workflow_service.get().cloned(),
                ..AgentRunServiceOptions::default()
            },
        );
        let failure_summary = match agent_runs
            .spawn_agent(SpawnAgentRequest {
                agent_name: launch.agent_def().name.clone(),
                agent_run_id: Some(agent_run_id),
                initial_messages: vec![Message::from_user_text(prompt)],
                parent_agent_run_id: None,
                request_id: Some(launch.request_id().clone()),
                task_id: Some(launch.task_id().clone()),
                attempt_id: Some(launch.attempt_id().clone()),
                workflow_id: Some(launch.workflow_id().clone()),
                sandbox_id: None,
                workspace_root: self.workspace_root.clone(),
                is_isolated_workspace_mode: false,
                persist: true,
                record_kind: AgentRunRecordKind::WorkflowTask {
                    workflow_id: launch.workflow_id().clone(),
                    iteration_id: launch.iteration_id().clone(),
                    attempt_id: launch.attempt_id().clone(),
                    role: workflow_message_record_role(launch.role()),
                },
            })
            .await
        {
            Ok(agent_run_id) => agent_runs
                .wait_for_agent_outcome(&agent_run_id)
                .await
                .map_or_else(|err| Some(err.to_string()), |outcome| outcome.error),
            Err(err) => Some(err.to_string()),
        };

        // The submit tool already recorded the agent's submission during the run
        // (Path A-recording); the runner reports only a framework fault, which
        // the loop uses as the still-RUNNING exhaustion summary for a dead agent.
        Ok(AgentRunReport { failure_summary })
    }
}

fn workflow_message_record_role(role: AgentRole) -> WorkflowTaskRole {
    match role {
        AgentRole::Planner => WorkflowTaskRole::Planner,
        AgentRole::Generator => WorkflowTaskRole::Generator,
        AgentRole::Reducer => WorkflowTaskRole::Reducer,
        AgentRole::Root | AgentRole::Helper | AgentRole::Subagent => WorkflowTaskRole::Generator,
    }
}
