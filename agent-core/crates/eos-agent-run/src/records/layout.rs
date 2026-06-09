use std::path::{Path, PathBuf};

use eos_types::{
    format_record_dir, AgentRunId, AgentRunRecordIndex, ParentedAgentRunKind, TaskAgentRunKind,
    WorkflowCoordinates, WorkflowTaskRole as SharedWorkflowTaskRole,
};

use super::error::{MessageRecordError, Result};
use super::kind::{AgentRunRecordKind, AgentRunRecordStart};

pub(crate) async fn resolve_agent_run(root: &Path, agent_run_id: &AgentRunId) -> Result<PathBuf> {
    safe_segment("agent_run_id", agent_run_id.as_str())?;
    let root = root.to_path_buf();
    let id = agent_run_id.clone();
    let found = tokio::task::spawn_blocking(move || find_agent_run_dir_in(&root, &id)).await??;
    found.ok_or_else(|| MessageRecordError::NotFound(agent_run_id.as_str().to_owned()))
}

pub(crate) fn node_dir(root: &Path, input: &AgentRunRecordStart<'_>) -> Result<PathBuf> {
    validate_start_segments(input)?;
    let task_id = input
        .task_id
        .cloned()
        .ok_or_else(|| MessageRecordError::unsafe_segment("task_id", ""))?;
    let kind = match input.kind {
        AgentRunRecordKind::Root => TaskAgentRunKind::Root,
        AgentRunRecordKind::WorkflowTask {
            workflow_id,
            iteration_id,
            attempt_id,
            role,
        } => TaskAgentRunKind::Workflow {
            workflow: WorkflowCoordinates {
                workflow_id: workflow_id.clone(),
                iteration_id: iteration_id.clone(),
                attempt_id: attempt_id.clone(),
            },
            role: shared_workflow_role(*role),
        },
        AgentRunRecordKind::Subagent {
            parent_agent_run_id,
        } => TaskAgentRunKind::Parented {
            parent_agent_run_id: parent_agent_run_id.clone(),
            kind: ParentedAgentRunKind::Subagent,
        },
        AgentRunRecordKind::Advisor {
            parent_agent_run_id,
        } => TaskAgentRunKind::Parented {
            parent_agent_run_id: parent_agent_run_id.clone(),
            kind: ParentedAgentRunKind::Advisor,
        },
    };
    let record_dir = format_record_dir(&AgentRunRecordIndex {
        request_id: input.request_id.clone(),
        agent_run_id: input.agent_run_id.clone(),
        task_id,
        kind,
    });
    let mut node = root.to_path_buf();
    for segment in record_dir.as_str().split('/') {
        node.push(safe_segment("record_dir", segment)?);
    }
    Ok(node)
}

fn validate_start_segments(input: &AgentRunRecordStart<'_>) -> Result<()> {
    safe_segment("request_id", input.request_id.as_str())?;
    safe_segment("agent-run", input.agent_run_id.as_str())?;
    if let Some(task_id) = input.task_id {
        safe_segment("task_id", task_id.as_str())?;
    }
    match input.kind {
        AgentRunRecordKind::WorkflowTask {
            workflow_id,
            iteration_id,
            attempt_id,
            ..
        } => {
            safe_segment("workflow", workflow_id.as_str())?;
            safe_segment("iteration", iteration_id.as_str())?;
            safe_segment("attempt", attempt_id.as_str())?;
        }
        AgentRunRecordKind::Subagent {
            parent_agent_run_id,
        }
        | AgentRunRecordKind::Advisor {
            parent_agent_run_id,
        } => {
            safe_segment("agent_run_id", parent_agent_run_id.as_str())?;
        }
        AgentRunRecordKind::Root => {}
    }
    Ok(())
}

fn find_agent_run_dir_in(root: &Path, agent_run_id: &AgentRunId) -> Result<Option<PathBuf>> {
    safe_segment("agent_run_id", agent_run_id.as_str())?;
    let needles = [
        safe_prefixed_segment("agent-run", agent_run_id.as_str())?,
        safe_prefixed_segment("subagent-run", agent_run_id.as_str())?,
        safe_prefixed_segment("advisor-run", agent_run_id.as_str())?,
    ];
    find_dir_named(root, &needles)
}

fn find_dir_named(root: &Path, needles: &[String]) -> Result<Option<PathBuf>> {
    let Ok(entries) = std::fs::read_dir(root) else {
        return Ok(None);
    };
    for entry in entries {
        let entry = entry?;
        let file_type = entry.file_type()?;
        if !file_type.is_dir() {
            continue;
        }
        let name = entry.file_name();
        if name
            .to_str()
            .is_some_and(|value| needles.iter().any(|needle| needle == value))
        {
            return Ok(Some(entry.path()));
        }
        if let Some(found) = find_dir_named(&entry.path(), needles)? {
            return Ok(Some(found));
        }
    }
    Ok(None)
}

fn safe_prefixed_segment(prefix: &'static str, id: &str) -> Result<String> {
    Ok(format!("{prefix}-{}", safe_segment(prefix, id)?))
}

fn shared_workflow_role(role: super::kind::WorkflowTaskRole) -> SharedWorkflowTaskRole {
    match role {
        super::kind::WorkflowTaskRole::Planner => SharedWorkflowTaskRole::Planner,
        super::kind::WorkflowTaskRole::Generator => SharedWorkflowTaskRole::Generator,
        super::kind::WorkflowTaskRole::Reducer => SharedWorkflowTaskRole::Reducer,
    }
}

fn safe_segment<'a>(field: &'static str, value: &'a str) -> Result<&'a str> {
    if value.is_empty()
        || value == "."
        || value == ".."
        || value.contains('/')
        || value.contains('\\')
        || value.contains(std::path::MAIN_SEPARATOR)
    {
        return Err(MessageRecordError::unsafe_segment(field, value));
    }
    Ok(value)
}
