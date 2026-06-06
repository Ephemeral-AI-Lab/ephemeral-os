//! Parent-agent exit cleanup for background work.

use std::sync::Arc;

use eos_tools::{BackgroundSupervisorPort, WorkflowControlPort};
use eos_types::AgentRunId;

use crate::query::{QueryContext, QueryExitReason};

pub(crate) struct BackgroundRunFinalizer {
    supervisor: Option<Arc<dyn BackgroundSupervisorPort>>,
    workflow_control: Option<Arc<dyn WorkflowControlPort>>,
    agent_run_ids: Vec<AgentRunId>,
    armed: bool,
}

impl BackgroundRunFinalizer {
    pub(crate) fn new(
        supervisor: Option<Arc<dyn BackgroundSupervisorPort>>,
        workflow_control: Option<Arc<dyn WorkflowControlPort>>,
        agent_run_ids: Vec<AgentRunId>,
    ) -> Self {
        Self {
            supervisor,
            workflow_control,
            agent_run_ids,
            armed: true,
        }
    }

    pub(crate) async fn finalize(&mut self, ctx: &QueryContext, error: Option<&str>) {
        let Some(supervisor) = &self.supervisor else {
            self.disarm();
            return;
        };
        let reason = finalize_reason(ctx.exit_reason, error);
        for agent_run_id in &self.agent_run_ids {
            supervisor
                .cancel_for_parent_exit(Some(agent_run_id), self.workflow_control.clone(), &reason)
                .await;
        }
        self.disarm();
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for BackgroundRunFinalizer {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        let Some(supervisor) = self.supervisor.take() else {
            return;
        };
        let workflow_control = self.workflow_control.take();
        let agent_run_ids = std::mem::take(&mut self.agent_run_ids);
        let reason = "engine run dropped before background finalization".to_owned();
        let Ok(handle) = tokio::runtime::Handle::try_current() else {
            tracing::warn!(
                "engine run dropped outside a Tokio runtime; background cleanup could not be spawned"
            );
            return;
        };
        handle.spawn(async move {
            for agent_run_id in agent_run_ids {
                supervisor
                    .cancel_for_parent_exit(Some(&agent_run_id), workflow_control.clone(), &reason)
                    .await;
            }
        });
    }
}

fn finalize_reason(exit_reason: Option<QueryExitReason>, error: Option<&str>) -> String {
    match (exit_reason, error) {
        (_, Some(error)) => format!("engine run failed: {error}"),
        (Some(QueryExitReason::TerminalNotSubmitted), None) => {
            "parent agent exited without submitting a terminal tool".to_owned()
        }
        (Some(QueryExitReason::ToolStop), None) => "parent agent submitted its terminal".to_owned(),
        (None, None) => "parent agent exited".to_owned(),
    }
}
