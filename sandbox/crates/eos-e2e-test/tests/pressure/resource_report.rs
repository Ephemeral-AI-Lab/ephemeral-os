use std::fs;

use anyhow::{ensure, Context, Result};
use eos_e2e_test::unique_suffix;
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::helpers::{pressure_levels, workload_timeout_s};
use crate::support::{
    as_bool, as_i64, as_str, live_pool_or_skip, wait_for_active_leases, wait_for_session_count,
};

#[test]
fn resource_report_smoke() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let levels = pressure_levels(&pool)?;
    let workload = pool.workload().clone();
    let timeout_s = workload_timeout_s(&pool);
    let lease = pool.acquire()?;
    let mut samples = Vec::with_capacity(workload.sample_count);

    for sample in 0..workload.sample_count {
        let path = format!("pressure/resource/sample-{sample}.txt");
        let write = lease.call_ok(
            ops::API_V1_WRITE_FILE,
            json!({
                "path": path,
                "content": format!("resource-sample-{sample}\n"),
                "overwrite": true
            }),
        )?;
        let read = lease.call_ok(
            ops::API_V1_READ_FILE,
            json!({"path": format!("pressure/resource/sample-{sample}.txt")}),
        )?;
        assert_eq!(
            as_str(&read, "content")?,
            format!("resource-sample-{sample}\n"),
            "resource report readback should match: {read}"
        );

        let exec = lease.call_ok(
            ops::API_V1_EXEC_COMMAND,
            json!({
                "cmd": format!("mkdir -p pressure/resource && printf exec-{sample} > pressure/resource/exec-{sample}.txt"),
                "yield_time_ms": 1000,
                "timeout_seconds": timeout_s,
                "max_output_tokens": 2000
            }),
        )?;
        assert_eq!(as_str(&exec, "status")?, "ok", "{exec}");
        assert_eq!(as_i64(&exec, "exit_code")?, 0, "{exec}");
        ensure_timing(&exec, "runtime.dispatch_s")?;
        ensure_timing(&exec, "resource.command_exec.upperdir_tree_bytes")?;
        ensure_timing(&write, "runtime.dispatch_s")?;
        ensure_timing(&read, "runtime.dispatch_s")?;

        let session = lease.call_ok(
            ops::API_V1_EXEC_COMMAND,
            json!({
                "cmd": format!("sh -c 'echo resource-report-{sample}; sleep 60'"),
                "yield_time_ms": 100,
                "timeout_seconds": timeout_s,
                "max_output_tokens": 500
            }),
        )?;
        assert_eq!(as_str(&session, "status")?, "running", "{session}");
        let cancel = lease.call_ok(
            ops::API_V1_COMMAND_CANCEL,
            json!({"command_session_id": as_str(&session, "command_session_id")?}),
        )?;
        assert!(
            matches!(as_str(&cancel, "status")?, "cancelled" | "ok" | "error"),
            "resource report cancel should return structured status: {cancel}"
        );
        wait_for_session_count(&lease, 0)?;
        let metrics = wait_for_active_leases(&lease, 0)?;
        let session_count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;

        samples.push(json!({
            "sample": sample,
            "write_timing_keys": timing_keys(&write),
            "read_timing_keys": timing_keys(&read),
            "exec_timing_keys": timing_keys(&exec),
            "command_status": as_str(&session, "status")?,
            "cancel_status": as_str(&cancel, "status")?,
            "metrics": metrics,
            "session_count": session_count,
        }));
    }

    let final_metrics = wait_for_active_leases(&lease, 0)?;
    let final_session_count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;
    let ready = lease.call_ok(ops::API_RUNTIME_READY, json!({}))?;
    assert!(as_bool(&ready, "ready")?, "{ready}");
    let plugin_status = lease.call_ok(ops::API_PLUGIN_STATUS, json!({}))?;
    let isolated_open = lease.call_ok(ops::API_ISOLATED_WORKSPACE_LIST_OPEN, json!({}))?;

    let report = json!({
        "schema_version": 1,
        "module": "pressure",
        "scenario": "resource_report_smoke",
        "workload": {
            "concurrency_levels": levels,
            "write_iterations": workload.write_iterations,
            "sample_count": workload.sample_count,
            "timeout_s": timeout_s,
        },
        "samples": samples,
        "leak_counters": {
            "active_leases": as_i64(&final_metrics, "active_leases")?,
            "command_session_count": as_i64(&final_session_count, "count")?,
            "open_isolated_callers": isolated_open
                .get("open_caller_ids")
                .and_then(Value::as_array)
                .map_or(0, Vec::len),
        },
        "final_metrics": final_metrics,
        "runtime_ready": ready,
        "plugin_status": plugin_status,
        "isolated_open": isolated_open,
    });

    ensure!(
        report["samples"]
            .as_array()
            .is_some_and(|samples| !samples.is_empty()),
        "resource report should include samples: {report}"
    );
    ensure_eq_zero(&report, "active_leases")?;
    ensure_eq_zero(&report, "command_session_count")?;

    let artifact_dir = workload.perf_artifact_dir;
    fs::create_dir_all(&artifact_dir)
        .with_context(|| format!("create perf artifact dir {}", artifact_dir.display()))?;
    let artifact = artifact_dir.join(format!(
        "pressure-resource-report-{}.json",
        unique_suffix().replace('-', "_")
    ));
    fs::write(&artifact, serde_json::to_vec_pretty(&report)?)
        .with_context(|| format!("write {}", artifact.display()))?;
    let parsed: Value = serde_json::from_slice(
        &fs::read(&artifact).with_context(|| format!("read {}", artifact.display()))?,
    )?;
    assert_eq!(parsed["scenario"], "resource_report_smoke");
    Ok(())
}

fn ensure_timing(response: &Value, key: &str) -> Result<()> {
    ensure!(
        response
            .get("timings")
            .and_then(Value::as_object)
            .is_some_and(|timings| timings.contains_key(key)),
        "response timings should include {key}: {response}"
    );
    Ok(())
}

fn timing_keys(response: &Value) -> Vec<String> {
    let mut keys = response
        .get("timings")
        .and_then(Value::as_object)
        .map(|timings| timings.keys().cloned().collect::<Vec<_>>())
        .unwrap_or_default();
    keys.sort();
    keys
}

fn ensure_eq_zero(report: &Value, key: &str) -> Result<()> {
    let value = report
        .get("leak_counters")
        .and_then(|counters| counters.get(key))
        .and_then(Value::as_i64)
        .with_context(|| format!("leak_counters.{key} missing in report"))?;
    ensure!(value == 0, "leak_counters.{key} should be zero: {report}");
    Ok(())
}
