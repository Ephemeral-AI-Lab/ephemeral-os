//! `api.audit.pull` cursor pagination plus the config-gated reset hook.

use anyhow::{Context, Result};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{as_i64, live_pool_or_skip};

#[test]
fn audit_pull_paginates_and_reset_floor_is_enabled_by_config() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let baseline = drain_audit_to_end(&lease)?;

    for index in 0..3 {
        lease.call_ok(
            ops::API_V1_WRITE_FILE,
            json!({
                "path": format!("daemon/audit/page-{index}.txt"),
                "content": format!("audit event {index}\n"),
                "overwrite": true
            }),
        )?;
    }

    let first_page = pull_after(&lease, baseline, 1)?;
    let first_events = events(&first_page)?;
    assert_eq!(
        first_events.len(),
        1,
        "limit=1 should return one audit event: {first_page}"
    );
    let first_cursor = cursor_after_seq(&first_page)?;
    assert!(
        first_cursor > baseline,
        "first page must advance cursor: baseline={baseline} first={first_page}"
    );

    let second_page = pull_after(&lease, first_cursor, 1)?;
    let second_events = events(&second_page)?;
    assert_eq!(
        second_events.len(),
        1,
        "second page should return the next event only: {second_page}"
    );
    let second_cursor = cursor_after_seq(&second_page)?;
    assert!(
        second_cursor > first_cursor,
        "second page must advance past first page: first={first_page} second={second_page}"
    );
    assert!(
        second_events[0]["seq"]
            .as_i64()
            .is_some_and(|seq| seq > first_cursor),
        "second page must filter strictly after first cursor: {second_page}"
    );

    let future_cursor = second_cursor + 1_000_000;
    let empty = pull_after(&lease, future_cursor, 1)?;
    assert!(
        events(&empty)?.is_empty(),
        "future cursor should return no events: {empty}"
    );
    assert_eq!(
        cursor_after_seq(&empty)?,
        future_cursor,
        "empty pull preserves the requested cursor floor: {empty}"
    );

    let reset = lease.call_ok(ops::API_AUDIT_RESET_FLOOR, json!({}))?;
    assert_eq!(
        reset["reset"], true,
        "daemon test config enables reset: {reset}"
    );
    Ok(())
}

fn drain_audit_to_end(lease: &eos_e2e_test::NodeLease<'_>) -> Result<i64> {
    let mut cursor = -1;
    loop {
        let batch = pull_after(lease, cursor, 128)?;
        cursor = cursor_after_seq(&batch)?;
        if events(&batch)?.len() < 128 {
            return Ok(cursor);
        }
    }
}

fn pull_after(lease: &eos_e2e_test::NodeLease<'_>, after_seq: i64, limit: u64) -> Result<Value> {
    lease.call_ok(
        ops::API_AUDIT_PULL,
        json!({"after_seq": after_seq, "limit": limit}),
    )
}

fn cursor_after_seq(value: &Value) -> Result<i64> {
    value
        .get("cursor")
        .with_context(|| format!("cursor missing in {value}"))
        .and_then(|cursor| as_i64(cursor, "after_seq"))
}

fn events(value: &Value) -> Result<&Vec<Value>> {
    value
        .get("events")
        .and_then(Value::as_array)
        .with_context(|| format!("events array missing in {value}"))
}
