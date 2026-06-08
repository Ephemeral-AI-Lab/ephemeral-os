//! Workflow behavior over shared state contracts.

mod projections;

pub(crate) use projections::{
    attempt_execution_outcomes, project_attempt_outcomes, project_iteration_outcomes,
};
