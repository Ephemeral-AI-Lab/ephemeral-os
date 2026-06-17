use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::request::{require_caller_id, ArgProblem, ArgsError};
use crate::CallerId;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IsolationEnterInput {
    pub caller: CallerId,
    pub root: WorkspaceRootInput,
}

impl IsolationEnterInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            caller: require_caller_id(args)?,
            root: WorkspaceRootInput::parse(args)?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum WorkspaceRootInput {
    WorkspaceRoot(PathBuf),
    LegacyLayerStackRoot(PathBuf),
}

impl WorkspaceRootInput {
    fn parse(args: &Value) -> Result<Self, ArgsError> {
        match (
            args.get("workspace_root").is_some(),
            args.get("layer_stack_root").is_some(),
        ) {
            (true, false) => Ok(Self::WorkspaceRoot(require_root_path(args, "workspace_root")?)),
            (false, true) => Ok(Self::LegacyLayerStackRoot(require_root_path(
                args,
                "layer_stack_root",
            )?)),
            (true, true) => Err(ArgsError {
                key: "workspace_root",
                problem: ArgProblem::Invalid(
                    "workspace_root and legacy layer_stack_root are ambiguous; pass only workspace_root"
                        .to_owned(),
                ),
            }),
            (false, false) => Err(ArgsError {
                key: "workspace_root",
                problem: ArgProblem::Required,
            }),
        }
    }
}

fn require_root_path(args: &Value, key: &'static str) -> Result<PathBuf, ArgsError> {
    let Some(raw) = args.get(key).and_then(Value::as_str) else {
        return Err(ArgsError {
            key,
            problem: ArgProblem::MustBeString,
        });
    };
    let path = raw.trim();
    if path.is_empty() {
        return Err(ArgsError {
            key,
            problem: ArgProblem::MustBeNonEmpty,
        });
    }
    Ok(PathBuf::from(path))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IsolationExitInput {
    pub caller: CallerId,
    pub grace_s: Option<f64>,
}

impl IsolationExitInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            caller: require_caller_id(args)?,
            grace_s: args.get("grace_s").and_then(Value::as_f64),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IsolationStatusInput {
    pub caller: CallerId,
}

impl IsolationStatusInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            caller: require_caller_id(args)?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum IsolationTestRemountFault {
    ProcessMembershipChanged,
    MountinfoMismatch,
}

impl IsolationTestRemountFault {
    #[must_use]
    pub const fn reason(&self) -> &'static str {
        match self {
            Self::ProcessMembershipChanged => "process_membership_changed",
            Self::MountinfoMismatch => "mountinfo_mismatch",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "process_membership_changed" => Some(Self::ProcessMembershipChanged),
            "mountinfo_mismatch" => Some(Self::MountinfoMismatch),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IsolationTestCompactRemountInput {
    pub caller: CallerId,
    pub root: WorkspaceRootInput,
    pub probe_path: Option<PathBuf>,
    pub probe_content: Option<String>,
    pub test_force_block_reason: Option<IsolationTestRemountFault>,
}

impl IsolationTestCompactRemountInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            caller: require_caller_id(args)?,
            root: WorkspaceRootInput::parse(args)?,
            probe_path: args
                .get("probe_path")
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .map(PathBuf::from),
            probe_content: args
                .get("probe_content")
                .and_then(Value::as_str)
                .map(str::to_owned),
            test_force_block_reason: parse_test_force_block_reason(args)?,
        })
    }
}

fn parse_test_force_block_reason(
    args: &Value,
) -> Result<Option<IsolationTestRemountFault>, ArgsError> {
    let Some(value) = args.get("test_force_block_reason") else {
        return Ok(None);
    };
    let Some(raw) = value.as_str() else {
        return Err(ArgsError {
            key: "test_force_block_reason",
            problem: ArgProblem::MustBeString,
        });
    };
    IsolationTestRemountFault::parse(raw)
        .map(Some)
        .ok_or_else(|| ArgsError {
            key: "test_force_block_reason",
            problem: ArgProblem::Invalid(
                "test_force_block_reason must be process_membership_changed or mountinfo_mismatch"
                    .to_owned(),
            ),
        })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IsolationEnterOutput {
    pub success: bool,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_handle_id: String,
    pub workspace_root: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IsolationExitOutput {
    pub success: bool,
    pub evicted_upperdir_bytes: u64,
    pub lifetime_s: f64,
    pub total_ms: f64,
    pub phases_ms: Value,
    pub inspection: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum IsolationStatusOutput {
    Open {
        success: bool,
        open: bool,
        manifest_version: i64,
        manifest_root_hash: String,
        workspace_root: String,
        created_at: f64,
        last_activity: f64,
    },
    Closed {
        success: bool,
        open: bool,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ListOpenOutput {
    pub success: bool,
    pub open_caller_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TestResetOutput {
    pub success: bool,
    pub reset: bool,
    pub exited_callers: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TestCompactRemountOutput {
    pub success: bool,
    pub before_manifest_depth: usize,
    pub before_layer_dirs: usize,
    pub before_storage_bytes: u64,
    pub compacted_snapshot_layers: usize,
    pub remounted_layer_count: usize,
    pub live_remount: bool,
    pub mount_verified: bool,
    pub remount_staged_switch: bool,
    pub remount_staging_verified: Option<bool>,
    pub remount_rollback_unmounted: Option<bool>,
    pub remount_rollback_unmount_error: Option<String>,
    pub remount_mount_namespace: Option<String>,
    pub remount_mountinfo_fs_type: Option<String>,
    pub remount_mountinfo_lowerdir_count: Option<usize>,
    pub remount_mountinfo_lowerdir_expected_count: Option<usize>,
    pub remount_mountinfo_lowerdir_count_matched: Option<bool>,
    pub remount_mountinfo_lowerdir_verified: Option<bool>,
    pub remount_probe_read_ok: Option<bool>,
    pub remount_probe_content_matched: Option<bool>,
    pub remount_probe_error: Option<String>,
    pub lease_retargeted: bool,
    pub remountable_commands: usize,
    pub process_count: usize,
    pub quiesced_process_count: usize,
    pub pinned_cwd_count: usize,
    pub pinned_root_count: usize,
    pub pinned_fd_count: usize,
    pub pinned_mapped_file_count: usize,
    pub mountinfo_checked_count: usize,
    pub process_resumed: bool,
    pub squash_manifest_version: Option<i64>,
    pub squash_lease_release_error: Option<String>,
    pub after_manifest_depth: usize,
    pub after_layer_dirs: usize,
    pub after_storage_bytes: u64,
    pub active_leases_after: usize,
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{
        IsolationEnterInput, IsolationTestCompactRemountInput, IsolationTestRemountFault,
        WorkspaceRootInput,
    };

    #[test]
    fn enter_parses_workspace_root() {
        let input = IsolationEnterInput::parse(&json!({
            "caller_id": "caller",
            "workspace_root": "/workspace",
        }))
        .expect("input should parse");

        assert_eq!(
            input.root,
            WorkspaceRootInput::WorkspaceRoot("/workspace".into())
        );
    }

    #[test]
    fn enter_parses_legacy_layer_stack_root() {
        let input = IsolationEnterInput::parse(&json!({
            "caller_id": "caller",
            "layer_stack_root": "/tmp/stack",
        }))
        .expect("legacy input should parse");

        assert_eq!(
            input.root,
            WorkspaceRootInput::LegacyLayerStackRoot("/tmp/stack".into())
        );
    }

    #[test]
    fn enter_rejects_ambiguous_roots() {
        let error = IsolationEnterInput::parse(&json!({
            "caller_id": "caller",
            "workspace_root": "/workspace",
            "layer_stack_root": "/tmp/stack",
        }))
        .expect_err("ambiguous roots should fail");

        assert_eq!(error.key, "workspace_root");
        assert!(error.message().contains("ambiguous"));
    }

    #[test]
    fn enter_rejects_malformed_dual_roots_as_ambiguous() {
        let error = IsolationEnterInput::parse(&json!({
            "caller_id": "caller",
            "workspace_root": 42,
            "layer_stack_root": "/tmp/stack",
        }))
        .expect_err("dual root presence should fail before legacy fallback");

        assert_eq!(error.key, "workspace_root");
        assert!(error.message().contains("ambiguous"));
    }

    #[test]
    fn enter_rejects_malformed_workspace_root_without_legacy_fallback() {
        let error = IsolationEnterInput::parse(&json!({
            "caller_id": "caller",
            "workspace_root": 42,
        }))
        .expect_err("workspace root must be a string");

        assert_eq!(error.key, "workspace_root");
        assert!(error.message().contains("must be a string"));
    }

    #[test]
    fn enter_rejects_blank_legacy_layer_stack_root() {
        let error = IsolationEnterInput::parse(&json!({
            "caller_id": "caller",
            "layer_stack_root": "   ",
        }))
        .expect_err("legacy layer stack root must be non-empty");

        assert_eq!(error.key, "layer_stack_root");
        assert!(error.message().contains("must be non-empty"));
    }

    #[test]
    fn parses_test_force_block_reason() {
        let input = IsolationTestCompactRemountInput::parse(&json!({
            "caller_id": "caller",
            "layer_stack_root": "/tmp/stack",
            "test_force_block_reason": "process_membership_changed",
        }))
        .expect("input should parse");

        assert_eq!(
            input.test_force_block_reason,
            Some(IsolationTestRemountFault::ProcessMembershipChanged)
        );
    }

    #[test]
    fn compact_remount_parses_workspace_root() {
        let input = IsolationTestCompactRemountInput::parse(&json!({
            "caller_id": "caller",
            "workspace_root": "/workspace",
        }))
        .expect("input should parse");

        assert_eq!(
            input.root,
            WorkspaceRootInput::WorkspaceRoot("/workspace".into())
        );
    }

    #[test]
    fn rejects_unknown_test_force_block_reason() {
        let error = IsolationTestCompactRemountInput::parse(&json!({
            "caller_id": "caller",
            "layer_stack_root": "/tmp/stack",
            "test_force_block_reason": "fd_pinned_workspace",
        }))
        .expect_err("unknown force reason should fail");

        assert_eq!(error.key, "test_force_block_reason");
        assert!(error
            .message()
            .contains("process_membership_changed or mountinfo_mismatch"));
    }
}
