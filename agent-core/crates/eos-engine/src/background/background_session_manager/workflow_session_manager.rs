use std::collections::HashMap;
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use async_trait::async_trait;
use eos_types::{AgentRunId, StartedWorkflow, WorkflowApi, WorkflowId, WorkflowTerminalStatus};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

use super::{BackgroundSession, BackgroundSessionManager, BackgroundSessionStatus};
use crate::background::notification::{BackgroundCompletion, BackgroundNotificationEmitter};

pub(in crate::background) type WorkflowServiceCell = Arc<OnceLock<Arc<dyn WorkflowApi>>>;

/// One delegated workflow tracked as background work for the owning agent run,
/// keyed by its natural [`WorkflowId`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub(in crate::background) struct WorkflowSession {
    workflow_id: WorkflowId,
    status: BackgroundSessionStatus,
}

impl WorkflowSession {
    fn running(workflow_id: WorkflowId) -> Self {
        Self {
            workflow_id,
            status: BackgroundSessionStatus::Running,
        }
    }

    fn workflow_id(&self) -> &WorkflowId {
        &self.workflow_id
    }

    const fn status(&self) -> BackgroundSessionStatus {
        self.status
    }

    fn cancel(&mut self) -> bool {
        if !matches!(self.status, BackgroundSessionStatus::Running) {
            return false;
        }
        self.status = BackgroundSessionStatus::Cancelled;
        true
    }

    fn settle_running(&mut self, status: BackgroundSessionStatus) -> bool {
        if !matches!(self.status, BackgroundSessionStatus::Running) {
            return false;
        }
        self.status = status;
        true
    }
}

impl BackgroundSession for WorkflowSession {
    type Id = WorkflowId;

    fn id(&self) -> &Self::Id {
        &self.workflow_id
    }
}

#[derive(Debug, Clone)]
pub(in crate::background) struct WorkflowCompletion {
    pub(super) workflow_id: WorkflowId,
    pub(super) status: BackgroundSessionStatus,
}

/// Tracks delegated workflow sessions for one agent run.
#[derive(Clone)]
pub(in crate::background) struct WorkflowSessionManager {
    agent_run_id: AgentRunId,
    sessions: Arc<Mutex<HashMap<WorkflowId, WorkflowSession>>>,
    workflow_service: WorkflowServiceCell,
    notification: BackgroundNotificationEmitter,
}

impl std::fmt::Debug for WorkflowSessionManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkflowSessionManager")
            .field("agent_run_id", &self.agent_run_id)
            .finish_non_exhaustive()
    }
}

impl WorkflowSessionManager {
    pub(in crate::background) fn new(
        agent_run_id: AgentRunId,
        workflow_service: WorkflowServiceCell,
        notification: BackgroundNotificationEmitter,
    ) -> Self {
        Self {
            agent_run_id,
            sessions: Arc::new(Mutex::new(HashMap::new())),
            workflow_service,
            notification,
        }
    }

    pub(in crate::background) async fn register_background_session(
        &self,
        workflow: &StartedWorkflow,
    ) {
        self.insert(WorkflowSession::running(workflow.workflow_id.clone()))
            .await;
    }

    pub(in crate::background) async fn cancel_session(&self, workflow_id: &WorkflowId) -> bool {
        self.sessions
            .lock()
            .await
            .get_mut(workflow_id)
            .is_some_and(WorkflowSession::cancel)
    }

    pub(in crate::background) async fn cancel_background_sessions(&self, reason: &str) {
        let workflow_service = self.workflow_service.get().cloned();
        let running = self.running_ids().await;
        for workflow_id in &running {
            if let Some(service) = &workflow_service {
                if let Err(err) = service.cancel_workflow(workflow_id, reason).await {
                    tracing::warn!(
                        error = %err,
                        workflow_id = workflow_id.as_str(),
                        "background workflow cancellation failed"
                    );
                }
            }
            let _ = self.cancel_session(workflow_id).await;
        }
    }

    pub(super) async fn running_ids(&self) -> Vec<WorkflowId> {
        self.sessions
            .lock()
            .await
            .values()
            .filter(|session| matches!(session.status(), BackgroundSessionStatus::Running))
            .map(|session| session.workflow_id().clone())
            .collect()
    }

    async fn running_sessions(&self) -> Vec<WorkflowSession> {
        self.sessions
            .lock()
            .await
            .values()
            .filter(|session| matches!(session.status(), BackgroundSessionStatus::Running))
            .cloned()
            .collect()
    }

    async fn settle_running(
        &self,
        workflow_id: &WorkflowId,
        status: BackgroundSessionStatus,
    ) -> Option<WorkflowCompletion> {
        let mut guard = self.sessions.lock().await;
        let session = guard.get_mut(workflow_id)?;
        if !session.settle_running(status) {
            return None;
        }
        Some(WorkflowCompletion {
            workflow_id: session.workflow_id().clone(),
            status,
        })
    }

    pub(in crate::background) async fn poll_completions(&self) -> Vec<WorkflowCompletion> {
        let Some(workflow_service) = self.workflow_service.get().cloned() else {
            return Vec::new();
        };
        let mut completions = Vec::new();
        for session in self.running_sessions().await {
            let terminal = match workflow_service
                .poll_terminal_workflow(session.workflow_id())
                .await
            {
                Ok(terminal) => terminal,
                Err(_) => continue,
            };
            let Some(terminal) = terminal else {
                continue;
            };
            let status = match terminal.status {
                WorkflowTerminalStatus::Completed => BackgroundSessionStatus::Completed,
                WorkflowTerminalStatus::Failed => BackgroundSessionStatus::Failed,
                WorkflowTerminalStatus::Cancelled => BackgroundSessionStatus::Cancelled,
            };
            if let Some(completion) = self.settle_running(session.workflow_id(), status).await {
                completions.push(completion);
            }
        }
        completions
    }
}

pub(in crate::background) struct WorkflowSessionMonitor {
    join: JoinHandle<()>,
}

impl Drop for WorkflowSessionMonitor {
    fn drop(&mut self) {
        self.join.abort();
    }
}

impl WorkflowSessionMonitor {
    pub(in crate::background) fn spawn(
        manager: WorkflowSessionManager,
        interval: Duration,
    ) -> Self {
        Self {
            join: tokio::spawn(async move {
                loop {
                    for completion in manager.poll_completions().await {
                        manager.push_notification_on_completion(completion).await;
                    }
                    tokio::time::sleep(interval).await;
                }
            }),
        }
    }
}

#[async_trait]
impl BackgroundSessionManager for WorkflowSessionManager {
    type Session = WorkflowSession;
    type Completion = WorkflowCompletion;

    async fn insert(&self, session: Self::Session) {
        self.sessions
            .lock()
            .await
            .insert(session.id().clone(), session);
    }

    async fn count(&self) -> usize {
        self.sessions
            .lock()
            .await
            .values()
            .filter(|session| matches!(session.status(), BackgroundSessionStatus::Running))
            .count()
    }

    async fn push_notification_on_completion(&self, completion: Self::Completion) {
        let _ = self
            .notification
            .emit(BackgroundCompletion::Workflow {
                workflow_id: completion.workflow_id,
                status: completion.status,
            })
            .await;
    }

    async fn cancel(&self, reason: &str) {
        self.cancel_background_sessions(reason).await;
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use std::sync::{Arc, OnceLock};

    use async_trait::async_trait;
    use eos_types::{
        OutstandingWorkflow, StartWorkflowRequest, StartedWorkflow, TaskId, TerminalWorkflow,
        WorkflowApiError,
    };

    use crate::background::notification::BackgroundNotificationEmitter;
    use crate::NotificationService;

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

        async fn find_outstanding_workflows(
            &self,
            _parent_task_id: &TaskId,
            _agent_run_id: &AgentRunId,
        ) -> Result<Vec<OutstandingWorkflow>, WorkflowApiError> {
            Ok(Vec::new())
        }

        async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, WorkflowApiError> {
            Ok(1)
        }
    }

    fn manager(notifier: &NotificationService) -> WorkflowSessionManager {
        let cell: WorkflowServiceCell = Arc::new(OnceLock::new());
        let _ = cell.set(Arc::new(AlwaysSucceededService));
        WorkflowSessionManager::new(
            "owner-run".parse().expect("agent run id"),
            cell,
            BackgroundNotificationEmitter::new(notifier.clone()),
        )
    }

    #[tokio::test]
    async fn poll_push_notification_and_cancel_are_manager_owned() {
        let notifier = NotificationService::new();
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
}
