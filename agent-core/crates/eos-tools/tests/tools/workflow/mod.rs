#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use eos_types::{
    AgentRunId, JsonObject, OutstandingWorkflow, StartWorkflowRequest, StartedWorkflow, TaskId,
    TerminalWorkflow, WorkflowApi, WorkflowApiError, WorkflowId,
};
use serde_json::json;

use super::super::{
    cancel_workflow::CancelWorkflow, check_workflow_status::CheckWorkflowStatus,
    delegate_workflow::DelegateWorkflow,
};
use crate::runtime::executor::ToolExecutor;
use crate::support::metadata;
use crate::WorkflowToolService;

fn obj(pairs: &[(&str, serde_json::Value)]) -> JsonObject {
    pairs
        .iter()
        .map(|(k, v)| ((*k).to_owned(), v.clone()))
        .collect()
}

#[derive(Default)]
struct RecordingWorkflowSessions {
    workflows: Mutex<Vec<String>>,
}

fn workflow_tool_service(background: Arc<RecordingWorkflowSessions>) -> WorkflowToolService {
    WorkflowToolService::new(move |workflow: StartedWorkflow| {
        let background = background.clone();
        async move {
            background
                .workflows
                .lock()
                .unwrap()
                .push(workflow.workflow_id.as_str().to_owned());
        }
    })
}

struct OutstandingControl;

#[async_trait]
impl WorkflowApi for OutstandingControl {
    async fn start_workflow(
        &self,
        _request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, WorkflowApiError> {
        unreachable!("outstanding short-circuit returns before start")
    }

    async fn check_workflow_status(
        &self,
        _workflow_id: &WorkflowId,
    ) -> Result<String, WorkflowApiError> {
        unreachable!()
    }

    async fn cancel_workflow(
        &self,
        _workflow_id: &WorkflowId,
        _reason: &str,
    ) -> Result<String, WorkflowApiError> {
        unreachable!()
    }

    async fn poll_terminal_workflow(
        &self,
        _workflow_id: &WorkflowId,
    ) -> Result<Option<TerminalWorkflow>, WorkflowApiError> {
        unreachable!()
    }

    async fn find_outstanding_workflows(
        &self,
        _parent_task_id: &TaskId,
        _agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, WorkflowApiError> {
        Ok(vec![OutstandingWorkflow {
            workflow_id: WorkflowId::new_v4(),
            workflow_goal: "prior goal".to_owned(),
        }])
    }

    async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, WorkflowApiError> {
        Ok(1)
    }
}

#[tokio::test]
async fn delegate_workflow_outstanding_is_error() {
    let mut ctx = metadata();
    ctx.task_id = Some("parent".parse().unwrap());

    let res = DelegateWorkflow::new(
        Some(Arc::new(OutstandingControl)),
        Some(workflow_tool_service(Arc::new(
            RecordingWorkflowSessions::default(),
        ))),
    )
    .execute(&obj(&[("goal", json!("do something"))]), &ctx)
    .await
    .expect("ok");

    assert!(res.is_error, "outstanding-workflow branch must be is_error");
    assert!(res.output.contains("already outstanding"), "{}", res.output);
}

struct StartingControl;

#[async_trait]
impl WorkflowApi for StartingControl {
    async fn start_workflow(
        &self,
        _request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, WorkflowApiError> {
        Ok(StartedWorkflow {
            workflow_id: "workflow-1".parse()?,
            workflow_goal: "do something".to_owned(),
        })
    }

    async fn check_workflow_status(
        &self,
        _workflow_id: &WorkflowId,
    ) -> Result<String, WorkflowApiError> {
        unreachable!()
    }

    async fn cancel_workflow(
        &self,
        _workflow_id: &WorkflowId,
        _reason: &str,
    ) -> Result<String, WorkflowApiError> {
        unreachable!()
    }

    async fn poll_terminal_workflow(
        &self,
        _workflow_id: &WorkflowId,
    ) -> Result<Option<TerminalWorkflow>, WorkflowApiError> {
        unreachable!()
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

#[tokio::test]
async fn delegate_workflow_registers_background_session() {
    let background = Arc::new(RecordingWorkflowSessions::default());
    let mut ctx = metadata();
    ctx.task_id = Some("parent".parse().unwrap());

    let res = DelegateWorkflow::new(
        Some(Arc::new(StartingControl)),
        Some(workflow_tool_service(background.clone())),
    )
    .execute(&obj(&[("goal", json!("do something"))]), &ctx)
    .await
    .expect("ok");

    assert!(!res.is_error, "{res:?}");
    assert_eq!(
        background.workflows.lock().unwrap().as_slice(),
        ["workflow-1"],
        "delegate_workflow must register the workflow as background work"
    );
}

#[tokio::test]
async fn workflow_tools_reject_empty_ids() {
    let ctx = metadata();

    let check = CheckWorkflowStatus::new(None)
        .execute(&obj(&[("workflow_id", json!(""))]), &ctx)
        .await
        .expect("ok");
    assert!(check.is_error);
    assert!(check.output.contains("workflow_id"), "{}", check.output);

    let cancel = CancelWorkflow::new(None)
        .execute(&obj(&[("workflow_id", json!(""))]), &ctx)
        .await
        .expect("ok");
    assert!(cancel.is_error);
    assert!(cancel.output.contains("workflow_id"));
}
