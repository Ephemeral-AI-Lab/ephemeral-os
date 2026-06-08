#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use eos_types::{AgentRunId, JsonObject, SubagentSessionId};
use serde_json::json;

use super::super::{cancel_subagent::CancelSubagent, run_subagent::RunSubagent};
use crate::runtime::executor::ToolExecutor;
use crate::support::metadata;
use crate::{AgentRunServicePort, AgentSpawnError, Sealed, SpawnAgentRequest, SubagentSessionPort};

#[derive(Default)]
struct FakeBackgroundSession {
    spawned: Mutex<Vec<(String, String)>>,
}

impl Sealed for FakeBackgroundSession {}

#[async_trait]
impl AgentRunServicePort for FakeBackgroundSession {
    async fn spawn_agent(&self, request: SpawnAgentRequest) -> Result<AgentRunId, AgentSpawnError> {
        self.spawned.lock().unwrap().push((
            request.agent_name.as_str().to_owned(),
            first_user_text(&request),
        ));
        Ok("agent-run-child".parse().unwrap())
    }

    async fn wait_for_agent_result(
        &self,
        _agent_run_id: &AgentRunId,
    ) -> Result<crate::ToolResult, crate::ToolError> {
        Ok(crate::ToolResult::ok("done"))
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

#[async_trait]
impl SubagentSessionPort for FakeBackgroundSession {
    async fn register_background_session(
        &self,
        _agent_run_id: &AgentRunId,
        _agent_name: &str,
    ) -> SubagentSessionId {
        "subagent_1".parse().unwrap()
    }

    async fn cancel_background_agent_run(&self, agent_run_id: &AgentRunId, _reason: &str) -> bool {
        agent_run_id.as_str() == "agent-run-child"
    }

    async fn count_background_sessions(&self) -> usize {
        0
    }

    async fn cancel_all_background_sessions(&self, _reason: &str) {}

    async fn poll_complete_background_sessions(&self) -> usize {
        0
    }
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
    let ctx = metadata();

    let res = RunSubagent::new(Some(background.clone()), Some(background.clone()))
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
