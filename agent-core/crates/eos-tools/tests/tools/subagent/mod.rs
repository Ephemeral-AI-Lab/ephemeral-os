#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use eos_types::{AgentRunId, JsonObject, SubagentSessionId};
use serde_json::json;

use super::super::{cancel_subagent::CancelSubagent, run_subagent::RunSubagent};
use crate::ports::{
    AgentRunServicePort, CancelledSubagent, Sealed, StartSubagentRunOutcome,
    StartSubagentRunRequest, StartedSubagentRun, SubagentProgress, SubagentSessionPort,
    SubagentSessionStatus, TerminalAgentRun,
};
use crate::runtime::executor::ToolExecutor;
use crate::support::metadata;

#[derive(Default)]
struct FakeBackgroundSession {
    spawned: Mutex<Vec<(String, String)>>,
}

impl Sealed for FakeBackgroundSession {}

#[async_trait]
impl AgentRunServicePort for FakeBackgroundSession {
    async fn start_subagent_run(
        &self,
        request: StartSubagentRunRequest,
    ) -> Result<StartSubagentRunOutcome, crate::ToolError> {
        self.spawned
            .lock()
            .unwrap()
            .push((request.agent_name.clone(), request.prompt));
        Ok(StartSubagentRunOutcome::Started(StartedSubagentRun {
            agent_run_id: "agent-run-child".parse().unwrap(),
            agent_name: request.agent_name,
        }))
    }

    async fn poll_terminal_agent_run(
        &self,
        _agent_run_id: &AgentRunId,
    ) -> Result<Option<TerminalAgentRun>, crate::ToolError> {
        Ok(None)
    }

    async fn cancel_agent_run(
        &self,
        _agent_run_id: &AgentRunId,
        _reason: &str,
    ) -> Result<(), crate::ToolError> {
        Ok(())
    }
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

    async fn subagent_session_snapshot(
        &self,
        subagent_session_id: &SubagentSessionId,
    ) -> Option<SubagentProgress> {
        Some(SubagentProgress::Found {
            subagent_session_id: subagent_session_id.clone(),
            status: SubagentSessionStatus::Running,
            agent_name: "explorer".to_owned(),
            result: None,
        })
    }

    async fn cancel_background_session(
        &self,
        subagent_session_id: &SubagentSessionId,
        reason: &str,
    ) -> CancelledSubagent {
        CancelledSubagent::Cancelled {
            subagent_session_id: subagent_session_id.clone(),
            reason: reason.to_owned(),
        }
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
