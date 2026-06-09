#![allow(clippy::expect_used)]

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use eos_types::{
    AgentName, AgentRun, AgentRunError, RequestId, SpawnAgentRequest, TaskId, TaskRole, ExecutionStatus,
    UtcDateTime,
};

use crate::EngineNotificationQueue;
use tokio::time::{sleep, timeout};

use super::*;

#[derive(Debug, Default)]
struct FakeAgentRunService;

#[async_trait]
impl AgentRunApi for FakeAgentRunService {
    async fn spawn_agent(&self, _request: SpawnAgentRequest) -> Result<AgentRunId, AgentRunError> {
        Ok(AgentRunId::new_v4())
    }

    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        Err(AgentRunError::NotActiveInProcess(agent_run_id.clone()))
    }

    async fn poll_agent_run_outcome(
        &self,
        _agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError> {
        Ok(None)
    }

    async fn cancel_agent_run(
        &self,
        _agent_run_id: &AgentRunId,
        _reason: &str,
    ) -> Result<(), AgentRunError> {
        Ok(())
    }
}

#[derive(Debug, Default)]
struct CompletingAgentRunService {
    polls: AtomicUsize,
}

impl CompletingAgentRunService {
    fn poll_count(&self) -> usize {
        self.polls.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl AgentRunApi for CompletingAgentRunService {
    async fn spawn_agent(&self, _request: SpawnAgentRequest) -> Result<AgentRunId, AgentRunError> {
        Ok(AgentRunId::new_v4())
    }

    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        Err(AgentRunError::NotActiveInProcess(agent_run_id.clone()))
    }

    async fn poll_agent_run_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError> {
        self.polls.fetch_add(1, Ordering::SeqCst);
        let mut payload = JsonObject::new();
        payload.insert("output".to_owned(), json!("findings"));
        payload.insert("is_error".to_owned(), json!(false));
        payload.insert("metadata".to_owned(), json!({}));
        payload.insert("is_terminal".to_owned(), json!(true));
        Ok(Some(AgentRunOutcome {
            agent_run_id: agent_run_id.clone(),
            status: AgentRunStatus::Completed,
            submission_payload: Some(payload),
            message_history: Vec::new(),
            token_count: Some(0),
            error: None,
        }))
    }

    async fn cancel_agent_run(
        &self,
        _agent_run_id: &AgentRunId,
        _reason: &str,
    ) -> Result<(), AgentRunError> {
        Ok(())
    }
}

fn manager(notifier: &EngineNotificationQueue) -> SubagentSessionManager {
    SubagentSessionManager::new(
        "owner-run".parse().expect("agent run id"),
        Arc::new(FakeAgentRunService),
        BackgroundNotificationEmitter::new(notifier.clone()),
    )
}

fn manager_with_service(
    notifier: &EngineNotificationQueue,
    agent_run_service: Arc<dyn AgentRunApi>,
) -> SubagentSessionManager {
    SubagentSessionManager::new(
        "owner-run".parse().expect("agent run id"),
        agent_run_service,
        BackgroundNotificationEmitter::new(notifier.clone()),
    )
}

fn finished_run(terminal_payload: Option<JsonObject>, error: Option<&str>) -> AgentRun {
    AgentRun {
        task_id: TaskId::new_v4(),
        agent_run_id: "run-sub-finished".parse().expect("agent run id"),
        request_id: RequestId::new_v4(),
        role: TaskRole::Root,
        status: if error.is_some() {
            ExecutionStatus::Failed
        } else {
            ExecutionStatus::Done
        },
        agent_name: AgentName::new("subagent").expect("valid agent name"),
        terminal_payload,
        submission_outcome: None,
        token_count: 0,
        error: error.map(str::to_owned),
        created_at: UtcDateTime::now(),
        updated_at: UtcDateTime::now(),
        finished_at: Some(UtcDateTime::now()),
    }
}

#[test]
fn submission_payload_settles_completed_and_finished() {
    let mut terminal = JsonObject::new();
    terminal.insert("output".to_owned(), json!("partial but delivered"));
    terminal.insert("is_error".to_owned(), json!(true));
    terminal.insert("metadata".to_owned(), json!({}));
    terminal.insert("is_terminal".to_owned(), json!(true));
    let (status, result, exit_code) =
        completion_from_agent_run(&finished_run(Some(terminal), None)).expect("completion");
    assert_eq!(status, BackgroundSessionStatus::Completed);
    assert!(result.is_error);
    assert_eq!(exit_code, 1);
    assert_eq!(result.output, "partial but delivered");
    assert_eq!(result.metadata["subagent_terminal_called"], json!(true));
}

#[test]
fn no_terminal_settles_failed() {
    let (status, result, _) =
        completion_from_agent_run(&finished_run(None, None)).expect("completion");
    assert_eq!(status, BackgroundSessionStatus::Failed);
    assert!(result.output.contains("without calling a terminal tool"));
}

#[tokio::test]
async fn count_cancel_and_completion_notification_are_manager_owned() {
    let notifier = EngineNotificationQueue::new();
    let manager = manager(&notifier);
    let running_id: AgentRunId = "run-sub-1".parse().expect("agent run id");
    manager
        .insert(SubagentSession::tracked(running_id.clone()))
        .await;

    assert_eq!(manager.count().await, 1);
    assert!(manager.cancel_one(&running_id, "not needed").await);
    assert_eq!(manager.count().await, 0);

    let done_id: AgentRunId = "run-sub-2".parse().expect("agent run id");
    manager
        .insert(SubagentSession::tracked(done_id.clone()))
        .await;
    let completion = manager
        .settle(
            &done_id,
            BackgroundSessionStatus::Completed,
            ToolResult::ok("findings"),
        )
        .await
        .expect("completion");
    manager.push_notification_on_completion(completion).await;

    let notifications = notifier.drain().await;
    assert_eq!(notifications.len(), 1);
    assert!(notifications[0]
        .message
        .contains("[BACKGROUND COMPLETED] agent_run_id=run-sub-2"));
    assert!(notifications[0].message.contains("findings"));
}

#[tokio::test]
async fn monitor_sleeps_until_subagent_session_is_registered() {
    let notifier = EngineNotificationQueue::new();
    let agent_run_service = Arc::new(CompletingAgentRunService::default());
    let manager = manager_with_service(&notifier, agent_run_service.clone());
    let _monitor = SubagentSessionMonitor::spawn(manager.clone(), Duration::from_millis(1));

    sleep(Duration::from_millis(20)).await;
    assert_eq!(
        agent_run_service.poll_count(),
        0,
        "idle monitor should not poll before a subagent is registered"
    );

    manager
        .register_background_session(&"run-sub-1".parse().expect("agent run id"))
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
    .expect("subagent completion notification");

    assert_eq!(agent_run_service.poll_count(), 1);
    assert_eq!(notifications.len(), 1);
    assert!(notifications[0]
        .message
        .contains("[BACKGROUND COMPLETED] agent_run_id=run-sub-1"));
    assert!(notifications[0].message.contains("findings"));
}

#[tokio::test]
async fn settling_same_terminal_subagent_completion_is_idempotent() {
    let notifier = EngineNotificationQueue::new();
    let manager = manager(&notifier);
    let done_id: AgentRunId = "run-sub-2".parse().expect("agent run id");
    manager
        .insert(SubagentSession::tracked(done_id.clone()))
        .await;

    let first = manager
        .settle(
            &done_id,
            BackgroundSessionStatus::Completed,
            ToolResult::ok("findings"),
        )
        .await;
    let second = manager
        .settle(
            &done_id,
            BackgroundSessionStatus::Completed,
            ToolResult::ok("duplicate findings"),
        )
        .await;

    assert!(first.is_some());
    assert!(second.is_none());
}
