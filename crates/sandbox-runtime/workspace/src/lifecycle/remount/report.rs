use std::path::PathBuf;

use serde_json::Value;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RemountProbe {
    pub path: Option<PathBuf>,
    pub expected_content: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RemountOverlayReport {
    pub mount_verified: bool,
    pub staged_switch: bool,
    pub staging_verified: Option<bool>,
    pub rollback_unmounted: Option<bool>,
    pub rollback_unmount_error: Option<String>,
    pub mount_namespace: Option<String>,
    pub mountinfo_mount_point: Option<String>,
    pub mountinfo_fs_type: Option<String>,
    pub mountinfo_lowerdir_count: Option<usize>,
    pub mountinfo_lowerdir: Option<String>,
    pub mountinfo_lowerdir_expected_count: Option<usize>,
    pub mountinfo_lowerdir_count_matched: Option<bool>,
    pub mountinfo_lowerdir_verified: Option<bool>,
    pub probe_path: Option<String>,
    pub probe_read_ok: Option<bool>,
    pub probe_content_matched: Option<bool>,
    pub probe_error: Option<String>,
}

impl RemountOverlayReport {
    #[must_use]
    pub fn from_payload(payload: &Value) -> Self {
        Self {
            mount_verified: payload
                .get("mount_verified")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            staged_switch: payload
                .get("staged_switch")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            staging_verified: payload.get("staging_verified").and_then(Value::as_bool),
            rollback_unmounted: payload.get("rollback_unmounted").and_then(Value::as_bool),
            rollback_unmount_error: optional_string(payload, "rollback_unmount_error"),
            mount_namespace: optional_string(payload, "mount_namespace"),
            mountinfo_mount_point: optional_string(payload, "mountinfo_mount_point"),
            mountinfo_fs_type: optional_string(payload, "mountinfo_fs_type"),
            mountinfo_lowerdir_count: payload
                .get("mountinfo_lowerdir_count")
                .and_then(Value::as_u64)
                .and_then(|value| usize::try_from(value).ok()),
            mountinfo_lowerdir: optional_string(payload, "mountinfo_lowerdir"),
            mountinfo_lowerdir_expected_count: payload
                .get("mountinfo_lowerdir_expected_count")
                .and_then(Value::as_u64)
                .and_then(|value| usize::try_from(value).ok()),
            mountinfo_lowerdir_count_matched: payload
                .get("mountinfo_lowerdir_count_matched")
                .and_then(Value::as_bool),
            mountinfo_lowerdir_verified: payload
                .get("mountinfo_lowerdir_verified")
                .and_then(Value::as_bool),
            probe_path: optional_string(payload, "probe_path"),
            probe_read_ok: payload.get("probe_read_ok").and_then(Value::as_bool),
            probe_content_matched: payload
                .get("probe_content_matched")
                .and_then(Value::as_bool),
            probe_error: optional_string(payload, "probe_error"),
        }
    }

    #[must_use]
    pub fn failure_summary(&self) -> String {
        if let Some(error) = &self.probe_error {
            return format!("probe failed: {error}");
        }
        if let Some(error) = &self.rollback_unmount_error {
            return format!("rollback cleanup failed: {error}");
        }
        format!(
            "mount_verified={}, staged_switch={}, staging_verified={:?}, rollback_unmounted={:?}, fs_type={:?}, lowerdir_count={:?}, lowerdir_expected_count={:?}, lowerdir_count_matched={:?}, lowerdir_verified={:?}, probe_read_ok={:?}, probe_content_matched={:?}",
            self.mount_verified,
            self.staged_switch,
            self.staging_verified,
            self.rollback_unmounted,
            self.mountinfo_fs_type,
            self.mountinfo_lowerdir_count,
            self.mountinfo_lowerdir_expected_count,
            self.mountinfo_lowerdir_count_matched,
            self.mountinfo_lowerdir_verified,
            self.probe_read_ok,
            self.probe_content_matched
        )
    }
}

fn optional_string(payload: &Value, key: &str) -> Option<String> {
    payload.get(key).and_then(Value::as_str).map(str::to_owned)
}
