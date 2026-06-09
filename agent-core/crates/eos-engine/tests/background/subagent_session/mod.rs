#![allow(clippy::expect_used)]

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{AgentRun, AgentRunError, SpawnAgentRequest, UtcDateTime};

use crate::EngineNotificationQueue;

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

fn manager(notifier: &EngineNotificationQueue) -> SubagentSessionManager {
    SubagentSessionManager::new(
        "owner-run".parse().expect("agent run id"),
        Arc::new(FakeAgentRunService),
        BackgroundNotificationEmitter::new(notifier.clone()),
    )
}

fn finished_run(terminal_payload: Option<JsonObject>, error: Option<&str>) -> AgentRun {
    AgentRun {
        id: "run-sub-finished".parse().expect("agent run id"),
        task_id: None,
        agent_name: "subagent".to_owned(),
        terminal_payload,
        token_count: 0,
        error: error.map(str::to_owned),
        created_at: UtcDateTime::now(),
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
