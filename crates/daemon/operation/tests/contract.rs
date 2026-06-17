use operation::{
    command::{CommandMetadata, CommandResponse, CommandStatus},
    ChangedPathKind, ChangedPathKinds, CommandId, MutationCore, MutationSource, WorkspaceConflict,
    WorkspaceKind, WorkspaceTimings,
};
use serde_json::{json, Map, Value};

#[test]
fn command_finalize_conflict_response_matches_fixture() {
    let mut changed_path_kinds = ChangedPathKinds::default();
    changed_path_kinds.insert("src/main.rs".to_owned(), ChangedPathKind::Write);
    let mut timings = WorkspaceTimings::default();
    timings.insert("command_exec.total_s".to_owned(), json!(1.25));

    let response = CommandResponse {
        status: CommandStatus::Ok,
        exit_code: Some(0),
        stdout: String::new(),
        stderr: String::new(),
        command_id: Some(CommandId::new("cmd_conflict")),
        finalized: Some(CommandMetadata {
            core: MutationCore {
                success: false,
                changed_paths: vec!["src/main.rs".to_owned()],
                changed_path_kinds,
                mutation_source: Some(MutationSource::OverlayCapture),
                conflict: Some(WorkspaceConflict::path(
                    "aborted_overlap",
                    "src/main.rs",
                    "conflict on src/main.rs",
                )),
                conflict_reason: Some("conflict on src/main.rs".to_owned()),
                timings,
            },
            workspace: WorkspaceKind::Host,
            extras: {
                let mut extras = Map::new();
                extras.insert(
                    "publish_lanes".to_owned(),
                    json!({
                        "source": {
                            "path_count": 1,
                            "publish_status": "conflict",
                            "drop_reason": null,
                        },
                        "ignored": {
                            "path_count": 0,
                            "bytes": 0,
                            "spooled_bytes": 0,
                            "publish_status": "empty",
                            "publish_mode": null,
                            "drop_reason": null,
                        },
                        "routing": {
                            "ignore_route_source": "command_snapshot",
                            "route_manifest_version": 0,
                            "dropped_path_count": 0,
                            "drop_reason_counts": {},
                        },
                    }),
                );
                extras
            },
        }),
    }
    .to_wire_value();
    let fixture: Value = serde_json::from_str(include_str!(
        "../fixtures/command_finalize_conflict_response.json"
    ))
    .expect("valid command finalize conflict fixture");

    assert_eq!(response, fixture);
}
