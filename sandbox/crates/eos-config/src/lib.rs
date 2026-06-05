//! Generic loader for the sandbox runtime configuration document.
//!
//! This crate owns file loading, path validation, YAML parsing, merge semantics,
//! and typed schemas for the sandbox config sections.

pub mod configs;
mod document;
mod error;
mod merge;
mod paths;

use std::path::Path;

pub use document::ConfigDocument;
pub use error::ConfigError;
pub use paths::ConfigPath;

/// Load the single production baseline at `sandbox/config/prd.yml`.
///
/// # Errors
/// Returns an error when the baseline path cannot be resolved, read, or parsed.
pub fn load_prd() -> Result<ConfigDocument, ConfigError> {
    let path = ConfigPath::prd()?;
    ConfigDocument::read(path.as_path())
}

/// Load `prd.yml`, merge one test-local `*.test.yml` override, and return the
/// merged document.
///
/// The path parameter is for test code only; this crate intentionally exposes no
/// CLI or environment variable config path selection.
///
/// # Errors
/// Returns an error when the override path is not a valid sandbox-local
/// `*.test.yml`, when either file cannot be read or parsed, or when merging
/// fails.
pub fn load_test_override(path: impl AsRef<Path>) -> Result<ConfigDocument, ConfigError> {
    let prd = ConfigPath::prd()?;
    let override_path = ConfigPath::test_override(path.as_ref())?;
    let mut baseline = ConfigDocument::read(prd.as_path())?;
    let override_doc = ConfigDocument::read(override_path.as_path())?;
    baseline.merge(override_doc)?;
    Ok(baseline)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::{SystemTime, UNIX_EPOCH};

    use serde::Deserialize;

    use super::*;

    #[test]
    fn load_prd_reads_committed_baseline() {
        let doc = load_prd().expect("prd.yml loads");
        let section = doc
            .section::<serde_yaml::Value>("eos_e2e_test")
            .expect("eos_e2e_test section exists");

        assert!(matches!(section, serde_yaml::Value::Mapping(_)));
    }

    #[test]
    fn merge_recurses_objects_replaces_scalars_and_replaces_arrays() {
        let mut baseline = ConfigDocument::from_yaml_str(
            r#"
daemon:
  command_sessions:
    default_yield_time_ms: 1000
    max_session_s: 21600
  plugin:
    max_response_bytes: 8388608
eos_e2e_test:
  docker:
    cap_add: [SYS_ADMIN, NET_ADMIN]
"#,
        )
        .expect("baseline parses");
        let override_doc = ConfigDocument::from_yaml_str(
            r#"
daemon:
  command_sessions:
    max_session_s: 2
eos_e2e_test:
  docker:
    cap_add: [SYS_PTRACE]
"#,
        )
        .expect("override parses");

        baseline.merge(override_doc).expect("merge succeeds");

        let merged = baseline.into_value();
        assert_eq!(
            merged["daemon"]["command_sessions"]["default_yield_time_ms"],
            serde_yaml::Value::Number(1000.into())
        );
        assert_eq!(
            merged["daemon"]["command_sessions"]["max_session_s"],
            serde_yaml::Value::Number(2.into())
        );
        assert_eq!(
            merged["daemon"]["plugin"]["max_response_bytes"],
            serde_yaml::Value::Number(8_388_608.into())
        );
        assert_eq!(
            merged["eos_e2e_test"]["docker"]["cap_add"],
            serde_yaml::Value::Sequence(vec![serde_yaml::Value::String("SYS_PTRACE".to_owned())])
        );
    }

    #[test]
    fn section_reports_unknown_field_errors() {
        #[derive(Debug, Deserialize)]
        #[serde(deny_unknown_fields)]
        #[allow(dead_code)]
        struct StrictSection {
            expected: u64,
        }

        let doc = ConfigDocument::from_yaml_str(
            r#"
daemon:
  expected: 1
  unexpected: true
"#,
        )
        .expect("document parses");

        let err = doc
            .section::<StrictSection>("daemon")
            .expect_err("unknown field should fail");
        let message = err.to_string();

        assert!(message.contains("daemon"), "{message}");
        assert!(message.contains("unexpected"), "{message}");
    }

    #[test]
    fn section_reports_wrong_type_errors() {
        #[derive(Debug, Deserialize)]
        #[serde(deny_unknown_fields)]
        #[allow(dead_code)]
        struct StrictSection {
            expected: u64,
        }

        let doc = ConfigDocument::from_yaml_str(
            r#"
daemon:
  expected: wrong
"#,
        )
        .expect("document parses");

        let err = doc
            .section::<StrictSection>("daemon")
            .expect_err("wrong field type should fail");
        let message = err.to_string();

        assert!(message.contains("daemon"), "{message}");
        assert!(message.contains("expected"), "{message}");
    }

    #[test]
    fn load_test_override_merges_sandbox_local_test_yaml() {
        let root = test_workspace_dir("load-test-override");
        fs::create_dir_all(&root).expect("create test dir");
        let override_path = root.join("local.test.yml");
        fs::write(
            &override_path,
            r#"
eos_e2e_test:
  pool:
    keep_container: false
    sandboxes: 1
"#,
        )
        .expect("write override");

        let doc = load_test_override(&override_path).expect("override loads");
        let section = doc
            .section::<serde_yaml::Value>("eos_e2e_test")
            .expect("section loads");

        assert_eq!(
            section["pool"]["keep_container"],
            serde_yaml::Value::Bool(false)
        );
        assert_eq!(
            section["pool"]["sandboxes"],
            serde_yaml::Value::Number(1.into())
        );
        assert_eq!(
            section["pool"]["recycle_after"],
            serde_yaml::Value::Number(50.into())
        );
    }

    #[test]
    fn load_test_override_rejects_non_test_yml_path() {
        let root = test_workspace_dir("non-test-yml");
        fs::create_dir_all(&root).expect("create test dir");
        let override_path = root.join("local.yml");
        fs::write(&override_path, "version: 1\n").expect("write override");

        let err = load_test_override(&override_path).expect_err("path suffix should fail");

        assert!(matches!(err, ConfigError::InvalidOverridePath { .. }));
        assert!(err.to_string().contains(".test.yml"));
    }

    #[test]
    fn load_test_override_rejects_path_outside_sandbox_workspace() {
        let override_path = std::env::temp_dir().join(format!(
            "eos-config-outside-{}-{}.test.yml",
            std::process::id(),
            unique_suffix()
        ));
        fs::write(&override_path, "version: 1\n").expect("write override");

        let err = load_test_override(&override_path).expect_err("outside path should fail");

        let _ = fs::remove_file(&override_path);
        assert!(matches!(err, ConfigError::InvalidOverridePath { .. }));
        assert!(err.to_string().contains("inside sandbox workspace"));
    }

    #[cfg(unix)]
    #[test]
    fn load_test_override_rejects_symlink_to_prd_baseline() {
        let root = test_workspace_dir("prd-symlink");
        fs::create_dir_all(&root).expect("create test dir");
        let link_path = root.join("prd-link.test.yml");
        let _ = fs::remove_file(&link_path);
        std::os::unix::fs::symlink(
            ConfigPath::prd().expect("resolve prd config").as_path(),
            &link_path,
        )
        .expect("create prd symlink");

        let err = load_test_override(&link_path).expect_err("prd symlink should fail");

        assert!(matches!(err, ConfigError::InvalidOverridePath { .. }));
        assert!(err.to_string().contains("prd.yml"));
    }

    fn test_workspace_dir(label: &str) -> PathBuf {
        workspace_root()
            .join("target")
            .join("eos-config-tests")
            .join(format!(
                "{label}-{}-{}",
                std::process::id(),
                unique_suffix()
            ))
    }

    fn workspace_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .expect("crate lives below sandbox/crates")
            .to_path_buf()
    }

    fn unique_suffix() -> u128 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock is after unix epoch")
            .as_nanos()
    }
}
