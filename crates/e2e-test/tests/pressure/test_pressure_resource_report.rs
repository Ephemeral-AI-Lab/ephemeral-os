use std::fs;
use std::time::{Duration, Instant};

use anyhow::{ensure, Context, Result};
use e2e_test::unique_suffix;
use protocol::catalog;
use serde_json::{json, Value};
use trace::ResourceStatsKind;

use crate::helpers::{
    ensure_response_step, ensure_trace_resource, finalize_foreground_command_wire, pressure_levels,
    response_result, trace_resource_number_or_truncated, workload_timeout_s,
};
use crate::support::{
    as_bool, as_i64, as_str, live_pool_or_skip, seed_base_files, wait_for_active_leases,
    wait_for_command_count,
};

const DAEMON_RESOURCE_CEILING_BYTES: f64 = 8.0 * 1024.0 * 1024.0 * 1024.0;

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
    let mut untruncated_memory_samples = 0usize;
    let mut untruncated_rss_samples = 0usize;
    let mut untruncated_max_rss_samples = 0usize;

    for sample in 0..workload.sample_count {
        let path = format!("pressure/resource/sample-{sample}.txt");
        let write_wire = lease.call(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": path,
                "content": format!("resource-sample-{sample}\n"),
                "overwrite": true
            }),
        )?;
        let write = response_result(&write_wire)?.clone();
        let read_wire = lease.call(
            catalog::SANDBOX_FILE_READ,
            json!({"path": format!("pressure/resource/sample-{sample}.txt")}),
        )?;
        let read = response_result(&read_wire)?.clone();
        assert_eq!(
            as_str(&read, "content")?,
            format!("resource-sample-{sample}\n"),
            "resource report readback should match: {read}"
        );

        let exec_wire = lease.call(
            catalog::SANDBOX_COMMAND_EXEC,
            json!({
                "cmd": format!("mkdir -p pressure/resource && printf exec-{sample} > pressure/resource/exec-{sample}.txt"),
                "yield_time_ms": 8000,
                "timeout_seconds": timeout_s,}),
        )?;
        let exec_completed_in_exec_response =
            as_str(response_result(&exec_wire)?, "status")? != "running";
        // The command can outlast its yield under emulation and return status
        // "running". In that case finalization arrives through command.poll, whose
        // response sidecar belongs to the poll op rather than the original exec op.
        let (exec_wire, exec) = finalize_foreground_command_wire(
            &lease,
            exec_wire,
            Instant::now() + Duration::from_secs(timeout_s + 5),
        )?;
        assert_eq!(as_str(&exec, "status")?, "ok", "{exec}");
        assert_eq!(as_i64(&exec, "exit_code")?, 0, "{exec}");
        ensure_response_step(&exec_wire, "dispatch")?;
        let exec_trace_resources = if exec_completed_in_exec_response {
            ensure_trace_resource(
                &exec_wire,
                ResourceStatsKind::Tree,
                "resource.command_exec.upperdir",
            )?;
            ensure_trace_resource(
                &exec_wire,
                ResourceStatsKind::Tree,
                "resource.command_exec.run_dir",
            )?;
            vec![
                "resource.command_exec.upperdir",
                "resource.command_exec.run_dir",
            ]
        } else {
            Vec::new()
        };
        ensure_response_step(&write_wire, "dispatch")?;
        ensure_response_step(&read_wire, "dispatch")?;

        // Memory gauges are collected per op via the cgroup/process collector.
        // They are gauges inflated by page cache on lowerdir reads, so assert
        // sane absolute bounds rather than delta-proportional growth.
        let memory_current = trace_resource_number_or_truncated(
            &write_wire,
            ResourceStatsKind::CgroupProcess,
            "daemon.response_timings",
            &["cgroup", "memory", "current_bytes"],
        )?;
        if let Some(memory_current) = memory_current {
            ensure_bounded_memory_gauge("cgroup memory.current", memory_current, &write_wire)?;
            untruncated_memory_samples += 1;
        }
        let rss = trace_resource_number_or_truncated(
            &write_wire,
            ResourceStatsKind::CgroupProcess,
            "daemon.response_timings",
            &["process", "gauges", "rss_bytes"],
        )?;
        if let Some(rss) = rss {
            ensure_bounded_memory_gauge("process RSS", rss, &write_wire)?;
            untruncated_rss_samples += 1;
        }
        let max_rss = trace_resource_number_or_truncated(
            &write_wire,
            ResourceStatsKind::CgroupProcess,
            "daemon.response_timings",
            &["process", "gauges", "max_rss_bytes"],
        )?;
        if let Some(max_rss) = max_rss {
            ensure_bounded_memory_gauge("process max RSS", max_rss, &write_wire)?;
            untruncated_max_rss_samples += 1;
        }
        if let (Some(rss), Some(max_rss)) = (rss, max_rss) {
            ensure!(
                max_rss >= rss,
                "process max RSS should be at least current RSS: rss={rss} max_rss={max_rss}"
            );
        }

        let session = lease.call_ok(
            catalog::SANDBOX_COMMAND_EXEC,
            json!({
                "cmd": format!("sh -c 'echo resource-report-{sample}; sleep 60'"),
                "yield_time_ms": 100,
                "timeout_seconds": timeout_s,}),
        )?;
        assert_eq!(as_str(&session, "status")?, "running", "{session}");
        // COMMAND_CANCEL returns the cancelled command's own outcome, whose
        // response `success` is false for a killed command — use `call` (the
        // command-cancel convention, as in the isolated-workspace tests) rather
        // than `call_ok`, then assert the structured status below.
        let cancel = lease.call(
            catalog::SANDBOX_COMMAND_CANCEL,
            json!({"command_id": as_str(&session, "command_id")?}),
        )?;
        let cancel = response_result(&cancel)?.clone();
        assert!(
            matches!(as_str(&cancel, "status")?, "cancelled" | "ok" | "error"),
            "resource report cancel should return structured status: {cancel}"
        );
        wait_for_command_count(&lease, 0)?;
        let metrics = wait_for_active_leases(&lease, 0)?;
        let command_count = lease.call_ok(catalog::SANDBOX_COMMAND_COUNT, json!({}))?;

        samples.push(json!({
            "sample": sample,
            "write_status": write.get("status").and_then(Value::as_str),
            "read_trace_step": "dispatch",
            "exec_trace_resources": exec_trace_resources,
            "memory_current_bytes": memory_current,
            "rss_bytes": rss,
            "max_rss_bytes": max_rss,
            "command_status": as_str(&session, "status")?,
            "cancel_status": as_str(&cancel, "status")?,
            "metrics": metrics,
            "command_count": command_count,
        }));
    }

    let final_metrics = wait_for_active_leases(&lease, 0)?;
    let final_command_count = lease.call_ok(catalog::SANDBOX_COMMAND_COUNT, json!({}))?;
    let ready = lease.call_ok(catalog::SANDBOX_RUNTIME_READY, json!({}))?;
    assert!(as_bool(&ready, "ready")?, "{ready}");
    let plugin_status = lease.call_ok(catalog::SANDBOX_PLUGIN_LIST, json!({}))?;
    let isolated_open = lease.call_ok(catalog::SANDBOX_ISOLATION_LIST_OPEN, json!({}))?;

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
        "resource_samples": {
            "memory_current_bytes": untruncated_memory_samples,
            "rss_bytes": untruncated_rss_samples,
            "max_rss_bytes": untruncated_max_rss_samples,
        },
        "leak_counters": {
            "active_leases": as_i64(&final_metrics, "active_leases")?,
            "command_count": as_i64(&final_command_count, "count")?,
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
            .is_some_and(|samples| samples.len() == workload.sample_count),
        "resource report should include every configured sample: {report}"
    );
    for sample in report["samples"].as_array().expect("samples checked above") {
        ensure!(
            sample["write_status"] == "committed",
            "pressure sample write status should be committed: {sample}"
        );
        ensure!(
            sample["metrics"]["active_leases"] == 0,
            "pressure sample should not leak active leases: {sample}"
        );
        ensure!(
            sample["command_count"]["count"] == 0,
            "pressure sample should not leave live commands: {sample}"
        );
    }
    ensure_resource_sample_counter(&report, "memory_current_bytes")?;
    ensure_resource_sample_counter(&report, "rss_bytes")?;
    ensure_resource_sample_counter(&report, "max_rss_bytes")?;
    ensure_eq_zero(&report, "active_leases")?;
    ensure_eq_zero(&report, "command_count")?;

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

#[test]
fn large_base_overlay_keeps_memory_bounded() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // A large lowerdir base plus a tiny overlay delta must not balloon daemon
    // memory: the base is shared via mount(2), never made resident per op. This
    // is a loose regression gauge (page cache inflates the gauge), not a tight
    // O(1) bound. The ~20MB base is built from sub-cap files (2 MiB write cap).
    seed_base_files(&lease, "pressure/mem/base", 20, 1_000_000)?;
    let exec = lease.call(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": "printf TINY > pressure/mem/delta.txt",
            "yield_time_ms": 1000,
            "timeout_seconds": 30,}),
    )?;
    let (_, exec) =
        finalize_foreground_command_wire(&lease, exec, Instant::now() + Duration::from_secs(35))?;
    assert_eq!(as_str(&exec, "status")?, "ok", "{exec}");
    // Memory gauges land on the fast-path file response; sample one after the op.
    let probe = lease.call(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "pressure/mem/probe.txt", "content": "probe\n", "overwrite": true}),
    )?;
    if let Some(memory_current) = trace_resource_number_or_truncated(
        &probe,
        ResourceStatsKind::CgroupProcess,
        "daemon.response_timings",
        &["cgroup", "memory", "current_bytes"],
    )? {
        ensure_bounded_memory_gauge("cgroup memory.current", memory_current, &probe)?;
    }
    let rss = trace_resource_number_or_truncated(
        &probe,
        ResourceStatsKind::CgroupProcess,
        "daemon.response_timings",
        &["process", "gauges", "rss_bytes"],
    )?;
    if let Some(rss) = rss {
        ensure_bounded_memory_gauge("process RSS", rss, &probe)?;
    }
    let max_rss = trace_resource_number_or_truncated(
        &probe,
        ResourceStatsKind::CgroupProcess,
        "daemon.response_timings",
        &["process", "gauges", "max_rss_bytes"],
    )?;
    if let Some(max_rss) = max_rss {
        ensure_bounded_memory_gauge("process max RSS", max_rss, &probe)?;
    }
    if let (Some(rss), Some(max_rss)) = (rss, max_rss) {
        ensure!(
            max_rss >= rss,
            "process max RSS should be at least current RSS after overlay op: rss={rss} max_rss={max_rss}"
        );
    }
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}

fn ensure_bounded_memory_gauge(label: &str, value: f64, response: &Value) -> Result<()> {
    ensure!(
        value > 0.0 && value < DAEMON_RESOURCE_CEILING_BYTES,
        "{label} gauge should be present and below {DAEMON_RESOURCE_CEILING_BYTES} bytes, got {value}: {response}"
    );
    Ok(())
}

fn ensure_resource_sample_counter(report: &Value, key: &str) -> Result<()> {
    report
        .get("resource_samples")
        .and_then(|samples| samples.get(key))
        .and_then(Value::as_u64)
        .with_context(|| format!("resource_samples.{key} missing in report"))?;
    Ok(())
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
