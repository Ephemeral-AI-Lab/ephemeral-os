//! Execution-outcome record type and the pure outcome-projection algebra.
//!
//! Ports `workflow/_core/outcomes.py`. Only the **typed** projection algebra
//! lives here; the JSON string ⇆ records codec and the raw-record
//! normalization fallbacks (`task_outcomes_from_row`, `parse_outcomes_record`)
//! move to the `eos-db` parse boundary (spec §6.8). The `Task.outcomes` /
//! `Attempt.outcomes` these projections read are therefore already typed and
//! pre-normalized.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{CoreError, TaskId};

use crate::attempt::{Attempt, AttemptStatus};
use crate::store::TaskStore;

/// Placeholder text for an [`ExecutionTaskOutcome`] outcome with no recorded
/// detail. Shared by the `eos-db` row mapper and the `eos-workflow` context
/// engine so the prompt-facing wording has one source of truth.
pub const NO_OUTCOME: &str = "(no outcome recorded)";

/// Binary status of one execution outcome (Python `TaskOutcomeStatus`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TaskOutcomeStatus {
    /// The task completed successfully.
    Success,
    /// The task failed.
    Failed,
}

impl TaskOutcomeStatus {
    /// The canonical `snake_case` token (matches the `serde` wire form), so
    /// prompt-facing rendering shares one source of truth with serialization.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failed => "failed",
        }
    }
}

/// The execution role an outcome belongs to (Python `ExecutionRole`). Only
/// `generator`/`reducer` execution evidence ever appears in outcomes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionRole {
    /// A generator (execution) task.
    Generator,
    /// A reducer task (the attempt's exit gate).
    Reducer,
}

impl ExecutionRole {
    /// The canonical `snake_case` token (matches the `serde` wire form).
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Generator => "generator",
            Self::Reducer => "reducer",
        }
    }
}

/// One generator/reducer task's terminal execution evidence
/// (Python `ExecutionTaskOutcome`). Bounded to a single persisted task.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ExecutionTaskOutcome {
    /// Whether the task succeeded or failed.
    pub status: TaskOutcomeStatus,
    /// The execution role that produced this outcome.
    pub role: ExecutionRole,
    /// The task that produced this outcome.
    pub task_id: TaskId,
    /// Free-text outcome summary.
    pub outcome: String,
}

/// Fill a *missing* per-record status from the owning task's raw status string
/// (Python `present_status`): `"done"` → `Success`, everything else → `Failed`.
///
/// Do **not** apply this to a record status that is already present — that path
/// uses `_normalize_status` at the `eos-db` boundary, where `"done"` → `Failed`
/// (spec §6.8). The two normalizers are distinct.
#[must_use]
pub fn present_status(raw_status: &str) -> TaskOutcomeStatus {
    if raw_status == "done" {
        TaskOutcomeStatus::Success
    } else {
        TaskOutcomeStatus::Failed
    }
}

/// Construct one execution outcome for a terminal submission
/// (Python `execution_outcome_for_submission`).
#[must_use]
pub fn execution_outcome_for_submission(
    task_id: TaskId,
    role: ExecutionRole,
    status: TaskOutcomeStatus,
    outcome: String,
) -> ExecutionTaskOutcome {
    ExecutionTaskOutcome {
        status,
        role,
        task_id,
        outcome,
    }
}

/// The latest iteration by `sequence_no` (Python `workflow_outcomes` selection
/// half). `sequence_no` is unique per workflow, so there is never a tie.
#[must_use]
pub fn latest_iteration(
    iterations: &[crate::iteration::Iteration],
) -> Option<&crate::iteration::Iteration> {
    iterations.iter().max_by_key(|it| it.sequence_no)
}

/// Project generator/reducer execution outcomes for one attempt
/// (Python `project_attempt_outcomes`). With no store, return the attempt's
/// persisted typed outcomes; otherwise collect each generator/reducer task's
/// pre-normalized outcomes from the store.
///
/// # Errors
/// Propagates any [`TaskStore::get`] failure.
pub async fn project_attempt_outcomes(
    attempt: &Attempt,
    task_store: Option<&dyn TaskStore>,
) -> Result<Vec<ExecutionTaskOutcome>, CoreError> {
    let Some(store) = task_store else {
        return Ok(attempt.outcomes().to_vec());
    };
    let mut out: Vec<ExecutionTaskOutcome> = Vec::new();
    for task_id in attempt
        .generator_task_ids()
        .iter()
        .chain(attempt.reducer_task_ids().iter())
    {
        if let Some(task) = store.get(task_id).await? {
            out.extend(task.outcomes.iter().cloned());
        }
    }
    Ok(out)
}

/// Persisted attempt outcomes when present, else recompute from task rows
/// (Python `attempt_execution_outcomes`).
///
/// # Errors
/// Propagates any [`TaskStore::get`] failure from [`project_attempt_outcomes`].
pub async fn attempt_execution_outcomes(
    attempt: &Attempt,
    task_store: Option<&dyn TaskStore>,
) -> Result<Vec<ExecutionTaskOutcome>, CoreError> {
    if !attempt.outcomes().is_empty() {
        return Ok(attempt.outcomes().to_vec());
    }
    project_attempt_outcomes(attempt, task_store).await
}

/// Execution evidence for the iteration's **closing attempt only**
/// (Python `project_iteration_outcomes`).
///
/// On a passing close, the closing attempt's successful reducer outcomes; on a
/// failed close, that attempt's failed generator/reducer tasks. Reducer
/// successes from earlier failed attempts are internal history and are never
/// surfaced (spec §8 invariant 4 — highest-risk regression).
///
/// # Errors
/// Propagates any [`TaskStore::get`] failure.
pub async fn project_iteration_outcomes(
    attempts: &[Attempt],
    task_store: Option<&dyn TaskStore>,
) -> Result<Vec<ExecutionTaskOutcome>, CoreError> {
    let Some(final_attempt) = attempts.last() else {
        return Ok(Vec::new());
    };
    let final_outcomes = attempt_execution_outcomes(final_attempt, task_store).await?;
    let filtered = if final_attempt.status() == AttemptStatus::Passed {
        final_outcomes
            .into_iter()
            .filter(|o| o.role == ExecutionRole::Reducer && o.status == TaskOutcomeStatus::Success)
            .collect()
    } else {
        final_outcomes
            .into_iter()
            .filter(|o| {
                matches!(o.role, ExecutionRole::Generator | ExecutionRole::Reducer)
                    && o.status == TaskOutcomeStatus::Failed
            })
            .collect()
    };
    Ok(filtered)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::attempt::{Attempt, AttemptClosure, AttemptState, AttemptStatus};
    use crate::support::FakeTaskStore;
    use crate::task::{Task, TaskRole, TaskStatus};
    use eos_types::{AttemptId, IterationId, RequestId, UtcDateTime, WorkflowId};

    fn tid(s: &str) -> TaskId {
        s.parse().expect("non-empty id")
    }

    fn outcome(task: &str, role: ExecutionRole, status: TaskOutcomeStatus) -> ExecutionTaskOutcome {
        ExecutionTaskOutcome {
            status,
            role,
            task_id: tid(task),
            outcome: format!("outcome-{task}"),
        }
    }

    fn task_with(id: &str, role: TaskRole, outcomes: Vec<ExecutionTaskOutcome>) -> Task {
        Task {
            id: tid(id),
            request_id: RequestId::new_v4(),
            role,
            instruction: "do".to_owned(),
            status: TaskStatus::Done,
            workflow_id: None,
            iteration_id: None,
            attempt_id: None,
            agent_name: None,
            needs: Vec::new(),
            outcomes,
            terminal_tool_result: None,
        }
    }

    fn attempt(
        status: AttemptStatus,
        generators: &[&str],
        reducers: &[&str],
        outcomes: Vec<ExecutionTaskOutcome>,
    ) -> Attempt {
        let now = UtcDateTime::now();
        Attempt {
            id: AttemptId::new_v4(),
            iteration_id: IterationId::new_v4(),
            workflow_id: WorkflowId::new_v4(),
            attempt_sequence_no: 0,
            state: AttemptState::Closed {
                closure: match status {
                    AttemptStatus::Passed => AttemptClosure::Passed {
                        outcomes,
                        closed_at: now,
                    },
                    AttemptStatus::Failed => AttemptClosure::Failed {
                        reason: crate::attempt::AttemptFailReason::TaskFailed,
                        outcomes,
                        closed_at: now,
                    },
                    AttemptStatus::Running => {
                        unreachable!("test helper only builds closed attempts")
                    }
                },
                planner_task_id: None,
                plan: Some(crate::MaterializedPlan {
                    planner_task_id: tid("planner"),
                    disposition: crate::PlanDisposition::Complete,
                    generator_task_ids: generators.iter().map(|s| tid(s)).collect(),
                    reducer_task_ids: reducers.iter().map(|s| tid(s)).collect(),
                }),
            },
            created_at: now,
            updated_at: now,
        }
    }

    #[test]
    fn present_status_only_maps_done_to_success() {
        assert_eq!(present_status("done"), TaskOutcomeStatus::Success);
        assert_eq!(present_status("failed"), TaskOutcomeStatus::Failed);
        assert_eq!(present_status(""), TaskOutcomeStatus::Failed);
        assert_eq!(present_status("success"), TaskOutcomeStatus::Failed);
    }

    // AC-eos-state-09: project_attempt_outcomes over an already-normalized
    // Task.outcomes matches Python (the eos-db boundary pre-normalizes records).
    #[tokio::test]
    async fn project_attempt_outcomes_pre_normalized() {
        let store = FakeTaskStore::new();
        let g = task_with(
            "g1",
            TaskRole::Generator,
            vec![outcome(
                "g1",
                ExecutionRole::Generator,
                TaskOutcomeStatus::Success,
            )],
        );
        let r = task_with(
            "r1",
            TaskRole::Reducer,
            vec![outcome(
                "r1",
                ExecutionRole::Reducer,
                TaskOutcomeStatus::Success,
            )],
        );
        store.put(g);
        store.put(r);
        let att = attempt(AttemptStatus::Passed, &["g1"], &["r1"], Vec::new());
        let got = project_attempt_outcomes(&att, Some(&store))
            .await
            .expect("projection");
        assert_eq!(
            got,
            vec![
                outcome("g1", ExecutionRole::Generator, TaskOutcomeStatus::Success),
                outcome("r1", ExecutionRole::Reducer, TaskOutcomeStatus::Success),
            ]
        );
    }

    // AC-eos-state-01: passing close surfaces only reducer successes; failed
    // close surfaces only failed generator/reducer tasks.
    #[tokio::test]
    async fn outcomes_projection_parity() {
        // Passing attempt: persisted outcomes carry a generator success and a
        // reducer success; only the reducer success is iteration evidence.
        let passing = attempt(
            AttemptStatus::Passed,
            &["g1"],
            &["r1"],
            vec![
                outcome("g1", ExecutionRole::Generator, TaskOutcomeStatus::Success),
                outcome("r1", ExecutionRole::Reducer, TaskOutcomeStatus::Success),
            ],
        );
        let got = project_iteration_outcomes(&[passing], None)
            .await
            .expect("projection");
        assert_eq!(
            got,
            vec![outcome(
                "r1",
                ExecutionRole::Reducer,
                TaskOutcomeStatus::Success
            )]
        );

        // Failed attempt: surface failed generator + reducer tasks only.
        let failed = attempt(
            AttemptStatus::Failed,
            &["g1"],
            &["r1"],
            vec![
                outcome("g1", ExecutionRole::Generator, TaskOutcomeStatus::Failed),
                outcome("r1", ExecutionRole::Reducer, TaskOutcomeStatus::Success),
            ],
        );
        let got = project_iteration_outcomes(&[failed], None)
            .await
            .expect("projection");
        assert_eq!(
            got,
            vec![outcome(
                "g1",
                ExecutionRole::Generator,
                TaskOutcomeStatus::Failed
            )]
        );
    }

    // AC-eos-state-05: reducer successes from an earlier failed attempt are
    // never surfaced — only the closing (last) attempt's evidence counts.
    #[tokio::test]
    async fn earlier_attempt_reducer_success_hidden() {
        let first_failed = attempt(
            AttemptStatus::Failed,
            &["g1"],
            &["r1"],
            vec![
                outcome("g1", ExecutionRole::Generator, TaskOutcomeStatus::Failed),
                // A successful reducer in a FAILED attempt — internal history.
                outcome("r1", ExecutionRole::Reducer, TaskOutcomeStatus::Success),
            ],
        );
        let second_passing = attempt(
            AttemptStatus::Passed,
            &["g2"],
            &["r2"],
            vec![outcome(
                "r2",
                ExecutionRole::Reducer,
                TaskOutcomeStatus::Success,
            )],
        );
        let got = project_iteration_outcomes(&[first_failed, second_passing], None)
            .await
            .expect("projection");
        // Only the closing attempt's reducer success appears; r1 is hidden.
        assert_eq!(
            got,
            vec![outcome(
                "r2",
                ExecutionRole::Reducer,
                TaskOutcomeStatus::Success
            )]
        );
    }

    // attempt_execution_outcomes: persisted outcomes win; empty recomputes.
    #[tokio::test]
    async fn attempt_execution_outcomes_persisted_then_recompute() {
        let store = FakeTaskStore::new();
        store.put(task_with(
            "g1",
            TaskRole::Generator,
            vec![outcome(
                "g1",
                ExecutionRole::Generator,
                TaskOutcomeStatus::Failed,
            )],
        ));
        // Persisted non-empty outcomes are returned verbatim (store ignored).
        let with_persisted = attempt(
            AttemptStatus::Failed,
            &["g1"],
            &[],
            vec![outcome(
                "g1",
                ExecutionRole::Generator,
                TaskOutcomeStatus::Success,
            )],
        );
        let got = attempt_execution_outcomes(&with_persisted, Some(&store))
            .await
            .expect("projection");
        assert_eq!(got, with_persisted.outcomes());

        // Empty persisted outcomes recompute from the task rows.
        let empty = attempt(AttemptStatus::Failed, &["g1"], &[], Vec::new());
        let got = attempt_execution_outcomes(&empty, Some(&store))
            .await
            .expect("projection");
        assert_eq!(
            got,
            vec![outcome(
                "g1",
                ExecutionRole::Generator,
                TaskOutcomeStatus::Failed
            )]
        );
    }
}
