//! Boot reap through the public service surface: every persisted handle is a
//! dead session; reap destroys run dirs (containment-guarded), resets the
//! handle file, and reports what it did.

use std::path::{Path, PathBuf};

use sandbox_observability_telemetry::Observer;
use sandbox_runtime_workspace::session::{ResourceCaps, WorkspaceManager};
use sandbox_runtime_workspace::WorkspaceRuntimeService;
use serde_json::json;

fn scratch(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("workspace-reap-{label}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch");
    dir
}

fn service(scratch_root: &Path) -> WorkspaceRuntimeService {
    let manager = WorkspaceManager::new(
        "/workspace",
        ResourceCaps::default(),
        scratch_root.to_path_buf(),
        Observer::disabled(),
    );
    WorkspaceRuntimeService::new(manager, scratch_root.join("layer-stack"))
}

#[test]
fn reap_destroys_run_dirs_and_resets_the_handle_file() {
    let scratch_root = scratch("basic");
    let run_dir = scratch_root.join("ws-dead");
    std::fs::create_dir_all(run_dir.join("upper")).expect("plant run dir");
    let foreign =
        std::env::temp_dir().join(format!("workspace-reap-foreign-{}", std::process::id()));
    std::fs::create_dir_all(&foreign).expect("plant foreign dir");
    let payload = json!({
        "schema_version": 1,
        "handles": [
            {"workspace_handle_id": "ws-dead", "scratch_dir": run_dir.to_string_lossy()},
            {"workspace_handle_id": "ws-escape", "scratch_dir": foreign.to_string_lossy()},
        ],
    });
    std::fs::write(
        scratch_root.join("manager.json"),
        serde_json::to_vec_pretty(&payload).expect("encode"),
    )
    .expect("write manager.json");

    let service = service(&scratch_root);
    let reaped = service.reap_persisted_sessions().expect("reap");
    assert_eq!(reaped.len(), 2);
    assert!(reaped[0].run_dir_removed, "in-scratch run dir destroyed");
    assert!(!run_dir.exists());
    assert!(
        !reaped[1].run_dir_removed,
        "paths outside the scratch root are never followed"
    );
    assert!(foreign.exists(), "foreign dir untouched");

    let rewritten: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(scratch_root.join("manager.json")).expect("read"),
    )
    .expect("parse");
    assert_eq!(
        rewritten["handles"].as_array().map(Vec::len),
        Some(0),
        "reap resets the handle file to the (empty) live set"
    );

    let _ = std::fs::remove_dir_all(&foreign);
    let _ = std::fs::remove_dir_all(&scratch_root);
}

#[test]
fn reap_tolerates_missing_and_garbage_handle_files() {
    let scratch_root = scratch("garbage");
    let service = service(&scratch_root);
    assert!(service.reap_persisted_sessions().expect("reap").is_empty());

    std::fs::write(scratch_root.join("manager.json"), b"{not json").expect("garbage");
    assert!(service.reap_persisted_sessions().expect("reap").is_empty());
    let rewritten = std::fs::read_to_string(scratch_root.join("manager.json")).expect("read");
    assert!(
        serde_json::from_str::<serde_json::Value>(&rewritten).is_ok(),
        "an unparsable handle file is rewritten to a clean empty set"
    );
    let _ = std::fs::remove_dir_all(&scratch_root);
}
