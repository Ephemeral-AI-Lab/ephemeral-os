#![allow(clippy::unwrap_used)]
use std::time::Duration;

use eos_types::ConfigError;
use tempfile::tempdir;

use super::*;

#[test]
fn engine_runtime_config_defaults_to_one_second() {
    let cfg = EngineRuntimeConfig::default();

    assert_eq!(
        cfg.background_completion_poll_interval_ms,
        DEFAULT_BACKGROUND_COMPLETION_POLL_INTERVAL_MS
    );
    assert_eq!(
        cfg.background_completion_poll_interval(),
        Duration::from_secs(1)
    );
}

#[test]
fn engine_runtime_config_parses_yaml_background_interval() {
    let cfg: EngineRuntimeConfig = serde_yaml::from_str(
        r#"
background_completion_poll_interval_ms: 250
"#,
    )
    .unwrap();

    assert_eq!(cfg.background_completion_poll_interval_ms, 250);
    assert_eq!(
        cfg.background_completion_poll_interval(),
        Duration::from_millis(250)
    );
}

#[test]
fn engine_runtime_config_accepts_legacy_command_session_alias() {
    let cfg: EngineRuntimeConfig = serde_yaml::from_str(
        r#"
command_session_completion_poll_interval_ms: 500
"#,
    )
    .unwrap();

    assert_eq!(cfg.background_completion_poll_interval_ms, 500);
}

#[test]
fn engine_runtime_config_loads_runtime_section_from_yaml_layers() {
    let dir = tempdir().unwrap();
    let prd = dir.path().join("prd.yml");
    let local = dir.path().join("local.yml");
    std::fs::write(
        &prd,
        r#"
runtime:
  background_completion_poll_interval_ms: 1000
providers:
  active: unused
"#,
    )
    .unwrap();
    std::fs::write(
        &local,
        r#"
runtime:
  background_completion_poll_interval_ms: 125
"#,
    )
    .unwrap();

    let cfg = EngineRuntimeConfig::load_from_paths(&[prd, local]).unwrap();

    assert_eq!(cfg.background_completion_poll_interval_ms, 125);
    assert_eq!(
        cfg.background_completion_poll_interval(),
        Duration::from_millis(125)
    );
}

#[test]
fn engine_runtime_config_rejects_zero_interval() {
    let cfg = EngineRuntimeConfig {
        background_completion_poll_interval_ms: 0,
    };

    let err = cfg.validate().unwrap_err();
    assert!(matches!(
        err,
        ConfigError::OutOfRange { field, .. }
            if field == "runtime.background_completion_poll_interval_ms"
    ));
}
