//! Config loader contract: the committed baseline loads, overrides merge, range
//! validation fires, and a `providers:` / `workflow:` section is rejected — the
//! enforceable form of "`ServerConfig` embeds no provider/workflow config" (AC11).
#![allow(clippy::unwrap_used)] // unwrap is permitted in tests

use std::path::{Path, PathBuf};

use eos_backend_config::{load_from_paths, ConfigError};

/// `backend-server/config/backend.yml`, resolved from this crate's manifest dir
/// (`backend-server/crates/eos-backend-config`).
fn baseline() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .unwrap()
        .join("config/backend.yml")
}

/// Write a uniquely-named temp YAML override and return its path.
fn temp_yaml(name: &str, content: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "eos_backend_config_{}_{name}.yml",
        std::process::id()
    ));
    std::fs::write(&path, content).unwrap();
    path
}

#[test]
fn loads_committed_baseline() {
    let config = load_from_paths(&[baseline()]).unwrap();
    assert_eq!(config.bind.port(), 8080);
    assert_eq!(config.sandbox.max_owned_sandboxes, 16);
    assert!(config.sandbox.destroy_on_finish);
    assert_eq!(config.sandbox.startup_timeout_ms, 30000);
    assert!(!config.obs.include_sandbox_audit);
    assert!(config.obs.event_queue_capacity >= 1);
    assert!(!config.agent_core.database_url.is_empty());
    assert!(!config
        .agent_core
        .message_records_root
        .as_os_str()
        .is_empty());
}

#[test]
fn override_merges_over_baseline() {
    let over = temp_yaml("merge", "sandbox:\n  max_owned_sandboxes: 4\n");
    let config = load_from_paths(&[baseline(), over.clone()]).unwrap();
    let _ = std::fs::remove_file(&over);

    assert_eq!(config.sandbox.max_owned_sandboxes, 4, "override wins");
    assert!(config.sandbox.destroy_on_finish, "baseline field survives");
    assert_eq!(
        config.bind.port(),
        8080,
        "untouched baseline field survives"
    );
}

#[test]
fn rejects_providers_section() {
    let over = temp_yaml("providers", "providers:\n  default: anthropic\n");
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);

    let err = result.unwrap_err();
    assert!(matches!(err, ConfigError::Schema(_)));
    assert!(err.to_string().contains("schema"), "{err}");
}

#[test]
fn rejects_workflow_section() {
    let over = temp_yaml("workflow", "workflow:\n  max_depth: 3\n");
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);
    assert!(matches!(result, Err(ConfigError::Schema(_))));
}

#[test]
fn rejects_unknown_field() {
    let over = temp_yaml("unknown", "bogus: 1\n");
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);
    assert!(matches!(result, Err(ConfigError::Schema(_))));
}

#[test]
fn rejects_empty_agent_core_database_url() {
    let over = temp_yaml("empty_db", "agent_core:\n  database_url: \"\"\n");
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);
    assert!(matches!(
        result,
        Err(ConfigError::Empty {
            field: "agent_core.database_url"
        })
    ));
}

#[test]
fn rejects_empty_agent_core_message_records_root() {
    let over = temp_yaml(
        "empty_message_records",
        "agent_core:\n  message_records_root: \"\"\n",
    );
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);
    assert!(matches!(
        result,
        Err(ConfigError::Empty {
            field: "agent_core.message_records_root"
        })
    ));
}

#[test]
fn rejects_out_of_range_max_owned_sandboxes() {
    let over = temp_yaml("range", "sandbox:\n  max_owned_sandboxes: 0\n");
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);
    assert!(matches!(
        result,
        Err(ConfigError::OutOfRange {
            field: "sandbox.max_owned_sandboxes",
            ..
        })
    ));
}

#[test]
fn rejects_out_of_range_startup_timeout_ms() {
    let over = temp_yaml("timeout", "sandbox:\n  startup_timeout_ms: 0\n");
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);
    assert!(matches!(
        result,
        Err(ConfigError::OutOfRange {
            field: "sandbox.startup_timeout_ms",
            ..
        })
    ));
}

#[test]
fn rejects_out_of_range_event_queue_capacity() {
    let over = temp_yaml("event_queue", "obs:\n  event_queue_capacity: 0\n");
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);
    assert!(matches!(
        result,
        Err(ConfigError::OutOfRange {
            field: "obs.event_queue_capacity",
            ..
        })
    ));
}

#[test]
fn rejects_out_of_range_audit_queue_capacity() {
    let over = temp_yaml("audit_queue", "obs:\n  audit_queue_capacity: 0\n");
    let result = load_from_paths(&[baseline(), over.clone()]);
    let _ = std::fs::remove_file(&over);
    assert!(matches!(
        result,
        Err(ConfigError::OutOfRange {
            field: "obs.audit_queue_capacity",
            ..
        })
    ));
}
