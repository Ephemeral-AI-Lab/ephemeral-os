use eos_types::Attempt;

use crate::attempt::AttemptResources;
use crate::{Result, WorkflowError};

use super::{AgentContext, ContextSection};

pub(crate) async fn render_planner_agent_context(
    deps: &AttemptResources,
    attempt: &Attempt,
) -> Result<AgentContext> {
    let iteration = deps
        .iteration_store
        .get(&attempt.iteration_id)
        .await?
        .ok_or_else(|| WorkflowError::not_found("iteration", attempt.iteration_id.as_str()))?;
    let workflow = deps
        .workflow_store
        .get(&attempt.workflow_id)
        .await?
        .ok_or_else(|| WorkflowError::not_found("workflow", attempt.workflow_id.as_str()))?;

    let prior_attempts = deps
        .attempt_store
        .list_for_iteration(&iteration.id)
        .await?
        .into_iter()
        .filter(|candidate| candidate.id != attempt.id)
        .map(|candidate| {
            ContextSection::new("attempt").with_attrs(vec![
                (
                    "sequence_no".to_owned(),
                    candidate.attempt_sequence_no.to_string(),
                ),
                ("status".to_owned(), candidate.status().as_str().to_owned()),
            ])
        })
        .collect::<Vec<_>>();

    Ok(AgentContext {
        sections: vec![
            ContextSection::new("workflow")
                .with_children(vec![
                    ContextSection::new("workflow_goal").with_text(workflow.workflow_goal)
                ]),
            ContextSection::new("current_iteration").with_children(vec![
                ContextSection::new("workflow_goal").with_text(iteration.workflow_goal),
                ContextSection::new("iteration_goal").with_text(iteration.iteration_goal),
            ]),
            ContextSection::new("prior_attempts").with_children(prior_attempts),
        ],
        guidance_contents: vec![
            "- <workflow>: workflow goal and current planning frame".to_owned(),
            "- <current_iteration>: current iteration goal".to_owned(),
            "- <prior_attempts>: earlier attempt status for this iteration".to_owned(),
        ],
        directive: "Author a worker plan and finish with submit_plan_outcome.".to_owned(),
        context_limits: vec![
            "Do not execute work items yourself; create worker work_items.".to_owned(),
            "Order work_items so dependency references can use their list positions.".to_owned(),
        ],
    })
}
