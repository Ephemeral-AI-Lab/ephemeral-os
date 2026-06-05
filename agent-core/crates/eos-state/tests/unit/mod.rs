use super::*;
use proptest::prelude::*;
use serde_json::json;
use std::path::PathBuf;

/// Enumerate every `src/*.rs` file as `(path, contents)` for the AC
/// source-scan tests. Scoped to enumeration only: the per-test needle
/// assembly and matching logic stay inline so this helper never carries a
/// forbidden literal (it is itself walked by those greps).
fn crate_src_files() -> Vec<(PathBuf, String)> {
    let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    for entry in std::fs::read_dir(&src).expect("read src dir") {
        let path = entry.expect("dir entry").path();
        if path.extension().and_then(|e| e.to_str()) != Some("rs") {
            continue;
        }
        let text = std::fs::read_to_string(&path).expect("read src file");
        files.push((path, text));
    }
    files
}

// AC-eos-state-03: every enum serializes to the exact Python wire string.
#[test]
fn serde_wire_values_match_python() {
    assert_eq!(json!(TaskStatus::Pending), json!("pending"));
    assert_eq!(json!(TaskStatus::Blocked), json!("blocked"));
    assert_eq!(json!(RequestStatus::Running), json!("running"));
    assert_eq!(json!(RequestStatus::Done), json!("done"));
    assert_eq!(json!(RequestStatus::Failed), json!("failed"));
    assert_eq!(json!(TaskRole::Root), json!("root"));
    assert_eq!(json!(TaskRole::Generator), json!("generator"));
    assert_eq!(json!(WorkflowStatus::Open), json!("open"));
    assert_eq!(json!(WorkflowStatus::Cancelled), json!("cancelled"));
    assert_eq!(json!(IterationStatus::Succeeded), json!("succeeded"));
    assert_eq!(
        json!(IterationCreationReason::DeferredGoalContinuation),
        json!("deferred_goal_continuation")
    );
    assert_eq!(json!(AttemptStage::Run), json!("run"));
    assert_eq!(json!(AttemptStatus::Passed), json!("passed"));
    assert_eq!(json!(AttemptFailReason::TaskFailed), json!("task_failed"));
    assert_eq!(
        json!(AttemptFailReason::StartupFailed),
        json!("startup_failed")
    );
    assert_eq!(json!(TaskOutcomeStatus::Success), json!("success"));
    assert_eq!(json!(ExecutionRole::Reducer), json!("reducer"));
    assert_eq!(json!(PlannerKind::Completes), json!("completes"));
    assert_eq!(
        json!(PlannerFailReason::RunExhausted),
        json!("run_exhausted")
    );

    // Round-trips back to the variant.
    let s: IterationCreationReason =
        serde_json::from_value(json!("deferred_goal_continuation")).expect("parse");
    assert_eq!(s, IterationCreationReason::DeferredGoalContinuation);
}

// AC-eos-state-03 (proptest half): the `serialize -> deserialize == identity`
// round-trip the spec names, complementing the exact-wire-string table test
// above. Exercises the wire-bearing enums + newtype ids + free text over
// arbitrary field content (so the `proptest` dev-dep is a real test, not
// unused scaffolding).
fn any_task_outcome_status() -> impl Strategy<Value = TaskOutcomeStatus> {
    prop_oneof![
        Just(TaskOutcomeStatus::Success),
        Just(TaskOutcomeStatus::Failed)
    ]
}

fn any_execution_role() -> impl Strategy<Value = ExecutionRole> {
    prop_oneof![Just(ExecutionRole::Generator), Just(ExecutionRole::Reducer)]
}

fn any_planner_kind() -> impl Strategy<Value = PlannerKind> {
    prop_oneof![Just(PlannerKind::Completes), Just(PlannerKind::Defers)]
}

fn any_task_id() -> impl Strategy<Value = TaskId> {
    "[A-Za-z0-9_:-]{1,24}".prop_map(|s| s.parse().expect("non-empty id"))
}

fn any_attempt_id() -> impl Strategy<Value = AttemptId> {
    "[A-Za-z0-9_:-]{1,24}".prop_map(|s| s.parse().expect("non-empty id"))
}

proptest! {
    #[test]
    fn execution_task_outcome_serde_roundtrip(
        status in any_task_outcome_status(),
        role in any_execution_role(),
        tid in any_task_id(),
        outcome in ".{0,64}",
    ) {
        let value = ExecutionTaskOutcome { status, role, task_id: tid, outcome };
        let encoded = serde_json::to_string(&value).expect("serialize");
        let decoded: ExecutionTaskOutcome =
            serde_json::from_str(&encoded).expect("deserialize");
        prop_assert_eq!(value, decoded);
    }

    #[test]
    fn planner_submission_serde_roundtrip(
        attempt_id in any_attempt_id(),
        planner_task_id in any_task_id(),
        kind in any_planner_kind(),
        generator_task_ids in prop::collection::vec(any_task_id(), 0..4),
        reducer_task_ids in prop::collection::vec(any_task_id(), 0..4),
        deferred in prop::option::of(".{0,32}"),
    ) {
        let value = PlannerSubmission {
            attempt_id,
            planner_task_id,
            kind,
            generator_task_ids,
            reducer_task_ids,
            deferred_goal_for_next_iteration: deferred,
        };
        let encoded = serde_json::to_string(&value).expect("serialize");
        let decoded: PlannerSubmission =
            serde_json::from_str(&encoded).expect("deserialize");
        prop_assert_eq!(value, decoded);
    }
}

// AC-eos-state-04: a Rust-side drift-guard snapshot of the submission +
// outcome schemas. (Type-level Pydantic parity is deferred to cutover, as
// there is no recorded Python golden — consistent with eos-config's note.)
#[test]
fn submission_schema_snapshot() {
    let schemas = json!({
        "ExecutionTaskOutcome": schemars::schema_for!(ExecutionTaskOutcome),
        "PlannerSubmission": schemars::schema_for!(PlannerSubmission),
        "PlannerFailureSubmission": schemars::schema_for!(PlannerFailureSubmission),
        "GeneratorSubmission": schemars::schema_for!(GeneratorSubmission),
        "ReducerSubmission": schemars::schema_for!(ReducerSubmission),
    });
    insta::assert_json_snapshot!("submission_schemas", schemas);
}

// AC-eos-state-02: the forbidden profile-alias role token (the non-generator
// execution alias) appears nowhere in crate source. The needle is assembled
// so the literal does not appear in this file.
#[test]
fn state_role_naming_is_generator() {
    // Compile-time variant assertion: the execution role is Generator.
    let _ = ExecutionRole::Generator;
    let _ = TaskRole::Generator;

    let needle = format!("exec{}", "utor"); // avoid the literal token here
    let mut offenders = Vec::new();
    for (path, text) in crate_src_files() {
        // Lowercase so any-case spelling is caught; `execution` (e-x-e-c-u-t-i-o-n)
        // does not contain the needle (e-x-e-c-u-t-o-r), so it is not a false hit.
        if text.to_lowercase().contains(&needle) {
            offenders.push(path.display().to_string());
        }
    }
    assert!(
        offenders.is_empty(),
        "forbidden role token found in: {offenders:?}"
    );
}

// AC-eos-state-07: `ModelRegistration` exposes `model_key` (not `key`),
// treats `class_path` as an opaque migration field, and `eos-state` contains
// no `class_path`-based dispatch branch (GC-eos-state-04).
#[test]
fn model_registration_no_class_path_dispatch() {
    // Field-presence (compile-time): the DTO exposes the normalized
    // `model_key`, and the migration-only import path is an opaque field.
    // Reading `.model_key` proves the rename away from the DB column `key`.
    let reg = ModelRegistration {
        id: 1,
        model_key: "anthropic:claude".to_owned(),
        label: "Claude".to_owned(),
        class_path: "providers.anthropic.Client".to_owned(),
        kwargs_json: "{}".to_owned(),
        is_active: true,
        created_at: UtcDateTime::now(),
        updated_at: UtcDateTime::now(),
    };
    let _normalized_key: &str = &reg.model_key;
    let _opaque_path: &str = &reg.class_path;

    // Source scan: the migration-only field is never used to branch. The
    // needle is assembled so the contiguous token does not appear on this
    // logic line (it would otherwise self-match), mirroring AC-02.
    let needle = format!("class{}path", "_");
    let dispatch_markers = [
        "match ",
        "=>",
        "==",
        " if ",
        "else ",
        ".starts_with(",
        ".contains(",
    ];
    let mut offenders = Vec::new();
    for (path, text) in crate_src_files() {
        for line in text.lines() {
            let lower = line.to_lowercase();
            if lower.contains(&needle) && dispatch_markers.iter().any(|m| lower.contains(m)) {
                offenders.push(format!("{}: {}", path.display(), line.trim()));
            }
        }
    }
    assert!(
        offenders.is_empty(),
        "migration-only field used in a dispatch branch (forbidden, GC-eos-state-04): {offenders:?}"
    );
}
