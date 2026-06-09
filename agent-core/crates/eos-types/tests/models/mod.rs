#![allow(clippy::unwrap_used)]

use serde_json::json;

use super::*;

#[test]
fn defaults_are_empty() {
    let cfg = ModelsConfig::default();

    assert_eq!(cfg.active_key(), None);
    assert!(cfg.registrations.is_empty());
    cfg.validate().unwrap();
}

#[test]
fn parses_registrations() {
    let cfg: ModelsConfig = serde_yaml::from_str(
        r#"
active: claude-sonnet-4-6
registrations:
  - key: claude-sonnet-4-6
    label: Claude Sonnet
    class_path: legacy.Claude
    kwargs:
      effort: high
"#,
    )
    .unwrap();

    assert_eq!(cfg.active_key(), Some("claude-sonnet-4-6"));
    assert_eq!(cfg.registrations[0].key(), "claude-sonnet-4-6");
    assert_eq!(cfg.registrations[0].label(), "Claude Sonnet");
    assert_eq!(cfg.registrations[0].class_path(), "legacy.Claude");
    assert_eq!(cfg.registrations[0].kwargs["effort"], json!("high"));
    cfg.validate().unwrap();
}

#[test]
fn active_only_config_is_valid() {
    let cfg: ModelsConfig = serde_yaml::from_str("active: claude-sonnet-4-6\n").unwrap();

    assert_eq!(cfg.active_key(), Some("claude-sonnet-4-6"));
    cfg.validate().unwrap();
    assert_eq!(
        cfg.active_registration().unwrap().key(),
        "claude-sonnet-4-6"
    );
}

#[test]
fn rejects_blank_and_duplicate_keys() {
    let blank_active: ModelsConfig = serde_yaml::from_str("active: '  '\n").unwrap();
    assert!(matches!(
        blank_active.validate().unwrap_err(),
        ConfigError::MissingValue { field } if field == "models.active"
    ));

    let blank_key: ModelsConfig = serde_yaml::from_str(
        r#"
registrations:
  - key: ''
"#,
    )
    .unwrap();
    assert!(matches!(
        blank_key.validate().unwrap_err(),
        ConfigError::MissingValue { field } if field == "models.registrations[0].key"
    ));

    let duplicate: ModelsConfig = serde_yaml::from_str(
        r#"
registrations:
  - key: one
  - key: one
"#,
    )
    .unwrap();
    assert!(matches!(
        duplicate.validate().unwrap_err(),
        ConfigError::OutOfRange { field, .. } if field == "models.registrations[1].key"
    ));
}

#[test]
fn validate_at_uses_nested_field_prefix() {
    let cfg: ModelsConfig = serde_yaml::from_str(
        r#"
registrations:
  - key: ''
"#,
    )
    .unwrap();

    assert!(matches!(
        cfg.validate_at("providers.codex_coding_plan.models")
            .unwrap_err(),
        ConfigError::MissingValue { field }
            if field == "providers.codex_coding_plan.models.registrations[0].key"
    ));
}
