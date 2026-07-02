//! The C5 failure table as a pure function of the runner's two booleans plus
//! report presence (test 19's daemon-side half), including the
//! missing-report mount-id comparison.

use sandbox_runtime_workspace::{classify_remount_report, ReportClassification};
use serde_json::{json, Value};

fn report(first: bool, verified: bool, detail: &str) -> Value {
    json!({
        "first_move_succeeded": first,
        "mount_verified": verified,
        "detail": detail,
    })
}

#[test]
fn pre_ponr_reports_are_clean_skips_with_their_detail() {
    for detail in [
        "stage_failed:staging_mount:28",
        "stage_failed:mask_restore_failed:eos",
        "stage_failed:staged_probe_mismatch:fstype:0x1021994",
        "move_failed:first_move:22",
    ] {
        let payload = report(false, false, detail);
        assert_eq!(
            classify_remount_report(Some(&payload), 135, None),
            ReportClassification::CleanSkip {
                reason: detail.to_owned()
            },
            "a failed first move or earlier is always a clean skip"
        );
    }
}

#[test]
fn verified_switch_migrates_and_ebusy_parks() {
    let payload = report(true, true, "switched");
    assert_eq!(
        classify_remount_report(Some(&payload), 135, None),
        ReportClassification::Verified {
            parked_reason: None
        }
    );

    let payload = report(true, true, "pinned:rollback_unmount_busy");
    assert_eq!(
        classify_remount_report(Some(&payload), 135, None),
        ReportClassification::Verified {
            parked_reason: Some("pinned:rollback_unmount_busy".to_owned())
        }
    );
}

#[test]
fn post_ponr_failures_are_faulty() {
    for (first, verified, detail) in [
        (true, false, "mount_uncertain:second_move:22"),
        (true, false, "mount_uncertain:visible_probe:readdir"),
        (true, true, "rollback_unmount_failed:22"),
    ] {
        let payload = report(first, verified, detail);
        assert_eq!(
            classify_remount_report(Some(&payload), 135, None),
            ReportClassification::Faulty {
                class_detail: detail.to_owned()
            },
            "any post-PONR outcome other than switched/park is faulty"
        );
    }
}

#[test]
fn missing_report_classifies_by_workspace_mount_id() {
    // Runner died before any move: the workspace row still carries the
    // quiesce-time mount id -> provably pre-PONR -> clean skip (E8 ii).
    assert_eq!(
        classify_remount_report(None, 135, Some(Some(135))),
        ReportClassification::CleanSkip {
            reason: "stage_failed:runner_died_before_switch".to_owned()
        }
    );
    // Killed between the moves: the workspace row is gone (X6.4).
    assert_eq!(
        classify_remount_report(None, 135, Some(None)),
        ReportClassification::Faulty {
            class_detail: "mount_uncertain:runner_report_missing".to_owned()
        }
    );
    // Killed after the switch: the id changed (X6.4).
    assert_eq!(
        classify_remount_report(None, 135, Some(Some(141))),
        ReportClassification::Faulty {
            class_detail: "mount_uncertain:runner_report_missing".to_owned()
        }
    );
    // A payload without the two booleans is a missing report, not a policy
    // input (the engine synthesizes {"status":"error"} for dead runners).
    let synthesized = json!({"status": "error"});
    assert_eq!(
        classify_remount_report(Some(&synthesized), 135, Some(Some(135))),
        ReportClassification::CleanSkip {
            reason: "stage_failed:runner_died_before_switch".to_owned()
        }
    );
}
