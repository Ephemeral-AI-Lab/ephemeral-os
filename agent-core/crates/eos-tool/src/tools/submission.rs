//! Submission terminal tools.

use eos_types::JsonObject;
use eos_types::TaskOutcomeStatus;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ToolResult;
use eos_types::SubmissionAck;

/// `Literal["success", "failed"]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub(in crate::tools::submission) enum SubmissionStatus {
    Success,
    Failed,
}

impl SubmissionStatus {
    pub(in crate::tools::submission) fn as_str(self) -> &'static str {
        match self {
            SubmissionStatus::Success => "success",
            SubmissionStatus::Failed => "failed",
        }
    }

    pub(in crate::tools::submission) fn outcome_status(self) -> TaskOutcomeStatus {
        match self {
            SubmissionStatus::Success => TaskOutcomeStatus::Success,
            SubmissionStatus::Failed => TaskOutcomeStatus::Failed,
        }
    }
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(in crate::tools::submission) struct OutcomeInput {
    pub(in crate::tools::submission) status: SubmissionStatus,
    pub(in crate::tools::submission) outcome: String,
}

pub(in crate::tools::submission) fn is_blank(s: &str) -> bool {
    s.trim().is_empty()
}

pub(in crate::tools::submission) fn meta_obj(pairs: &[(&str, Value)]) -> JsonObject {
    pairs
        .iter()
        .map(|(k, v)| ((*k).to_owned(), v.clone()))
        .collect()
}

pub(in crate::tools::submission) fn submission_ack_result(
    ack: SubmissionAck,
    success: &str,
    metadata: &JsonObject,
) -> ToolResult {
    match ack {
        SubmissionAck::Accepted => ToolResult::ok(success).with_metadata(metadata.clone()),
        SubmissionAck::Rejected(message) => ToolResult::error(message),
    }
}

mod planner {
    use std::collections::{BTreeMap, BTreeSet};
    use std::sync::Arc;

    use async_trait::async_trait;
    use eos_types::JsonObject;
    use eos_types::{DeferredGoal, PlanDisposition, PlanNodeId};
    use schemars::{schema_for, JsonSchema};
    use serde::{Deserialize, Serialize};
    use serde_json::json;

    use crate::registry::text_spec;
    use crate::registry::ToolConfigSet;
    use crate::tools::parse_input;
    use crate::tools::AttemptSubmissionHandle;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolRegistry;
    use crate::{OutputShape, ToolResult};
    use eos_types::{PlanReducer, PlanTask, PlannerPlan};

    use super::{is_blank, meta_obj, submission_ack_result};

    #[derive(Debug, Deserialize, Serialize, JsonSchema)]
    struct PlanTaskInput {
        id: String,
        agent_name: String,
        #[serde(default)]
        needs: Vec<String>,
    }

    #[derive(Debug, Deserialize, Serialize, JsonSchema)]
    struct ReducerInput {
        id: String,
        #[serde(default)]
        needs: Vec<String>,
        prompt: String,
    }

    #[derive(Debug, Deserialize, Serialize, JsonSchema)]
    pub(super) struct SubmitPlannerOutcomeInput {
        tasks: Vec<PlanTaskInput>,
        task_specs: BTreeMap<String, String>,
        reducers: Vec<ReducerInput>,
        #[serde(default)]
        deferred_goal_for_next_iteration: Option<String>,
    }

    struct SubmitPlannerOutcome {
        service: AttemptSubmissionHandle,
    }

    impl SubmitPlannerOutcome {
        fn new(service: AttemptSubmissionHandle) -> Self {
            Self { service }
        }
    }

    #[async_trait]
    impl ToolExecutor for SubmitPlannerOutcome {
        async fn execute(
            &self,
            input: &JsonObject,
            ctx: &ExecutionMetadata,
        ) -> Result<ToolResult, ToolError> {
            let parsed: SubmitPlannerOutcomeInput =
                match parse_input(ToolName::SubmitPlannerOutcome, input) {
                    Ok(v) => v,
                    Err(err) => return Ok(err),
                };
            if let Err(message) = validate_planner_input(&parsed) {
                return Ok(ToolResult::error(message));
            }
            if let Err(message) = validate_planner_structure(&parsed) {
                return Ok(ToolResult::error(message));
            }

            let attempt_id = ctx.require_attempt_id()?.clone();
            let planner_task_id = ctx.require_task_id()?.clone();
            let plan = match planner_plan(parsed, attempt_id, planner_task_id.clone()) {
                Ok(plan) => plan,
                Err(err) => return Ok(ToolResult::error(err.to_string())),
            };
            let submission_kind = plan.disposition.submission_kind_label();

            let ack = self.service.port.apply_plan(plan).await?;
            Ok(submission_ack_result(
                ack,
                "Accepted planner submission.",
                &meta_obj(&[
                    ("submission_kind", json!(submission_kind)),
                    ("task_id", json!(planner_task_id.as_str())),
                    (
                        "attempt_id",
                        json!(ctx.attempt_id.as_ref().map(eos_types::AttemptId::as_str)),
                    ),
                ]),
            ))
        }
    }

    fn planner_plan(
        parsed: SubmitPlannerOutcomeInput,
        attempt_id: eos_types::AttemptId,
        planner_task_id: eos_types::TaskId,
    ) -> Result<PlannerPlan, eos_types::CoreError> {
        let disposition = PlanDisposition::from_deferred_goal(
            parsed
                .deferred_goal_for_next_iteration
                .map(DeferredGoal::new)
                .transpose()?,
        );
        Ok(PlannerPlan {
            attempt_id,
            planner_task_id,
            disposition,
            tasks: parsed
                .tasks
                .into_iter()
                .map(|task| {
                    Ok(PlanTask {
                        id: PlanNodeId::new(task.id)?,
                        agent_name: task.agent_name,
                        needs: task
                            .needs
                            .into_iter()
                            .map(PlanNodeId::new)
                            .collect::<Result<Vec<_>, _>>()?,
                    })
                })
                .collect::<Result<Vec<_>, eos_types::CoreError>>()?,
            task_specs: parsed
                .task_specs
                .into_iter()
                .map(|(key, value)| PlanNodeId::new(key).map(|key| (key, value)))
                .collect::<Result<BTreeMap<_, _>, _>>()?,
            reducers: parsed
                .reducers
                .into_iter()
                .map(|reducer| {
                    Ok(PlanReducer {
                        id: PlanNodeId::new(reducer.id)?,
                        needs: reducer
                            .needs
                            .into_iter()
                            .map(PlanNodeId::new)
                            .collect::<Result<Vec<_>, _>>()?,
                        prompt: reducer.prompt,
                    })
                })
                .collect::<Result<Vec<_>, eos_types::CoreError>>()?,
        })
    }

    fn validate_planner_input(input: &SubmitPlannerOutcomeInput) -> Result<(), String> {
        if input.tasks.is_empty() {
            return Err("tasks must not be empty".to_owned());
        }
        if input.task_specs.is_empty() {
            return Err("task_specs must not be empty".to_owned());
        }
        if input.reducers.is_empty() {
            return Err("reducers must not be empty".to_owned());
        }
        for task in &input.tasks {
            if is_blank(&task.id) {
                return Err("id must be nonblank".to_owned());
            }
            if is_blank(&task.agent_name) {
                return Err("agent_name must be nonblank".to_owned());
            }
            if task.needs.iter().any(|need| is_blank(need)) {
                return Err("needs must be nonblank".to_owned());
            }
        }
        for (key, spec) in &input.task_specs {
            if is_blank(key) {
                return Err("task_specs key must be nonblank".to_owned());
            }
            if is_blank(spec) {
                return Err(format!("task spec for '{key}' must be nonblank"));
            }
        }
        for reducer in &input.reducers {
            if is_blank(&reducer.id) {
                return Err("id must be nonblank".to_owned());
            }
            if reducer.needs.iter().any(|need| is_blank(need)) {
                return Err("needs must be nonblank".to_owned());
            }
            if is_blank(&reducer.prompt) {
                return Err("prompt must be nonblank".to_owned());
            }
        }
        if let Some(deferred) = &input.deferred_goal_for_next_iteration {
            if is_blank(deferred) {
                return Err("deferred_goal_for_next_iteration must be nonblank".to_owned());
            }
        }
        Ok(())
    }

    fn validate_planner_structure(input: &SubmitPlannerOutcomeInput) -> Result<(), String> {
        let mut seen = BTreeSet::new();
        for task in &input.tasks {
            if !seen.insert(task.id.as_str()) {
                return Err(format!("Plan contains duplicate task id '{}'.", task.id));
            }
        }
        let task_ids: BTreeSet<&str> = input.tasks.iter().map(|task| task.id.as_str()).collect();
        let spec_ids: BTreeSet<&str> = input.task_specs.keys().map(String::as_str).collect();

        let missing: Vec<&str> = task_ids.difference(&spec_ids).copied().collect();
        if !missing.is_empty() {
            return Err(format!("Missing task_specs for {}.", missing.join(", ")));
        }
        let extra: Vec<&str> = spec_ids.difference(&task_ids).copied().collect();
        if !extra.is_empty() {
            return Err(format!(
                "task_specs contains unknown ids {}.",
                extra.join(", ")
            ));
        }
        Ok(())
    }

    pub(super) fn register(
        registry: &mut ToolRegistry,
        config: &ToolConfigSet,
        attempt_submission: AttemptSubmissionHandle,
    ) {
        let planner = config.get(ToolName::SubmitPlannerOutcome);
        crate::tools::register_tool(
            registry,
            ToolName::SubmitPlannerOutcome,
            planner,
            text_spec(
                ToolName::SubmitPlannerOutcome,
                &planner.description,
                schema_for!(SubmitPlannerOutcomeInput),
            ),
            OutputShape::Text,
            Arc::new(SubmitPlannerOutcome::new(attempt_submission)),
        );
    }
}
mod root {
    //! The `submit_root_outcome` terminal tool.

    use std::sync::Arc;

    use async_trait::async_trait;
    use eos_types::JsonObject;
    use eos_types::{RequestStatus, TaskRole, TaskStatus};
    use schemars::{schema_for, JsonSchema};
    use serde::{Deserialize, Serialize};
    use serde_json::json;

    use crate::registry::text_spec;
    use crate::registry::ToolConfigSet;
    use crate::tools::parse_input;
    use crate::tools::RootSubmissionHandle;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolRegistry;
    use crate::{OutputShape, ToolResult};

    use super::{is_blank, meta_obj, SubmissionStatus};

    #[derive(Debug, Deserialize, Serialize, JsonSchema)]
    pub(super) struct SubmitRootOutcomeInput {
        status: SubmissionStatus,
        outcome: String,
    }

    struct SubmitRootOutcome {
        service: RootSubmissionHandle,
    }

    impl SubmitRootOutcome {
        fn new(service: RootSubmissionHandle) -> Self {
            Self { service }
        }
    }

    #[async_trait]
    impl ToolExecutor for SubmitRootOutcome {
        async fn execute(
            &self,
            input: &JsonObject,
            ctx: &ExecutionMetadata,
        ) -> Result<ToolResult, ToolError> {
            let parsed: SubmitRootOutcomeInput =
                match parse_input(ToolName::SubmitRootOutcome, input) {
                    Ok(v) => v,
                    Err(err) => return Ok(err),
                };
            if is_blank(&parsed.outcome) {
                return Ok(ToolResult::error("outcome must be nonblank"));
            }

            let request_id = ctx.require_request_id()?;
            let task_id = ctx.require_task_id()?;
            let service = &self.service;
            let task_store = service.submission.task_store()?;
            let request_store = service.submission.request_store()?;

            let task = match task_store.get(task_id).await? {
                Some(task) => task,
                None => {
                    return Ok(ToolResult::error(format!(
                        "Root task '{}' was not found.",
                        task_id.as_str()
                    )));
                }
            };
            if task.request_id != *request_id {
                return Ok(ToolResult::error(
                    "Root task does not belong to this request.",
                ));
            }
            if task.workflow_id.is_some() {
                return Ok(ToolResult::error(
                    "submit_root_outcome is only valid for the root task.",
                ));
            }
            if task.role != TaskRole::Root {
                return Ok(ToolResult::error(format!(
                    "Task '{}' is not a root task.",
                    task_id.as_str()
                )));
            }
            if task.status != TaskStatus::Running {
                return Ok(ToolResult::error(format!(
                    "Root task '{}' is not running.",
                    task_id.as_str()
                )));
            }

            let task_status = match parsed.status {
                SubmissionStatus::Success => TaskStatus::Done,
                SubmissionStatus::Failed => TaskStatus::Failed,
            };
            let request_status = match parsed.status {
                SubmissionStatus::Success => RequestStatus::Done,
                SubmissionStatus::Failed => RequestStatus::Failed,
            };
            let terminal = meta_obj(&[
                ("status", json!(parsed.status.as_str())),
                ("outcome", json!(parsed.outcome)),
            ]);
            task_store
                .set_task_status_if_current(
                    task_id,
                    TaskStatus::Running,
                    task_status,
                    None,
                    Some(&terminal),
                )
                .await?
                .ok_or_else(|| {
                    ToolError::Internal(format!(
                        "root task '{}' was closed before terminal submission was recorded",
                        task_id.as_str()
                    ))
                })?;
            request_store
                .finish_request(request_id, request_status)
                .await?;

            let kind = if parsed.status == SubmissionStatus::Success {
                "root_success"
            } else {
                "root_failure"
            };
            Ok(
                ToolResult::ok(format!("Accepted root {}.", parsed.status.as_str())).with_metadata(
                    meta_obj(&[
                        ("submission_kind", json!(kind)),
                        ("request_id", json!(request_id.as_str())),
                        ("task_id", json!(task_id.as_str())),
                    ]),
                ),
            )
        }
    }

    pub(super) fn register(
        registry: &mut ToolRegistry,
        config: &ToolConfigSet,
        root_submission: RootSubmissionHandle,
    ) {
        let root = config.get(ToolName::SubmitRootOutcome);
        crate::tools::register_tool(
            registry,
            ToolName::SubmitRootOutcome,
            root,
            text_spec(
                ToolName::SubmitRootOutcome,
                &root.description,
                schema_for!(SubmitRootOutcomeInput),
            ),
            OutputShape::Text,
            Arc::new(SubmitRootOutcome::new(root_submission)),
        );
    }

    #[cfg(test)]
    mod tests {
        #![allow(clippy::unwrap_used)]

        use std::sync::Arc;

        use eos_types::{RequestId, Task};
        use serde_json::json;

        use super::*;
        use crate::support::{metadata, FakeRequestStore, FakeTaskStore};

        fn obj(pairs: &[(&str, serde_json::Value)]) -> JsonObject {
            pairs
                .iter()
                .map(|(k, v)| ((*k).to_owned(), v.clone()))
                .collect()
        }

        fn root_task(request_id: &RequestId) -> Task {
            Task {
                id: "root-1".parse().expect("id"),
                request_id: request_id.clone(),
                role: TaskRole::Root,
                instruction: "do the request".to_owned(),
                status: TaskStatus::Running,
                workflow_id: None,
                iteration_id: None,
                attempt_id: None,
                agent_name: Some("root".to_owned()),
                needs: Vec::new(),
                outcomes: Vec::new(),
                terminal_tool_result: None,
            }
        }

        fn root_metadata(request_id: RequestId) -> ExecutionMetadata {
            let mut ctx = metadata();
            ctx.request_id = Some(request_id);
            ctx.task_id = Some("root-1".parse().expect("id"));
            ctx
        }

        fn executor(
            task_store: Arc<FakeTaskStore>,
            request_store: Arc<FakeRequestStore>,
        ) -> SubmitRootOutcome {
            SubmitRootOutcome::new(RootSubmissionHandle::new(crate::Submission::new(
                task_store,
                request_store,
                Arc::new(NoopAttemptSubmission),
            )))
        }

        struct NoopAttemptSubmission;

        #[async_trait::async_trait]
        impl eos_types::AttemptSubmissionPort for NoopAttemptSubmission {
            async fn apply_plan(
                &self,
                _plan: eos_types::PlannerPlan,
            ) -> Result<eos_types::SubmissionAck, eos_types::CoreError> {
                Ok(eos_types::SubmissionAck::Rejected(
                    "not used by root tests".to_owned(),
                ))
            }

            async fn submit_generator(
                &self,
                _submission: eos_types::GeneratorSubmission,
            ) -> Result<eos_types::SubmissionAck, eos_types::CoreError> {
                Ok(eos_types::SubmissionAck::Rejected(
                    "not used by root tests".to_owned(),
                ))
            }

            async fn apply_reducer(
                &self,
                _submission: eos_types::ReducerSubmission,
            ) -> Result<eos_types::SubmissionAck, eos_types::CoreError> {
                Ok(eos_types::SubmissionAck::Rejected(
                    "not used by root tests".to_owned(),
                ))
            }
        }

        #[tokio::test]
        async fn main_role_terminals() {
            let request_id: RequestId = RequestId::new_v4();
            let task_store = Arc::new(FakeTaskStore::new());
            task_store.put(root_task(&request_id));
            let request_store = Arc::new(FakeRequestStore::new());
            let ctx = root_metadata(request_id.clone());
            let executor = executor(task_store.clone(), request_store.clone());

            let res = executor
                .execute(
                    &obj(&[("status", json!("success")), ("outcome", json!("all done"))]),
                    &ctx,
                )
                .await
                .expect("ok");
            assert!(!res.is_error, "{}", res.output);
            assert_eq!(res.metadata["submission_kind"], json!("root_success"));
            assert_eq!(
                request_store.finished(),
                vec![(request_id.as_str().to_owned(), RequestStatus::Done)]
            );

            let res = executor
                .execute(
                    &obj(&[("status", json!("success")), ("outcome", json!("   "))]),
                    &ctx,
                )
                .await
                .expect("ok");
            assert!(res.is_error);
            assert!(res.output.contains("outcome must be nonblank"));

            let other = root_metadata(RequestId::new_v4());
            let res = executor
                .execute(
                    &obj(&[("status", json!("failed")), ("outcome", json!("blocked"))]),
                    &other,
                )
                .await
                .expect("ok");
            assert!(res.is_error);
            assert!(
                res.output.contains("does not belong to this request"),
                "{}",
                res.output
            );
        }

        #[tokio::test]
        async fn root_rejects_non_root_task() {
            let request_id = RequestId::new_v4();
            let task_store = Arc::new(FakeTaskStore::new());
            let mut task = root_task(&request_id);
            task.role = TaskRole::Generator;
            task_store.put(task);
            let request_store = Arc::new(FakeRequestStore::new());
            let ctx = root_metadata(request_id);
            let res = executor(task_store, request_store)
                .execute(
                    &obj(&[("status", json!("success")), ("outcome", json!("x"))]),
                    &ctx,
                )
                .await
                .expect("ok");
            assert!(res.is_error);
            assert!(res.output.contains("is not a root task"), "{}", res.output);
        }
    }
}
mod generator {
    use std::sync::Arc;

    use async_trait::async_trait;
    use eos_types::GeneratorSubmission;
    use eos_types::JsonObject;
    use schemars::schema_for;
    use serde_json::json;

    use crate::registry::text_spec;
    use crate::registry::ToolConfigSet;
    use crate::tools::parse_input;
    use crate::tools::AttemptSubmissionHandle;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolRegistry;
    use crate::{OutputShape, ToolResult};

    use super::{is_blank, meta_obj, submission_ack_result, OutcomeInput, SubmissionStatus};

    struct SubmitGeneratorOutcome {
        service: AttemptSubmissionHandle,
    }

    impl SubmitGeneratorOutcome {
        fn new(service: AttemptSubmissionHandle) -> Self {
            Self { service }
        }
    }

    #[async_trait]
    impl ToolExecutor for SubmitGeneratorOutcome {
        async fn execute(
            &self,
            input: &JsonObject,
            ctx: &ExecutionMetadata,
        ) -> Result<ToolResult, ToolError> {
            let parsed: OutcomeInput = match parse_input(ToolName::SubmitGeneratorOutcome, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
            if is_blank(&parsed.outcome) {
                return Ok(ToolResult::error("outcome must be nonblank"));
            }
            let attempt_id = ctx.require_attempt_id()?.clone();
            let task_id = ctx.require_task_id()?.clone();
            let submission = GeneratorSubmission {
                attempt_id,
                task_id: task_id.clone(),
                status: parsed.status.outcome_status(),
                outcome: parsed.outcome.clone(),
                terminal_tool_result: meta_obj(&[("generator_role", json!("generator"))]),
            };
            let ack = self.service.port.submit_generator(submission).await?;
            Ok(submission_ack_result(
                ack,
                &format!("Accepted generator {}.", parsed.status.as_str()),
                &meta_obj(&[
                    (
                        "submission_kind",
                        json!(if parsed.status == SubmissionStatus::Success {
                            "generator_success"
                        } else {
                            "generator_failure"
                        }),
                    ),
                    ("task_id", json!(task_id.as_str())),
                    (
                        "attempt_id",
                        json!(ctx.attempt_id.as_ref().map(eos_types::AttemptId::as_str)),
                    ),
                ]),
            ))
        }
    }

    pub(super) fn register(
        registry: &mut ToolRegistry,
        config: &ToolConfigSet,
        attempt_submission: AttemptSubmissionHandle,
    ) {
        let generator = config.get(ToolName::SubmitGeneratorOutcome);
        crate::tools::register_tool(
            registry,
            ToolName::SubmitGeneratorOutcome,
            generator,
            text_spec(
                ToolName::SubmitGeneratorOutcome,
                &generator.description,
                schema_for!(OutcomeInput),
            ),
            OutputShape::Text,
            Arc::new(SubmitGeneratorOutcome::new(attempt_submission)),
        );
    }
}
mod reducer {
    use std::sync::Arc;

    use async_trait::async_trait;
    use eos_types::JsonObject;
    use eos_types::ReducerSubmission;
    use schemars::schema_for;
    use serde_json::json;

    use crate::registry::text_spec;
    use crate::registry::ToolConfigSet;
    use crate::tools::parse_input;
    use crate::tools::AttemptSubmissionHandle;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolRegistry;
    use crate::{OutputShape, ToolResult};

    use super::{is_blank, meta_obj, submission_ack_result, OutcomeInput, SubmissionStatus};

    struct SubmitReducerOutcome {
        service: AttemptSubmissionHandle,
    }

    impl SubmitReducerOutcome {
        fn new(service: AttemptSubmissionHandle) -> Self {
            Self { service }
        }
    }

    #[async_trait]
    impl ToolExecutor for SubmitReducerOutcome {
        async fn execute(
            &self,
            input: &JsonObject,
            ctx: &ExecutionMetadata,
        ) -> Result<ToolResult, ToolError> {
            let parsed: OutcomeInput = match parse_input(ToolName::SubmitReducerOutcome, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
            if is_blank(&parsed.outcome) {
                return Ok(ToolResult::error("outcome must be nonblank"));
            }
            let attempt_id = ctx.require_attempt_id()?.clone();
            let task_id = ctx.require_task_id()?.clone();
            let submission = ReducerSubmission {
                attempt_id,
                task_id: task_id.clone(),
                status: parsed.status.outcome_status(),
                outcome: parsed.outcome.clone(),
                terminal_tool_result: JsonObject::new(),
            };
            let ack = self.service.port.apply_reducer(submission).await?;
            Ok(submission_ack_result(
                ack,
                &format!("Accepted reducer {}.", parsed.status.as_str()),
                &meta_obj(&[
                    (
                        "submission_kind",
                        json!(if parsed.status == SubmissionStatus::Success {
                            "reducer_success"
                        } else {
                            "reducer_failure"
                        }),
                    ),
                    ("task_id", json!(task_id.as_str())),
                    (
                        "attempt_id",
                        json!(ctx.attempt_id.as_ref().map(eos_types::AttemptId::as_str)),
                    ),
                ]),
            ))
        }
    }

    pub(super) fn register(
        registry: &mut ToolRegistry,
        config: &ToolConfigSet,
        attempt_submission: AttemptSubmissionHandle,
    ) {
        let reducer = config.get(ToolName::SubmitReducerOutcome);
        crate::tools::register_tool(
            registry,
            ToolName::SubmitReducerOutcome,
            reducer,
            text_spec(
                ToolName::SubmitReducerOutcome,
                &reducer.description,
                schema_for!(OutcomeInput),
            ),
            OutputShape::Text,
            Arc::new(SubmitReducerOutcome::new(attempt_submission)),
        );
    }
}
mod advisor {
    //! The `submit_advisor_feedback` helper terminal.

    use std::sync::Arc;

    use async_trait::async_trait;
    use eos_types::JsonObject;
    use schemars::{schema_for, JsonSchema};
    use serde::{Deserialize, Serialize};
    use serde_json::json;

    use crate::registry::text_spec;
    use crate::registry::ToolConfigSet;
    use crate::tools::parse_input;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolRegistry;
    use crate::{OutputShape, ToolResult};

    use super::{is_blank, meta_obj};

    /// `Literal["approve", "reject"]`.
    #[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, JsonSchema)]
    #[serde(rename_all = "snake_case")]
    enum Verdict {
        Approve,
        Reject,
    }

    impl Verdict {
        fn as_str(self) -> &'static str {
            match self {
                Verdict::Approve => "approve",
                Verdict::Reject => "reject",
            }
        }
    }

    #[derive(Debug, Deserialize, Serialize, JsonSchema)]
    pub(super) struct SubmitAdvisorFeedbackInput {
        verdict: Verdict,
        summary: String,
    }

    struct SubmitAdvisorFeedback;

    #[async_trait]
    impl ToolExecutor for SubmitAdvisorFeedback {
        async fn execute(
            &self,
            input: &JsonObject,
            _ctx: &ExecutionMetadata,
        ) -> Result<ToolResult, ToolError> {
            let parsed: SubmitAdvisorFeedbackInput =
                match parse_input(ToolName::SubmitAdvisorFeedback, input) {
                    Ok(v) => v,
                    Err(err) => return Ok(err),
                };
            if is_blank(&parsed.summary) {
                return Ok(ToolResult::error("summary must be nonblank"));
            }
            Ok(ToolResult::ok(parsed.summary).with_metadata(meta_obj(&[
                ("helper_role", json!("advisor")),
                ("verdict", json!(parsed.verdict.as_str())),
            ])))
        }
    }

    pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
        let advisor = config.get(ToolName::SubmitAdvisorFeedback);
        crate::tools::register_tool(
            registry,
            ToolName::SubmitAdvisorFeedback,
            advisor,
            text_spec(
                ToolName::SubmitAdvisorFeedback,
                &advisor.description,
                schema_for!(SubmitAdvisorFeedbackInput),
            ),
            OutputShape::Text,
            Arc::new(SubmitAdvisorFeedback),
        );
    }
}
mod subagent {
    //! The `submit_subagent_result` terminal.

    use std::sync::Arc;

    use async_trait::async_trait;
    use eos_types::JsonObject;
    use schemars::{schema_for, JsonSchema};
    use serde::{Deserialize, Serialize};
    use serde_json::json;

    use crate::registry::text_spec;
    use crate::registry::ToolConfigSet;
    use crate::tools::parse_input;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolRegistry;
    use crate::{OutputShape, ToolResult};

    use super::{is_blank, meta_obj};

    #[derive(Debug, Deserialize, Serialize, JsonSchema)]
    pub(super) struct SubmitSubagentResultInput {
        summary: String,
        #[serde(default)]
        findings: Vec<String>,
        #[serde(default)]
        references: Vec<String>,
    }

    struct SubmitSubagentResult;

    #[async_trait]
    impl ToolExecutor for SubmitSubagentResult {
        async fn execute(
            &self,
            input: &JsonObject,
            _ctx: &ExecutionMetadata,
        ) -> Result<ToolResult, ToolError> {
            let parsed: SubmitSubagentResultInput =
                match parse_input(ToolName::SubmitSubagentResult, input) {
                    Ok(v) => v,
                    Err(err) => return Ok(err),
                };
            if is_blank(&parsed.summary) {
                return Ok(ToolResult::error("summary must be nonblank"));
            }
            Ok(ToolResult::ok(parsed.summary).with_metadata(meta_obj(&[
                ("agent_type", json!("subagent")),
                ("findings", json!(parsed.findings)),
                ("references", json!(parsed.references)),
            ])))
        }
    }

    pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
        let subagent = config.get(ToolName::SubmitSubagentResult);
        crate::tools::register_tool(
            registry,
            ToolName::SubmitSubagentResult,
            subagent,
            text_spec(
                ToolName::SubmitSubagentResult,
                &subagent.description,
                schema_for!(SubmitSubagentResultInput),
            ),
            OutputShape::Text,
            Arc::new(SubmitSubagentResult),
        );
    }
}

pub(crate) fn register(
    registry: &mut crate::ToolRegistry,
    config: &crate::registry::ToolConfigSet,
    root: crate::tools::RootSubmissionHandle,
    attempt: crate::tools::AttemptSubmissionHandle,
) {
    planner::register(registry, config, attempt.clone());
    root::register(registry, config, root);
    generator::register(registry, config, attempt.clone());
    reducer::register(registry, config, attempt);
    advisor::register(registry, config);
    subagent::register(registry, config);
}

pub(crate) fn register_schema(
    registry: &mut crate::ToolRegistry,
    config: &crate::registry::ToolConfigSet,
) {
    use crate::registry::text_spec;
    use crate::{OutputShape, ToolName};
    use schemars::schema_for;

    let planner = config.get(ToolName::SubmitPlannerOutcome);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitPlannerOutcome,
        planner,
        text_spec(
            ToolName::SubmitPlannerOutcome,
            &planner.description,
            schema_for!(planner::SubmitPlannerOutcomeInput),
        ),
        OutputShape::Text,
    );
    let root = config.get(ToolName::SubmitRootOutcome);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitRootOutcome,
        root,
        text_spec(
            ToolName::SubmitRootOutcome,
            &root.description,
            schema_for!(root::SubmitRootOutcomeInput),
        ),
        OutputShape::Text,
    );
    let generator = config.get(ToolName::SubmitGeneratorOutcome);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitGeneratorOutcome,
        generator,
        text_spec(
            ToolName::SubmitGeneratorOutcome,
            &generator.description,
            schema_for!(OutcomeInput),
        ),
        OutputShape::Text,
    );
    let reducer = config.get(ToolName::SubmitReducerOutcome);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitReducerOutcome,
        reducer,
        text_spec(
            ToolName::SubmitReducerOutcome,
            &reducer.description,
            schema_for!(OutcomeInput),
        ),
        OutputShape::Text,
    );
    let advisor = config.get(ToolName::SubmitAdvisorFeedback);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitAdvisorFeedback,
        advisor,
        text_spec(
            ToolName::SubmitAdvisorFeedback,
            &advisor.description,
            schema_for!(advisor::SubmitAdvisorFeedbackInput),
        ),
        OutputShape::Text,
    );
    let subagent = config.get(ToolName::SubmitSubagentResult);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitSubagentResult,
        subagent,
        text_spec(
            ToolName::SubmitSubagentResult,
            &subagent.description,
            schema_for!(subagent::SubmitSubagentResultInput),
        ),
        OutputShape::Text,
    );
}
