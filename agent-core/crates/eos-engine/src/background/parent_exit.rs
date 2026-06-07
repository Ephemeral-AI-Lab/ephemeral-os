//! Normal-exit background cleanup for one agent run.
//!
//! When `run_agent` finishes naturally (the agent submitted its terminal, ran
//! out of turns, or faulted), this finalizer tears down any background work the
//! run still owns through the per-run supervisor handle's `teardown` (settle +
//! abort subagents, cancel delegated workflows, and cancel command sessions in
//! one per-caller daemon RPC). Hard cancellation (`cancel_agent_run`) goes
//! through the same `teardown`; this is the awaited normal-exit path, with a
//! `Drop` backstop only for an unexpected early drop.

use std::sync::Arc;

use eos_tools::{BackgroundSupervisorPort, WorkflowControlPort};

use crate::query::{QueryContext, QueryExitReason};

pub(crate) struct BackgroundRunFinalizer {
    supervisor: Option<Arc<dyn BackgroundSupervisorPort>>,
    workflow_control: Option<Arc<dyn WorkflowControlPort>>,
    armed: bool,
}

impl BackgroundRunFinalizer {
    pub(crate) fn new(
        supervisor: Option<Arc<dyn BackgroundSupervisorPort>>,
        workflow_control: Option<Arc<dyn WorkflowControlPort>>,
    ) -> Self {
        Self {
            supervisor,
            workflow_control,
            armed: true,
        }
    }

    pub(crate) async fn finalize(&mut self, ctx: &QueryContext, error: Option<&str>) {
        let Some(supervisor) = &self.supervisor else {
            self.disarm();
            return;
        };
        let reason = finalize_reason(ctx.exit_reason, error);
        supervisor
            .teardown(self.workflow_control.clone(), &reason)
            .await;
        self.disarm();
    }

    /// Disarm without running cleanup: the caller has handed background teardown
    /// to another owner (e.g. a concurrent `cancel_agent_run` won the
    /// finalization claim), so neither `finalize` nor the `Drop` backstop should
    /// fire a second teardown.
    pub(crate) fn disarm(&mut self) {
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
        let reason = "engine run dropped before background finalization".to_owned();
        let Ok(handle) = tokio::runtime::Handle::try_current() else {
            tracing::warn!(
                "engine run dropped outside a Tokio runtime; background cleanup could not be spawned"
            );
            return;
        };
        handle.spawn(async move {
            supervisor.teardown(workflow_control, &reason).await;
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

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use async_trait::async_trait;
    use eos_tools::{
        RunningBackgroundTasks, SpawnedSubagent, StartedSubagent, StartedWorkflowHandle, ToolError,
        ToolResult,
    };
    use eos_types::{SubagentSessionId, WorkflowSessionId};
    use tokio::sync::mpsc;
    use tokio::time::{timeout, Duration};

    use super::*;

    #[derive(Debug)]
    struct RecordingSupervisor {
        tx: mpsc::UnboundedSender<String>,
    }

    impl eos_tools::ports::Sealed for RecordingSupervisor {}

    fn empty_report() -> RunningBackgroundTasks {
        RunningBackgroundTasks {
            total: 0,
            subagents: 0,
            workflows: 0,
            command_sessions: 0,
        }
    }

    #[async_trait]
    impl BackgroundSupervisorPort for RecordingSupervisor {
        async fn spawn(
            &self,
            _ctx: &eos_tools::ExecutionMetadata,
            _agent_name: &str,
            _prompt: &str,
        ) -> Result<SpawnedSubagent, ToolError> {
            Ok(SpawnedSubagent::Launched(StartedSubagent {
                subagent_session_id: "subagent_1".parse().expect("subagent id"),
            }))
        }

        async fn progress(
            &self,
            _subagent_session_id: &SubagentSessionId,
            _last_n_messages: u8,
        ) -> Result<ToolResult, ToolError> {
            Ok(ToolResult::ok("running"))
        }

        async fn cancel(
            &self,
            _subagent_session_id: &SubagentSessionId,
            _reason: &str,
        ) -> Result<ToolResult, ToolError> {
            Ok(ToolResult::ok("cancelled"))
        }

        async fn running_background_tasks(&self) -> RunningBackgroundTasks {
            empty_report()
        }

        async fn cancel_subagents(&self) -> RunningBackgroundTasks {
            empty_report()
        }

        async fn register_workflow(&self, _workflow: &StartedWorkflowHandle) {}

        async fn cancel_workflow_record(
            &self,
            _workflow_task_id: &WorkflowSessionId,
            _reason: &str,
        ) -> bool {
            false
        }

        async fn teardown(
            &self,
            _workflow_control: Option<Arc<dyn WorkflowControlPort>>,
            reason: &str,
        ) -> RunningBackgroundTasks {
            self.tx.send(reason.to_owned()).expect("send cleanup");
            empty_report()
        }
    }

    #[tokio::test]
    async fn drop_spawns_background_cleanup_when_still_armed() {
        let (tx, mut rx) = mpsc::unbounded_channel();
        let supervisor = Arc::new(RecordingSupervisor { tx });

        {
            let _finalizer = BackgroundRunFinalizer::new(Some(supervisor), None);
        }

        let reason = timeout(Duration::from_millis(100), rx.recv())
            .await
            .expect("cleanup spawned")
            .expect("cleanup message");
        assert_eq!(reason, "engine run dropped before background finalization");
    }
}
