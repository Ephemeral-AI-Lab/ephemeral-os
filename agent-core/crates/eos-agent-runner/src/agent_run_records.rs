//! Private adapter to `eos-agent-message-records`.

use eos_agent_ports::{AgentRunMessageRecordKind, WorkflowTaskRole};

/// Convert the public runner/port record kind into the private message-records
/// crate type.
#[must_use]
pub fn to_message_record_kind(
    kind: &AgentRunMessageRecordKind,
) -> eos_agent_message_records::AgentRunRecordKind {
    match kind {
        AgentRunMessageRecordKind::Root => eos_agent_message_records::AgentRunRecordKind::Root,
        AgentRunMessageRecordKind::WorkflowTask {
            workflow_id,
            iteration_id,
            attempt_id,
            role,
        } => eos_agent_message_records::AgentRunRecordKind::WorkflowTask {
            workflow_id: workflow_id.clone(),
            iteration_id: iteration_id.clone(),
            attempt_id: attempt_id.clone(),
            role: to_message_record_workflow_role(*role),
        },
        AgentRunMessageRecordKind::Subagent {
            parent_agent_run_id,
        } => eos_agent_message_records::AgentRunRecordKind::Subagent {
            parent_agent_run_id: parent_agent_run_id.clone(),
        },
        AgentRunMessageRecordKind::Advisor {
            parent_agent_run_id,
        } => eos_agent_message_records::AgentRunRecordKind::Advisor {
            parent_agent_run_id: parent_agent_run_id.clone(),
        },
        AgentRunMessageRecordKind::Agent => eos_agent_message_records::AgentRunRecordKind::Agent,
        _ => eos_agent_message_records::AgentRunRecordKind::Agent,
    }
}

fn to_message_record_workflow_role(
    role: WorkflowTaskRole,
) -> eos_agent_message_records::WorkflowTaskRole {
    match role {
        WorkflowTaskRole::Planner => eos_agent_message_records::WorkflowTaskRole::Planner,
        WorkflowTaskRole::Generator => eos_agent_message_records::WorkflowTaskRole::Generator,
        WorkflowTaskRole::Reducer => eos_agent_message_records::WorkflowTaskRole::Reducer,
        _ => eos_agent_message_records::WorkflowTaskRole::Generator,
    }
}
