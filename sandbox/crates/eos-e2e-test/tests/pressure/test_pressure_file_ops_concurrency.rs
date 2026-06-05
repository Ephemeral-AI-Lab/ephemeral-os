use std::sync::{Arc, Barrier};
use std::thread;

use anyhow::Result;
use eos_e2e_test::next_invocation_id;
use eos_protocol::ops;
use serde_json::json;

use crate::helpers::{pressure_levels, request_with_identity};
use crate::support::{as_bool, as_i64, as_str, live_pool_or_skip, wait_for_active_leases};

#[test]
fn n_concurrent_mixed_ops() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "pressure/mixed-seed.txt", "content": "needle\n", "overwrite": true}),
    )?;
    let barrier = Arc::new(Barrier::new(10));
    let handles: Vec<_> = (0..10)
        .map(|index| {
            let client = lease.client().clone();
            let root = lease.root().to_owned();
            let caller_id = lease.caller_id().to_owned();
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait();
                let args = match index % 3 {
                    0 => json!({
                        "layer_stack_root": root,
                        "caller_id": caller_id,
                        "path": format!("pressure/mixed-{index}.txt"),
                        "content": format!("mixed-{index}\n"),
                        "overwrite": true
                    }),
                    1 => json!({
                        "layer_stack_root": root,
                        "caller_id": caller_id,
                        "path": "pressure/mixed-seed.txt"
                    }),
                    _ => json!({
                        "layer_stack_root": root,
                        "caller_id": caller_id,
                        "cmd": "printf pressure",
                        "yield_time_ms": 1000,
                        "timeout_seconds": 10
                    }),
                };
                let op = match index % 3 {
                    0 => ops::API_V1_WRITE_FILE,
                    1 => ops::API_V1_READ_FILE,
                    _ => ops::API_V1_EXEC_COMMAND,
                };
                client.request(op, &next_invocation_id(), &args)
            })
        })
        .collect();
    for handle in handles {
        let response = handle.join().expect("mixed op thread panicked")?;
        assert!(
            as_bool(&response, "success").unwrap_or(false) || response.get("error").is_some(),
            "mixed pressure op should return a structured payload: {response}"
        );
    }
    let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    assert_eq!(
        as_i64(&metrics, "active_leases")?,
        0,
        "mixed ops should not leak leases: {metrics}"
    );
    Ok(())
}

#[test]
fn file_ops_ladder_1_3_6_12() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let levels = pressure_levels(&pool)?;
    let lease = pool.acquire()?;

    for level in levels {
        let barrier = Arc::new(Barrier::new(level));
        let handles: Vec<_> = (0..level)
            .map(|index| {
                let client = lease.client().clone();
                let root = lease.root().to_owned();
                let caller_id = lease.caller_id().to_owned();
                let barrier = Arc::clone(&barrier);
                thread::spawn(move || {
                    barrier.wait();
                    request_with_identity(
                        &client,
                        ops::API_V1_WRITE_FILE,
                        &root,
                        &caller_id,
                        json!({
                            "path": format!("pressure/ladder/file/level-{level}/item-{index}.txt"),
                            "content": format!("file-level-{level}-item-{index}\n"),
                            "overwrite": true
                        }),
                    )
                })
            })
            .collect();

        for handle in handles {
            let response = handle.join().expect("file writer thread panicked")?;
            assert!(
                as_bool(&response, "success")?,
                "file ladder write should commit at level {level}: {response}"
            );
        }

        let barrier = Arc::new(Barrier::new(level));
        let handles: Vec<_> = (0..level)
            .map(|index| {
                let client = lease.client().clone();
                let root = lease.root().to_owned();
                let caller_id = lease.caller_id().to_owned();
                let barrier = Arc::clone(&barrier);
                thread::spawn(move || {
                    barrier.wait();
                    request_with_identity(
                        &client,
                        ops::API_V1_READ_FILE,
                        &root,
                        &caller_id,
                        json!({
                            "path": format!("pressure/ladder/file/level-{level}/item-{index}.txt")
                        }),
                    )
                })
            })
            .collect();

        for (index, handle) in handles.into_iter().enumerate() {
            let response = handle.join().expect("file reader thread panicked")?;
            assert_eq!(
                as_str(&response, "content")?,
                format!("file-level-{level}-item-{index}\n"),
                "file ladder readback should match at level {level}: {response}"
            );
        }
        let metrics = wait_for_active_leases(&lease, 0)?;
        assert_eq!(
            as_i64(&metrics, "active_leases")?,
            0,
            "file ladder should not leak leases at level {level}: {metrics}"
        );
    }
    Ok(())
}

#[test]
fn write_storm_squash_under_load() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    for version in 0..115 {
        lease.call_ok(
            ops::API_V1_WRITE_FILE,
            json!({
                "path": "pressure/storm.txt",
                "content": format!("storm-{version}\n"),
                "overwrite": true
            }),
        )?;
        if version % 20 == 0 {
            let read =
                lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "pressure/storm.txt"}))?;
            assert!(as_str(&read, "content")?.contains("storm"));
        }
    }
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "pressure/storm.txt"}))?;
    assert_eq!(as_str(&read, "content")?, "storm-114\n");
    let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    assert!(
        as_i64(&metrics, "manifest_depth")? <= 100,
        "write storm should remain within auto-squash depth target: {metrics}"
    );
    Ok(())
}
