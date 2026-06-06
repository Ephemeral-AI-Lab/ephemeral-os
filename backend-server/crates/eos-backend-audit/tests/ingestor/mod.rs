//! Unit tests for the audit-cursor epoch math (`next_cursor`), reached through a
//! `#[path]` include from `ingestor.rs` so the private fn stays crate-internal.

use eos_backend_types::AuditCursor;
use eos_types::{SandboxId, UtcDateTime};

use crate::{SandboxAuditLoss, SandboxPullBatch};

use super::next_cursor;

fn prior(last_seq: i64) -> AuditCursor {
    AuditCursor {
        sandbox_id: SandboxId::new_v4(),
        last_seq,
        boot_epoch_id: 1,
        lost_before_seq: None,
        dropped_count: 0,
        updated_at: UtcDateTime::now(),
    }
}

fn batch(cursor_after_seq: Option<i64>, lost_before_seq: Option<i64>) -> SandboxPullBatch {
    SandboxPullBatch {
        rows: Vec::new(),
        loss: SandboxAuditLoss {
            cursor_after_seq,
            lost_before_seq,
            dropped_event_count: None,
        },
    }
}

#[test]
fn epoch_reset_with_zero_boundary_records_no_loss() {
    // A reboot where the prior epoch consumed nothing (`last_seq == 0`) and the new
    // pull reports no ring loss must NOT fabricate a `lost_before_seq = Some(0)`
    // boundary (M3): `has_counted_loss` (`seq > 0`) and `audit_sandboxes_with_loss`
    // must agree that this row carries no loss.
    let cursor = next_cursor(&SandboxId::new_v4(), 2, true, Some(&prior(0)), &batch(None, None));
    assert_eq!(cursor.lost_before_seq, None);
    assert_eq!(cursor.last_seq, 0);
    assert_eq!(cursor.boot_epoch_id, 2);
}

#[test]
fn epoch_reset_after_real_progress_records_prior_high_water() {
    // A reboot after genuine progress still records the prior high-water as lost and
    // resets `last_seq` into the new epoch's space.
    let cursor = next_cursor(&SandboxId::new_v4(), 2, true, Some(&prior(7)), &batch(Some(3), None));
    assert_eq!(cursor.lost_before_seq, Some(7));
    assert_eq!(cursor.last_seq, 3);
}
