use super::*;
use eos_types::{RequestId, TaskRole};

fn tid(s: &str) -> TaskId {
    s.parse().expect("task id")
}

fn task(id: &str, status: TaskStatus, needs: &[&str]) -> Task {
    Task {
        id: tid(id),
        request_id: RequestId::new_v4(),
        role: TaskRole::Generator,
        instruction: "do".to_owned(),
        status,
        workflow_id: None,
        iteration_id: None,
        attempt_id: None,
        agent_name: Some("coder".to_owned()),
        needs: needs.iter().map(|n| tid(n)).collect(),
        outcomes: Vec::new(),
        terminal_payload: None,
    }
}

#[test]
fn dag_status_mixed_ready_and_quiescent() {
    let tasks = vec![
        task("g1", TaskStatus::Done, &[]),
        task("g2", TaskStatus::Pending, &["g1"]),
        task("r1", TaskStatus::Pending, &["g2"]),
    ];
    assert_eq!(
        ready_pending_plan_ids(&tasks).expect("ready ids"),
        vec![tid("g2")]
    );
    assert_eq!(
        dag_resolution(&tasks).expect("dag resolution"),
        DagResolution::Running
    );
}

#[test]
fn failed_ancestor_makes_pending_descendant_quiescent() {
    let tasks = vec![
        task("g1", TaskStatus::Failed, &[]),
        task("r1", TaskStatus::Pending, &["g1"]),
    ];
    assert_eq!(
        dag_resolution(&tasks).expect("dag resolution"),
        DagResolution::FailedOrBlocked
    );
}

#[test]
fn unknown_need_and_cycle_error() {
    let unknown = vec![task("g1", TaskStatus::Pending, &["missing"])];
    assert!(ready_pending_plan_ids(&unknown).is_err());

    let cycle = vec![
        task("a", TaskStatus::Pending, &["b"]),
        task("b", TaskStatus::Pending, &["a"]),
    ];
    assert!(dag_resolution(&cycle).is_err());
}

// ---- authoring-time plan-shape validation (`validate_plan_shape`) --------
//
// The cycle test above exercises `dag_resolution` (the persisted-state
// scheduler). `validate_plan_shape` is the distinct authoring-time gate that
// rejects a malformed planner submission before any task row is written; its
// reject branches (and `assert_acyclic`) were untested. Each case isolates
// one rejection and pairs it with the accepted baseline, so the test fails if
// a guard is dropped (malformed -> Ok) or the baseline breaks (valid -> Err).

fn gen_id(id: &str) -> eos_types::GeneratorId {
    eos_types::GeneratorId::new(id).expect("generator id")
}

fn red_id(id: &str) -> eos_types::ReducerId {
    eos_types::ReducerId::new(id).expect("reducer id")
}

fn gen(id: &str, needs: &[&str]) -> eos_types::PlanTask {
    eos_types::PlanTask {
        generator_id: gen_id(id),
        agent_name: "coder".to_owned(),
        needs: needs.iter().map(|n| gen_id(n)).collect(),
    }
}

fn red(id: &str, needs: &[&str]) -> eos_types::PlanReducer {
    eos_types::PlanReducer {
        reducer_id: red_id(id),
        needs: needs.iter().map(|n| gen_id(n)).collect(),
        prompt: "reduce".to_owned(),
    }
}

fn shape_plan(
    tasks: Vec<eos_types::PlanTask>,
    reducers: Vec<eos_types::PlanReducer>,
) -> PlannerPlan {
    let task_specs = tasks
        .iter()
        .map(|task| (task.generator_id.clone(), "spec".to_owned()))
        .collect();
    PlannerPlan {
        attempt_id: eos_types::AttemptId::new_v4(),
        planner_task_id: tid("planner"),
        disposition: eos_types::PlanDisposition::Complete,
        tasks,
        task_specs,
        reducers,
    }
}

#[test]
fn validate_plan_shape_accepts_valid_and_rejects_each_malformation() {
    // Baseline: one generator feeding one reducer — a well-formed DAG.
    validate_plan_shape(&shape_plan(vec![gen("g1", &[])], vec![red("r1", &["g1"])]))
        .expect("a well-formed plan validates");

    // No reducer.
    assert!(validate_plan_shape(&shape_plan(vec![gen("g1", &[])], vec![])).is_err());
    // Duplicate generator id.
    assert!(validate_plan_shape(&shape_plan(
        vec![gen("g1", &[]), gen("g1", &[])],
        vec![red("r1", &["g1"])],
    ))
    .is_err());
    // A generator need must name a known generator id.
    assert!(validate_plan_shape(&shape_plan(
        vec![gen("g1", &["r1"])],
        vec![red("r1", &["g1"])],
    ))
    .is_err());
    // A generator with an unknown need.
    assert!(validate_plan_shape(&shape_plan(
        vec![gen("g1", &["ghost"])],
        vec![red("r1", &["g1"])],
    ))
    .is_err());
    // A reducer with no needs.
    assert!(validate_plan_shape(&shape_plan(vec![gen("g1", &[])], vec![red("r1", &[])])).is_err());
    // A reducer need must name a known generator id.
    assert!(validate_plan_shape(&shape_plan(
        vec![gen("g1", &[])],
        vec![red("r1", &["g1"]), red("r2", &["r1"])],
    ))
    .is_err());
    // A reducer with an unknown need (alongside a valid one).
    assert!(validate_plan_shape(&shape_plan(
        vec![gen("g1", &[])],
        vec![red("r1", &["g1", "ghost"])],
    ))
    .is_err());
    // A dangling generator no downstream task needs.
    assert!(validate_plan_shape(&shape_plan(
        vec![gen("g1", &[]), gen("g2", &[])],
        vec![red("r1", &["g1"])],
    ))
    .is_err());
    // A generator dependency cycle (reaches `assert_acyclic`).
    assert!(validate_plan_shape(&shape_plan(
        vec![gen("g1", &["g2"]), gen("g2", &["g1"])],
        vec![red("r1", &["g1"])],
    ))
    .is_err());
}
