#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use eos_agent_run::{
    AgentRunApi, AgentRunError, AgentRunOutcome, AgentRunStatus, SpawnAgentRequest,
};
use eos_types::{AgentRunId, JsonObject};
use serde_json::json;

use super::super::{cancel_subagent::CancelSubagent, run_subagent::RunSubagent};
use crate::runtime::executor::ToolExecutor;
use crate::support::metadata;
use crate::SubagentToolService;

#[derive(Default)]
struct FakeBackgroundSession {
    spawned: Mutex<Vec<(String, String)>>,
}

#[async_trait]
impl AgentRunApi for FakeBackgroundSession {
    async fn spawn_agent(&self, request: SpawnAgentRequest) -> Result<AgentRunId, AgentRunError> {
        self.spawned.lock().unwrap().push((
            request.agent_name.as_str().to_owned(),
            first_user_text(&request),
        ));
        Ok("agent-run-child".parse().unwrap())
    }

    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        Ok(AgentRunOutcome {
            agent_run_id: agent_run_id.clone(),
            status: AgentRunStatus::Completed,
            submission_payload: Some(obj(&[
                ("output", json!("done")),
                ("is_error", json!(false)),
                ("metadata", json!({})),
                ("is_terminal", json!(true)),
            ])),
            message_history: Vec::new(),
            token_count: None,
            error: None,
        })
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

fn first_user_text(request: &SpawnAgentRequest) -> String {
    request
        .initial_messages
        .first()
        .and_then(|message| message.content.first())
        .and_then(|block| match block {
            eos_llm_client::ContentBlock::Text { text } => Some(text.clone()),
            _ => None,
        })
        .unwrap_or_default()
}

fn subagent_service() -> SubagentToolService {
    SubagentToolService::new(
        |_agent_run_id| async {},
        |agent_run_id, _reason| async move { agent_run_id.as_str() == "agent-run-child" },
        || async { 0 },
        |_reason| async {},
    )
}

fn obj(pairs: &[(&str, serde_json::Value)]) -> JsonObject {
    pairs
        .iter()
        .map(|(k, v)| ((*k).to_owned(), v.clone()))
        .collect()
}

#[tokio::test]
async fn run_subagent_returns_agent_run_id() {
    let background = Arc::new(FakeBackgroundSession::default());
    let subagent_sessions = subagent_service();
    let ctx = metadata();

    let res = RunSubagent::new(Some(background.clone()), Some(subagent_sessions))
        .execute(
            &obj(&[
                ("agent_name", json!("explorer")),
                ("prompt", json!("inspect the plan")),
            ]),
            &ctx,
        )
        .await
        .expect("ok");

    assert!(!res.is_error, "{}", res.output);
    assert!(res.output.contains("[SUBAGENT LAUNCHED]"), "{}", res.output);
    assert_eq!(res.metadata["agent_run_id"], json!("agent-run-child"));
    assert_eq!(res.metadata["status"], json!("running"));
    assert_eq!(
        background.spawned.lock().unwrap().as_slice(),
        &[("explorer".to_owned(), "inspect the plan".to_owned())]
    );
}

#[tokio::test]
async fn cancel_subagent_rejects_empty_agent_run_id() {
    let ctx = metadata();
    let cancel = CancelSubagent::new(None)
        .execute(
            &obj(&[("agent_run_id", json!("")), ("reason", json!("x"))]),
            &ctx,
        )
        .await
        .expect("ok");
    assert!(cancel.is_error);
    assert!(cancel.output.contains("agent_run_id"));
}
