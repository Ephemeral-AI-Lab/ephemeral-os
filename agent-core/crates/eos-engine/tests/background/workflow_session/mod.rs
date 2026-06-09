#![allow(clippy::expect_used)]

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use eos_types::{
    OpenDelegatedWorkflow, StartWorkflowRequest, StartedWorkflow, TerminalWorkflow,
    WorkflowApiError,
};

use crate::background::notification::BackgroundNotificationEmitter;
use crate::EngineNotificationQueue;
use tokio::time::{sleep, timeout};

use super::*;

#[derive(Debug)]
struct AlwaysSucceededService;

#[async_trait]
impl WorkflowApi for AlwaysSucceededService {
    async fn start_workflow(
        &self,
        _request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, WorkflowApiError> {
        unreachable!("not used")
    }

    async fn check_workflow_status(
        &self,
        _workflow_id: &WorkflowId,
    ) -> Result<String, WorkflowApiError> {
        unreachable!("not used")
    }

    async fn cancel_workflow(
        &self,
        _workflow_id: &WorkflowId,
        _reason: &str,
    ) -> Result<String, WorkflowApiError> {
        Ok("cancelled".to_owned())
    }

    async fn poll_terminal_workflow(
        &self,
        workflow_id: &WorkflowId,
    ) -> Result<Option<TerminalWorkflow>, WorkflowApiError> {
        Ok(Some(TerminalWorkflow {
            workflow_id: workflow_id.clone(),
            status: WorkflowTerminalStatus::Completed,
        }))
    }

    async fn list_open_delegated_workflows_for_agent_run(
        &self,
        _agent_run_id: &AgentRunId,
    ) -> Result<Vec<OpenDelegatedWorkflow>, WorkflowApiError> {
        Ok(Vec::new())
    }

    async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, WorkflowApiError> {
        Ok(1)
    }
}

#[derive(Debug, Default)]
struct CountingWorkflowService {
    polls: AtomicUsize,
}

impl CountingWorkflowService {
    fn poll_count(&self) -> usize {
        self.polls.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl WorkflowApi for CountingWorkflowService {
    async fn start_workflow(
        &self,
        _request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, WorkflowApiError> {
        unreachable!("not used")
    }

    async fn check_workflow_status(
        &self,
        _workflow_id: &WorkflowId,
    ) -> Result<String, WorkflowApiError> {
        unreachable!("not used")
    }

    async fn cancel_workflow(
        &self,
        _workflow_id: &WorkflowId,
        _reason: &str,
    ) -> Result<String, WorkflowApiError> {
        Ok("cancelled".to_owned())
    }

    async fn poll_terminal_workflow(
        &self,
        workflow_id: &WorkflowId,
    ) -> Result<Option<TerminalWorkflow>, WorkflowApiError> {
        self.polls.fetch_add(1, Ordering::SeqCst);
        Ok(Some(TerminalWorkflow {
            workflow_id: workflow_id.clone(),
            status: WorkflowTerminalStatus::Completed,
        }))
    }

    async fn list_open_delegated_workflows_for_agent_run(
        &self,
        _agent_run_id: &AgentRunId,
    ) -> Result<Vec<OpenDelegatedWorkflow>, WorkflowApiError> {
        Ok(Vec::new())
    }

    async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, WorkflowApiError> {
        Ok(1)
    }
}

fn manager(notifier: &EngineNotificationQueue) -> WorkflowSessionManager {
    let workflow_service: Arc<dyn WorkflowApi> = Arc::new(AlwaysSucceededService);
    WorkflowSessionManager::new(
        "owner-run".parse().expect("agent run id"),
        workflow_service,
        BackgroundNotificationEmitter::new(notifier.clone()),
    )
}

fn manager_with_service(
    notifier: &EngineNotificationQueue,
    workflow_service: Arc<dyn WorkflowApi>,
) -> WorkflowSessionManager {
    WorkflowSessionManager::new(
        "owner-run".parse().expect("agent run id"),
        workflow_service,
        BackgroundNotificationEmitter::new(notifier.clone()),
    )
}

#[tokio::test]
async fn poll_push_notification_and_cancel_are_manager_owned() {
    let notifier = EngineNotificationQueue::new();
    let manager = manager(&notifier);
    manager
        .register_background_session(&StartedWorkflow {
            workflow_id: "workflow-1".parse().expect("workflow id"),
            workflow_goal: "goal".to_owned(),
        })
        .await;
    assert_eq!(manager.count().await, 1);

    let completions = manager.poll_completions().await;
    assert_eq!(completions.len(), 1);
    for completion in completions {
        manager.push_notification_on_completion(completion).await;
    }
    assert_eq!(manager.count().await, 0);
    let notifications = notifier.drain().await;
    assert_eq!(notifications.len(), 1);
    assert!(notifications[0]
        .message
        .contains("[BACKGROUND COMPLETED] workflow_id=workflow-1"));

    manager
        .register_background_session(&StartedWorkflow {
            workflow_id: "workflow-2".parse().expect("workflow id"),
            workflow_goal: "goal".to_owned(),
        })
        .await;
    assert_eq!(manager.count().await, 1);
    manager.cancel("parent exited").await;
    assert_eq!(manager.count().await, 0);
}

#[tokio::test]
async fn monitor_sleeps_until_workflow_session_is_registered() {
    let notifier = EngineNotificationQueue::new();
    let workflow_service = Arc::new(CountingWorkflowService::default());
    let manager = manager_with_service(&notifier, workflow_service.clone());
    let _monitor = WorkflowSessionMonitor::spawn(manager.clone(), Duration::from_millis(1));

    sleep(Duration::from_millis(20)).await;
    assert_eq!(
        workflow_service.poll_count(),
        0,
        "idle monitor should not poll before a workflow is registered"
    );

    manager
        .register_background_session(&StartedWorkflow {
            workflow_id: "workflow-1".parse().expect("workflow id"),
            workflow_goal: "goal".to_owned(),
        })
        .await;

    let notifications = timeout(Duration::from_millis(200), async {
        loop {
            let drained = notifier.drain().await;
            if !drained.is_empty() {
                break drained;
            }
            sleep(Duration::from_millis(2)).await;
        }
    })
    .await
    .expect("workflow completion notification");

    assert_eq!(workflow_service.poll_count(), 1);
    assert_eq!(notifications.len(), 1);
    assert!(notifications[0]
        .message
        .contains("[BACKGROUND COMPLETED] workflow_id=workflow-1"));
}
