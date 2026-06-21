use sandbox_runtime_workspace::profile::RemountOverlayReport;
use serde_json::json;

#[test]
fn parses_staged_switch_cleanup_telemetry() {
    let report = RemountOverlayReport::from_payload(&json!({
        "mount_verified": true,
        "staged_switch": true,
        "staging_verified": true,
        "rollback_unmounted": true,
        "mountinfo_fs_type": "overlay",
        "mountinfo_lowerdir_count": 2,
        "mountinfo_lowerdir_expected_count": 2,
        "mountinfo_lowerdir_count_matched": true,
        "mountinfo_lowerdir_verified": null,
    }));

    assert!(report.mount_verified);
    assert!(report.staged_switch);
    assert_eq!(report.staging_verified, Some(true));
    assert_eq!(report.rollback_unmounted, Some(true));
    assert_eq!(report.rollback_unmount_error, None);
    assert_eq!(report.mountinfo_lowerdir_expected_count, Some(2));
    assert_eq!(report.mountinfo_lowerdir_count_matched, Some(true));
    assert_eq!(report.mountinfo_lowerdir_verified, None);
}

#[test]
fn failure_summary_prioritizes_rollback_cleanup() {
    let report = RemountOverlayReport::from_payload(&json!({
        "mount_verified": false,
        "staged_switch": false,
        "staging_verified": true,
        "rollback_unmounted": false,
        "rollback_unmount_error": "busy",
    }));

    assert!(report
        .failure_summary()
        .contains("rollback cleanup failed: busy"));
}
