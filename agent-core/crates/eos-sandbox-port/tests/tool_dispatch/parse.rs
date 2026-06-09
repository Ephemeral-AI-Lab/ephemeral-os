use super::*;
fn obj(value: Value) -> JsonObject {
    match value {
        Value::Object(map) => map,
        _ => panic!("test json is an object"),
    }
}

// AC-sandbox-api-04 (envelope portion): the daemon identity emits only a
// top-level caller_id and a top-level invocation_id when present. The fixture
// keeps that sandbox-facing identity opaque.
#[test]
fn identity_envelope_has_top_level_caller_and_optional_invocation() {
    let base = SandboxRequestBase {
        caller_id: "caller-1".to_owned(),
        description: String::new(),
        invocation_id: None,
    };
    let payload = daemon_request_identity_fields(&base);
    assert_eq!(payload["caller_id"], serde_json::json!("caller-1"));
    assert!(!payload.contains_key("caller"));
    assert!(!payload.contains_key("invocation_id"));

    let base = SandboxRequestBase {
        caller_id: "caller-1".to_owned(),
        description: String::new(),
        invocation_id: Some("inv-9".parse().expect("non-empty")),
    };
    let payload = daemon_request_identity_fields(&base);
    assert_eq!(payload["invocation_id"], serde_json::json!("inv-9"));
}

// AC-sandbox-api-03: read decodes content/exists/encoding and timings.
#[test]
fn parse_read_file_decodes_fields_and_timings() {
    let response = obj(serde_json::json!({
        "success": true,
        "exists": true,
        "content": "hello",
        "encoding": "utf-8",
        "timings": {"api.read.total_s": 0.5},
    }));
    let result = parse_read_file_result(&response).expect("parse");
    assert!(result.base.success);
    assert!(result.exists);
    assert_eq!(result.content, "hello");
    assert_eq!(result.encoding, "utf-8");
    assert_eq!(result.base.timings.get("api.read.total_s"), Some(&0.5));
}

// AC-sandbox-api-03: missing `success`/`exists` decode to false (fail-closed).
#[test]
fn parse_missing_success_and_exists_are_false() {
    let response = obj(serde_json::json!({"content": "x"}));
    let result = parse_read_file_result(&response).expect("parse");
    assert!(!result.base.success, "missing success is false");
    assert!(!result.exists, "missing exists is false");

    let guarded = parse_write_file_result(&obj(serde_json::json!({}))).expect("parse");
    assert!(
        !guarded.base.success,
        "missing success is false for guarded"
    );
}

// AC-sandbox-api-03: blank/whitespace path entries and blank kind pairs are
// filtered by the guarded parser, replicating the Rust filters.
#[test]
fn parse_drops_blank_paths_and_kinds() {
    let response = obj(serde_json::json!({
        "success": false,
        "changed_paths": ["real.txt", "  ", ""],
        "changed_path_kinds": {"real.txt": "modified", "": "x", "blank": "  "},
        "status": "ok",
    }));
    let result = parse_write_file_result(&response).expect("parse");
    assert_eq!(result.base.changed_paths, vec!["real.txt"]);
    assert_eq!(result.changed_path_kinds.len(), 1);
    assert_eq!(
        result.changed_path_kinds.get("real.txt"),
        Some(&"modified".to_owned())
    );
}

// AC-sandbox-api-03: ExecCommandResult.success is derived from status.
#[test]
fn parse_exec_derives_success_from_status() {
    let ok = parse_exec_command_result(&obj(serde_json::json!({
        "status": "completed",
        "exit_code": 0,
        "output": {"stdout": "hi", "stderr": ""},
    })))
    .expect("parse");
    assert!(ok.base.success);
    assert_eq!(ok.status, "completed");
    assert_eq!(ok.exit_code, Some(0));
    assert_eq!(ok.output.stdout, "hi");

    for failing in ["error", "timed_out"] {
        let result = parse_exec_command_result(&obj(serde_json::json!({"status": failing})))
            .expect("parse");
        assert!(!result.base.success, "status {failing} is not success");
    }

    // Missing status: success (empty status is not in the failure set), but
    // the status field falls back to "error".
    let missing = parse_exec_command_result(&obj(serde_json::json!({}))).expect("parse");
    assert!(missing.base.success);
    assert_eq!(missing.status, "error");
    assert_eq!(missing.exit_code, None);
}

#[test]
fn parse_exec_does_not_filter_changed_paths() {
    // Exec uses the unfiltered list/map, unlike the guarded parser.
    let result = parse_exec_command_result(&obj(serde_json::json!({
        "status": "completed",
        "changed_paths": ["a", ""],
        "changed_path_kinds": {"a": "m", "": "x"},
    })))
    .expect("parse");
    assert_eq!(result.base.changed_paths, vec!["a", ""]);
    assert_eq!(result.changed_path_kinds.len(), 2);
}

// strict_int rejects bool-as-int; raw serde would silently coerce.
#[test]
fn strict_int_rejects_bool() {
    let response = obj(serde_json::json!({"success": true, "applied_edits": true}));
    assert!(parse_edit_file_result(&response).is_err());
}

// Workspace mode now comes from the daemon response when present.
#[test]
fn parse_preserves_workspace_field() {
    let response = obj(serde_json::json!({"success": true, "workspace": "isolated"}));
    let result = parse_write_file_result(&response).expect("parse");
    assert_eq!(result.base.workspace, Workspace::Isolated);

    let read = parse_read_file_result(&obj(serde_json::json!({
        "success": true,
        "workspace_mode": "isolated",
    })))
    .expect("parse");
    assert_eq!(read.base.workspace, Workspace::Isolated);

    let exec = parse_exec_command_result(&obj(serde_json::json!({
        "status": "completed",
        "workspace": "isolated",
    })))
    .expect("parse");
    assert_eq!(exec.base.workspace, Workspace::Isolated);
}

#[test]
fn guarded_parses_conflict_and_changed_paths() {
    let response = obj(serde_json::json!({
        "success": false,
        "status": "aborted_overlap",
        "conflict": {"reason": "aborted_overlap", "conflict_file": "a.txt", "message": "overlap"},
        "conflict_reason": "overlap",
        "changed_paths": ["a.txt"],
        "error": {"code": "x"},
    }));
    let result = parse_edit_file_result(&response).expect("parse");
    assert_eq!(result.status, "aborted_overlap");
    let conflict = result.base.conflict.expect("conflict");
    assert_eq!(conflict.reason, "aborted_overlap");
    assert_eq!(conflict.conflict_file.as_deref(), Some("a.txt"));
    assert_eq!(result.base.conflict_reason.as_deref(), Some("overlap"));
    assert!(result.base.error.is_some());
}

#[test]
fn guarded_mutation_source_collapses_falsy() {
    // Rust `str(response.get("mutation_source") or "")`: a falsy value
    // collapses to "" (not "False"/"0"); a truthy string is kept.
    for falsy in [
        serde_json::json!(false),
        serde_json::json!(0),
        serde_json::json!(""),
        serde_json::Value::Null,
    ] {
        let response = obj(serde_json::json!({"success": true, "mutation_source": falsy}));
        let result = parse_write_file_result(&response).expect("parse");
        assert_eq!(result.mutation_source, "", "falsy mutation_source");
    }
    let kept = parse_write_file_result(&obj(
        serde_json::json!({"success": true, "mutation_source": "overlay"}),
    ))
    .expect("parse");
    assert_eq!(kept.mutation_source, "overlay");
}

#[test]
fn user_visible_strips_internal_error_prefix() {
    assert_eq!(user_visible_error_message("internal_error: boom"), "boom");
    assert_eq!(user_visible_error_message("plain"), "plain");
}

#[test]
fn conflict_classifier_matches_code_or_marker() {
    // Code match.
    assert!(is_edit_conflict(&SandboxPortError::transport(
        Some("aborted_overlap".to_owned()),
        "anything",
    )));
    // Marker match (case-insensitive, prefix-stripped).
    assert!(is_edit_conflict(&SandboxPortError::transport(
        None,
        "internal_error: Anchor Not Found here",
    )));
    // Non-conflict.
    assert!(!is_edit_conflict(&SandboxPortError::transport(
        Some("boom".to_owned()),
        "random failure",
    )));
    // A decode error is never a conflict.
    assert!(!is_edit_conflict(&SandboxPortError::decode("bad number")));
}
