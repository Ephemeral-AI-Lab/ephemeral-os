use anyhow::Result;
use eos_protocol::ops;
use serde_json::json;

use crate::common::{as_i64, as_str, live_pool_or_skip, stdout};

#[test]
fn exec_simple() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({"cmd": "true", "yield_time_ms": 1000, "timeout_seconds": 5}),
    )?;
    assert_eq!(as_str(&exec, "status")?, "ok");
    assert_eq!(as_i64(&exec, "exit_code")?, 0);
    assert_eq!(stdout(&exec), "");
    Ok(())
}
