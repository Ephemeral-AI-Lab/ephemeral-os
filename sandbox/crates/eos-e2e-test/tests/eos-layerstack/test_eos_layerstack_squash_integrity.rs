use anyhow::Result;
use eos_daemon::wire::ops;
use serde_json::{json, Value};

use crate::support::{as_i64, as_str, live_pool_or_skip};

fn grow_past_auto_squash(lease: &eos_e2e_test::NodeLease<'_>, path: &str) -> Result<Value> {
    let mut last = Value::Null;
    for version in 0..105 {
        last = lease.call_ok(
            ops::API_V1_WRITE_FILE,
            json!({"path": path, "content": format!("version-{version}\n"), "overwrite": true}),
        )?;
    }
    Ok(last)
}

#[test]
fn auto_squash_triggers_past_depth() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    grow_past_auto_squash(&lease, "squash/depth.txt")?;
    // 105 publishes against the suite's `auto_squash_max_depth: 8` end shallow
    // only if auto-squash kept folding the stack into checkpoint layers.
    let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    let depth = as_i64(&metrics, "manifest_depth")?;
    assert!(
        depth <= 8,
        "auto-squash should keep depth bounded: {metrics}"
    );
    assert_eq!(
        as_i64(&metrics, "referenced_layers")?,
        depth,
        "the active manifest should reference exactly the folded layers: {metrics}"
    );
    Ok(())
}

#[test]
fn head_readable_after_squash() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    grow_past_auto_squash(&lease, "squash/head.txt")?;
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "squash/head.txt"}))?;
    assert_eq!(as_str(&read, "content")?, "version-104\n");
    Ok(())
}

