#![allow(clippy::unwrap_used)]
use super::*;

#[test]
fn attempt_defaults_are_set() {
    assert_eq!(AttemptConfig::default().max_concurrent_task_runs, 8);
}

#[test]
fn workflow_defaults_are_set() {
    let cfg = WorkflowConfig::default();
    assert_eq!(cfg.max_depth, 2);
    assert_eq!(cfg.attempt.max_concurrent_task_runs, 8);
}

#[test]
fn parses_hyphenated_max_depth() {
    let cfg: WorkflowConfig = serde_json::from_value(serde_json::json!({
        "max-depth": 3,
        "attempt": {
            "max_concurrent_task_runs": 4,
        },
    }))
    .unwrap();

    assert_eq!(cfg.max_depth, 3);
    assert_eq!(cfg.attempt.max_concurrent_task_runs, 4);
}

#[test]
fn rejects_zero_max_depth() {
    let cfg = WorkflowConfig {
        max_depth: 0,
        ..WorkflowConfig::default()
    };

    let err = cfg.validate().unwrap_err();
    assert!(matches!(
        err,
        ConfigError::OutOfRange { field, .. } if field == "workflow.max-depth"
    ));
}
