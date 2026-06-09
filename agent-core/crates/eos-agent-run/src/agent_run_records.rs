//! Private adapter to engine-owned message records.

use eos_types::{TaskAgentRunKind, WorkflowTaskRole};

/// Convert the public runner/port record kind into the engine message-record
/// type.
#[must_use]
pub fn to_message_record_kind(kind: &TaskAgentRunKind) -> eos_engine::records::AgentRunRecordKind {
    match kind {
        TaskAgentRunKind::Root => eos_engine::records::AgentRunRecordKind::Root,
        TaskAgentRunKind::WorkflowTask {
            workflow_id,
            iteration_id,
            attempt_id,
            role,
        } => eos_engine::records::AgentRunRecordKind::WorkflowTask {
            workflow_id: workflow_id.clone(),
            iteration_id: iteration_id.clone(),
            attempt_id: attempt_id.clone(),
            role: to_message_record_workflow_role(*role),
        },
        TaskAgentRunKind::Subagent {
            parent_agent_run_id,
        } => eos_engine::records::AgentRunRecordKind::Subagent {
            parent_agent_run_id: parent_agent_run_id.clone(),
        },
        TaskAgentRunKind::Advisor {
            parent_agent_run_id,
        } => eos_engine::records::AgentRunRecordKind::Advisor {
            parent_agent_run_id: parent_agent_run_id.clone(),
        },
        TaskAgentRunKind::Agent => eos_engine::records::AgentRunRecordKind::Agent,
        _ => eos_engine::records::AgentRunRecordKind::Agent,
    }
}

fn to_message_record_workflow_role(
    role: WorkflowTaskRole,
) -> eos_engine::records::WorkflowTaskRole {
    match role {
        WorkflowTaskRole::Planner => eos_engine::records::WorkflowTaskRole::Planner,
        WorkflowTaskRole::Generator => eos_engine::records::WorkflowTaskRole::Generator,
        WorkflowTaskRole::Reducer => eos_engine::records::WorkflowTaskRole::Reducer,
        _ => eos_engine::records::WorkflowTaskRole::Generator,
    }
}
