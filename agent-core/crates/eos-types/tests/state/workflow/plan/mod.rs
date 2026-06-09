#![allow(clippy::unwrap_used)]

use super::*;

#[test]
fn workflow_role_ids_reject_blank_on_serde_path() {
    let planner: PlannerId = serde_json::from_value(serde_json::json!("planner-1")).unwrap();
    assert_eq!(planner.as_str(), "planner-1");

    assert!(serde_json::from_value::<PlannerId>(serde_json::json!("   ")).is_err());
    assert!(serde_json::from_value::<GeneratorId>(serde_json::json!("")).is_err());
    assert!(serde_json::from_value::<ReducerId>(serde_json::json!("\t")).is_err());
}

#[test]
fn deferred_goal_rejects_blank_on_serde_path() {
    let goal: DeferredGoal = serde_json::from_value(serde_json::json!("continue")).unwrap();
    assert_eq!(goal.as_str(), "continue");

    assert!(serde_json::from_value::<DeferredGoal>(serde_json::json!(" ")).is_err());
}

#[test]
fn attempt_budget_rejects_zero_on_serde_path() {
    assert!(serde_json::from_value::<AttemptBudget>(serde_json::json!(0)).is_err());
    let budget: AttemptBudget = serde_json::from_value(serde_json::json!(3)).unwrap();
    assert_eq!(budget.get(), 3);
}
