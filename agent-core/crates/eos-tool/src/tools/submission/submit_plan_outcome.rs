//! The `submit_plan_outcome` terminal tool.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{
    AgentName, DeferredGoal, JsonObject, PlanOutcomeSubmission, WorkItemId, WorkItemSpec,
};
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::registry::{text_spec, ToolConfigSet};
use crate::tools::{parse_input, AttemptSubmissionHandle};
use crate::{
    ExecutionMetadata, OutputShape, ToolError, ToolExecutor, ToolName, ToolRegistry, ToolResult,
};

use super::support::{is_blank, meta_obj, submission_ack_result};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct SubmitPlanOutcomeInput {
    plan_spec: String,
    work_items: Vec<WorkItemSpecInput>,
    #[serde(default)]
    deferred_goal_for_next_iteration: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct WorkItemSpecInput {
    agent_name: String,
    work_spec: String,
    #[serde(default)]
    needs: Vec<usize>,
}

struct SubmitPlanOutcome {
    service: AttemptSubmissionHandle,
}

impl SubmitPlanOutcome {
    fn new(service: AttemptSubmissionHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for SubmitPlanOutcome {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: SubmitPlanOutcomeInput = match parse_input(ToolName::SubmitPlanOutcome, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if let Err(message) = validate_plan_input(&parsed) {
            return Ok(ToolResult::error(message));
        }
        if let Err(message) = validate_plan_structure(&parsed) {
            return Ok(ToolResult::error(message));
        }

        let agent_run_id = ctx.require_agent_run_id()?.clone();
        let submission = match plan_submission(parsed, agent_run_id.clone()) {
            Ok(submission) => submission,
            Err(err) => return Ok(ToolResult::error(err.to_string())),
        };
        let has_deferred = submission.deferred_goal_for_next_iteration.is_some();
        let plan_spec = submission.plan_spec.clone();
        let work_items = submission.work_items.clone();
        let deferred_goal = submission.deferred_goal_for_next_iteration.clone();
        let ack = self.service.api.submit_plan_outcome(submission).await?;
        Ok(submission_ack_result(
            ack,
            "Accepted plan outcome.",
            &meta_obj(&[
                ("kind", json!("planner")),
                ("plan_spec", json!(plan_spec)),
                ("work_items", json!(work_items)),
                ("deferred_goal_for_next_iteration", json!(deferred_goal)),
                ("agent_run_id", json!(agent_run_id.as_str())),
                ("has_deferred_goal_for_next_iteration", json!(has_deferred)),
            ]),
        ))
    }
}

fn plan_submission(
    parsed: SubmitPlanOutcomeInput,
    agent_run_id: eos_types::AgentRunId,
) -> Result<PlanOutcomeSubmission, eos_types::CoreError> {
    Ok(PlanOutcomeSubmission {
        agent_run_id,
        plan_spec: parsed.plan_spec,
        work_items: parsed
            .work_items
            .into_iter()
            .enumerate()
            .map(|item| {
                let (index, item) = item;
                Ok(WorkItemSpec {
                    id: generated_work_item_id(index + 1)?,
                    agent_name: AgentName::new(item.agent_name)
                        .map_err(|err| eos_types::CoreError::Store(err.to_string()))?,
                    work_spec: item.work_spec,
                    needs: item
                        .needs
                        .into_iter()
                        .map(generated_work_item_id)
                        .collect::<Result<Vec<_>, _>>()?,
                })
            })
            .collect::<Result<Vec<_>, eos_types::CoreError>>()?,
        deferred_goal_for_next_iteration: parsed
            .deferred_goal_for_next_iteration
            .map(DeferredGoal::new)
            .transpose()?,
    })
}

fn validate_plan_input(input: &SubmitPlanOutcomeInput) -> Result<(), String> {
    if is_blank(&input.plan_spec) {
        return Err("plan_spec must be nonblank".to_owned());
    }
    if input.work_items.is_empty() {
        return Err("work_items must not be empty".to_owned());
    }
    let work_item_count = input.work_items.len();
    for (index, item) in input.work_items.iter().enumerate() {
        if is_blank(&item.agent_name) {
            return Err("agent_name must be nonblank".to_owned());
        }
        if is_blank(&item.work_spec) {
            return Err("work_spec must be nonblank".to_owned());
        }
        for need in &item.needs {
            if *need == 0 || *need > work_item_count {
                return Err(format!(
                    "work item {} needs invalid work item number {}.",
                    index + 1,
                    need
                ));
            }
        }
    }
    if let Some(deferred) = &input.deferred_goal_for_next_iteration {
        if is_blank(deferred) {
            return Err("deferred_goal_for_next_iteration must be nonblank".to_owned());
        }
    }
    Ok(())
}

fn validate_plan_structure(input: &SubmitPlanOutcomeInput) -> Result<(), String> {
    assert_acyclic(input)
}

fn assert_acyclic(input: &SubmitPlanOutcomeInput) -> Result<(), String> {
    let graph: BTreeMap<usize, Vec<usize>> = input
        .work_items
        .iter()
        .enumerate()
        .map(|(index, item)| (index + 1, item.needs.clone()))
        .collect();
    let mut visiting = BTreeSet::new();
    let mut visited = BTreeSet::new();
    for id in graph.keys().copied() {
        visit(id, &graph, &mut visiting, &mut visited)?;
    }
    Ok(())
}

fn visit(
    id: usize,
    graph: &BTreeMap<usize, Vec<usize>>,
    visiting: &mut BTreeSet<usize>,
    visited: &mut BTreeSet<usize>,
) -> Result<(), String> {
    if visited.contains(id) {
        return Ok(());
    }
    if !visiting.insert(id) {
        return Err(format!(
            "Plan contains a dependency cycle involving work item {id}."
        ));
    }
    for need in graph.get(&id).into_iter().flatten().copied() {
        visit(need, graph, visiting, visited)?;
    }
    visiting.remove(&id);
    visited.insert(id);
    Ok(())
}

fn generated_work_item_id(position: usize) -> Result<WorkItemId, eos_types::CoreError> {
    WorkItemId::new(format!("work-{position}"))
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    attempt_submission: AttemptSubmissionHandle,
) {
    let plan = config.get(ToolName::SubmitPlanOutcome);
    crate::tools::register_tool(
        registry,
        ToolName::SubmitPlanOutcome,
        plan,
        text_spec(
            ToolName::SubmitPlanOutcome,
            &plan.description,
            schema_for!(SubmitPlanOutcomeInput),
        ),
        OutputShape::Text,
        Arc::new(SubmitPlanOutcome::new(attempt_submission)),
    );
}

pub(super) fn register_schema(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let plan = config.get(ToolName::SubmitPlanOutcome);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitPlanOutcome,
        plan,
        text_spec(
            ToolName::SubmitPlanOutcome,
            &plan.description,
            schema_for!(SubmitPlanOutcomeInput),
        ),
        OutputShape::Text,
    );
}
