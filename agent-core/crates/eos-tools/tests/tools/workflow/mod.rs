#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use eos_types::{AgentRunId, JsonObject, TaskId, WorkflowId, WorkflowSessionId};
use serde_json::json;

use super::super::{
    cancel_workflow::CancelWorkflow, check_workflow_status::CheckWorkflowStatus,
    delegate_workflow::DelegateWorkflow,
};
use crate::core::error::ToolError;
use crate::runtime::executor::ToolExecutor;
use crate::support::metadata;
use crate::{
    OutstandingWorkflow, Sealed, StartWorkflowRequest, StartedWorkflow, TerminalWorkflow,
    WorkflowServicePort, WorkflowToolService,
};

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

impl Sealed for RecordingWorkflowSessions {}

fn workflow_tool_service(background: Arc<RecordingWorkflowSessions>) -> WorkflowToolService {
    WorkflowToolService::new(move |workflow| {
        let background = background.clone();
        async move {
            background
                .workflows
                .lock()
                .unwrap()
                .push(workflow.workflow_task_id.as_str().to_owned());
        }
    })
}

struct OutstandingControl;

impl Sealed for OutstandingControl {}

#[async_trait]
impl WorkflowServicePort for OutstandingControl {
    async fn start_workflow(
        &self,
        _request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, ToolError> {
        unreachable!("outstanding short-circuit returns before start")
    }

    async fn check_workflow_status(
        &self,
        _workflow_id: &WorkflowId,
        _workflow_task_id: Option<&WorkflowSessionId>,
    ) -> Result<String, ToolError> {
        unreachable!()
    }

    async fn cancel_workflow_session(
        &self,
        _workflow_task_id: &WorkflowSessionId,
        _reason: &str,
    ) -> Result<String, ToolError> {
        unreachable!()
    }

    async fn poll_terminal_workflow(
        &self,
        _workflow_id: &WorkflowId,
        _workflow_task_id: &WorkflowSessionId,
    ) -> Result<Option<TerminalWorkflow>, ToolError> {
        unreachable!()
    }

    async fn find_outstanding_workflows(
        &self,
        _parent_task_id: &TaskId,
        _agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, ToolError> {
        Ok(vec![OutstandingWorkflow {
            workflow_id: WorkflowId::new_v4(),
            workflow_task_id: WorkflowSessionId::new_v4(),
            workflow_goal: "prior goal".to_owned(),
        }])
    }

    async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, ToolError> {
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

impl Sealed for StartingControl {}

#[async_trait]
impl WorkflowServicePort for StartingControl {
    async fn start_workflow(
        &self,
        _request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, ToolError> {
        Ok(StartedWorkflow {
            workflow_id: WorkflowId::new_v4(),
            workflow_task_id: "wf_1".parse()?,
        })
    }

    async fn check_workflow_status(
        &self,
        _workflow_id: &WorkflowId,
        _workflow_task_id: Option<&WorkflowSessionId>,
    ) -> Result<String, ToolError> {
        unreachable!()
    }

    async fn cancel_workflow_session(
        &self,
        _workflow_task_id: &WorkflowSessionId,
        _reason: &str,
    ) -> Result<String, ToolError> {
        unreachable!()
    }

    async fn poll_terminal_workflow(
        &self,
        _workflow_id: &WorkflowId,
        _workflow_task_id: &WorkflowSessionId,
    ) -> Result<Option<TerminalWorkflow>, ToolError> {
        unreachable!()
    }

    async fn find_outstanding_workflows(
        &self,
        _parent_task_id: &TaskId,
        _agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, ToolError> {
        Ok(Vec::new())
    }

    async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, ToolError> {
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
        ["wf_1"],
        "delegate_workflow must register the workflow as background work"
    );
}

#[tokio::test]
async fn workflow_tools_reject_empty_ids() {
    let ctx = metadata();

    for input in [
        obj(&[("workflow_id", json!(""))]),
        obj(&[
            ("workflow_id", json!("workflow-1")),
            ("workflow_task_id", json!("")),
        ]),
    ] {
        let res = CheckWorkflowStatus::new(None)
            .execute(&input, &ctx)
            .await
            .expect("ok");
        assert!(res.is_error);
        assert!(res.output.contains("workflow"), "{}", res.output);
    }

    let cancel = CancelWorkflow::new(None)
        .execute(&obj(&[("workflow_task_id", json!(""))]), &ctx)
        .await
        .expect("ok");
    assert!(cancel.is_error);
    assert!(cancel.output.contains("workflow_task_id"));
}
