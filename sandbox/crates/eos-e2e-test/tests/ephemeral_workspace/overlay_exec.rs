use anyhow::Result;
use eos_protocol::ops;
use serde_json::json;

use crate::common::{array, as_i64, as_str, live_pool_or_skip};

#[test]
fn exec_overlay_mount_publishes_changed_paths() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "mkdir -p overlay && printf from-overlay > overlay/exec.txt",
            "yield_time_ms": 1000,
            "timeout_seconds": 10,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "ok");
    assert_eq!(as_i64(&exec, "exit_code")?, 0);
    assert!(
        array(&exec, "changed_paths")?
            .iter()
            .any(|path| path.as_str() == Some("overlay/exec.txt")),
        "exec overlay should publish captured upperdir paths: {exec}"
    );
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "overlay/exec.txt"}))?;
    assert_eq!(as_str(&read, "content")?, "from-overlay");
    Ok(())
}
