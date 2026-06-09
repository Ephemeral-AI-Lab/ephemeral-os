use eos_types::{Attempt, SubmissionOutcome, WorkItemSpec};

use crate::attempt::{planner_outcome_for_attempt, AttemptResources};
use crate::{Result, WorkflowError};

use super::{AgentContext, ContextSection};

pub(crate) async fn render_worker_agent_context(
    deps: &AttemptResources,
    attempt: &Attempt,
    work_item: &WorkItemSpec,
) -> Result<AgentContext> {
    let planner = planner_outcome_for_attempt(deps, attempt).await?;
    let needs = dependency_sections(deps, attempt, work_item).await?;

    Ok(AgentContext {
        sections: vec![
            ContextSection::new("plan_spec").with_text(planner.plan_spec),
            ContextSection::new("work_item")
                .with_attrs(vec![(
                    "agent_name".to_owned(),
                    work_item.agent_name.as_str().to_owned(),
                )])
                .with_children(vec![
                    ContextSection::new("work_spec").with_text(work_item.work_spec.clone())
                ]),
            ContextSection::new("needs").with_children(needs),
        ],
        guidance_contents: vec![
            "- <plan_spec>: the plan explanation".to_owned(),
            "- <work_item>: your assigned work item only".to_owned(),
            "- <needs>: direct dependency outcomes only".to_owned(),
        ],
        directive: "Execute only this work item and finish with submit_worker_outcome.".to_owned(),
        context_limits: vec![
            "Use dependency outcomes as input context only.".to_owned(),
            "Do not report on work items outside this assignment.".to_owned(),
        ],
    })
}

async fn dependency_sections(
    deps: &AttemptResources,
    attempt: &Attempt,
    work_item: &WorkItemSpec,
) -> Result<Vec<ContextSection>> {
    let planner = planner_outcome_for_attempt(deps, attempt).await?;
    let mut sections = Vec::with_capacity(work_item.needs.len());
    for need in &work_item.needs {
        let node = attempt
            .execution_tree
            .node(need)
            .ok_or_else(|| WorkflowError::not_found("work item", need.as_str()))?;
        let agent_run_id = node.agent_run_id.as_ref().ok_or_else(|| {
            WorkflowError::invariant(format!(
                "dependency work item {:?} has no bound agent run",
                need.as_str()
            ))
        })?;
        let SubmissionOutcome::Worker { is_pass, outcome } =
            node.outcome.clone().ok_or_else(|| {
                WorkflowError::invariant(format!(
                    "dependency worker agent run {:?} has no worker outcome",
                    agent_run_id.as_str()
                ))
            })?
        else {
            return Err(WorkflowError::invariant(format!(
                "dependency agent run {:?} did not record a worker outcome",
                agent_run_id.as_str()
            )));
        };
        let work_spec = planner
            .work_items
            .iter()
            .find(|candidate| &candidate.id == need)
            .map(|item| item.work_spec.clone())
            .unwrap_or_default();
        sections.push(
            ContextSection::new("need")
                .with_attrs(vec![("is_pass".to_owned(), is_pass.to_string())])
                .with_children(vec![
                    ContextSection::new("work_spec").with_text(work_spec),
                    ContextSection::new("outcome").with_text(outcome),
                ]),
        );
    }
    Ok(sections)
}
