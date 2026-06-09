#![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
use super::*;
use std::str::FromStr;

// AC-types-01: Display/FromStr roundtrip for every id, over arbitrary
// representative non-empty strings; empty input is rejected on every fallible
// path (CoreError::EmptyId).
macro_rules! roundtrip_suite {
    ($modname:ident, $ty:ty) => {
        mod $modname {
            use super::*;

            #[test]
            fn id_display_fromstr_roundtrip() {
                for s in [
                    "simple",
                    "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                    "root-abc123def4567890",
                    "tool-call_01",
                ] {
                    let id = <$ty>::from_str(&s).expect("non-empty parses");
                    assert_eq!(id.to_string(), s);
                }
            }

            #[test]
            fn explicit_shapes_roundtrip() {
                for s in [
                    "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                    "root-abc123def4567890",
                ] {
                    let id = <$ty>::from_str(s).expect("non-empty parses");
                    assert_eq!(id.to_string(), s);
                }
            }

            #[test]
            fn empty_is_rejected_on_every_fallible_path() {
                assert!(matches!(
                    <$ty>::from_str(""),
                    Err(crate::error::CoreError::EmptyId { .. })
                ));
                assert!(matches!(
                    <$ty>::try_from(""),
                    Err(crate::error::CoreError::EmptyId { .. })
                ));
                assert!(matches!(
                    <$ty>::try_from(String::new()),
                    Err(crate::error::CoreError::EmptyId { .. })
                ));
            }
        }
    };
}

roundtrip_suite!(request_id, RequestId);
roundtrip_suite!(task_id, TaskId);
roundtrip_suite!(workflow_id, WorkflowId);
roundtrip_suite!(iteration_id, IterationId);
roundtrip_suite!(attempt_id, AttemptId);
roundtrip_suite!(agent_run_id, AgentRunId);
roundtrip_suite!(sandbox_id, SandboxId);
roundtrip_suite!(tool_use_id, ToolUseId);
roundtrip_suite!(invocation_id, InvocationId);
roundtrip_suite!(command_session_id, CommandSessionId);

// AC-types-02: transparent serde, no key duplication (GC-types-01).
#[test]
fn id_serde_transparent_single_key() {
    let id: TaskId = "t1".parse().unwrap();
    assert_eq!(serde_json::to_value(&id).unwrap(), serde_json::json!("t1"));
    let back: TaskId = serde_json::from_value(serde_json::json!("t1")).unwrap();
    assert_eq!(back, id);

    let empty = serde_json::from_value::<TaskId>(serde_json::json!(""));
    assert!(
        empty.is_err(),
        "transparent id serde must preserve the non-empty invariant"
    );

    #[derive(serde::Serialize)]
    struct Row {
        id: TaskId,
    }
    let row = serde_json::to_value(Row {
        id: "t1".parse().unwrap(),
    })
    .unwrap();
    assert_eq!(row, serde_json::json!({ "id": "t1" }));
    // The id appears under exactly one key (no `id` + `task_id` duplication).
    assert_eq!(row.as_object().unwrap().len(), 1);
}

#[test]
fn try_from_and_parse_agree() {
    let a: TaskId = "x".parse().unwrap();
    let b = TaskId::try_from("x").unwrap();
    let c = TaskId::try_from(String::from("x")).unwrap();
    assert_eq!(a, b);
    assert_eq!(b, c);
    assert_eq!(a.as_str(), "x");
    assert_eq!(a.into_inner(), String::from("x"));
}

#[test]
fn try_from_error_type_is_core_error() {
    // The named contract is `TryFrom<_, Error = CoreError>` — assert the
    // associated error type, not the std blanket `Infallible`.
    fn assert_core_error<T>()
    where
        T: TryFrom<&'static str, Error = crate::error::CoreError>,
    {
    }
    assert_core_error::<TaskId>();
}

#[test]
fn new_v4_is_a_dashed_uuid() {
    let id = RequestId::new_v4();
    // 8-4-4-4-12 dashed form, parses back as a uuid.
    assert!(uuid::Uuid::parse_str(id.as_str()).is_ok());
    assert_eq!(id.as_str().len(), 36);
}

// AC-types-06 (ids portion): every id schemas as a JSON `string`.
#[test]
fn json_schema_ids_are_strings() {
    macro_rules! assert_string_schema {
        ($ty:ty) => {{
            let schema = serde_json::to_value(schemars::schema_for!($ty)).unwrap();
            assert_eq!(
                schema["type"],
                serde_json::json!("string"),
                "{}",
                stringify!($ty)
            );
        }};
    }
    assert_string_schema!(RequestId);
    assert_string_schema!(TaskId);
    assert_string_schema!(WorkflowId);
    assert_string_schema!(IterationId);
    assert_string_schema!(AttemptId);
    assert_string_schema!(AgentRunId);
    assert_string_schema!(SandboxId);
    assert_string_schema!(ToolUseId);
    assert_string_schema!(InvocationId);
    assert_string_schema!(CommandSessionId);
}
