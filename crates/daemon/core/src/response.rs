//! Shared daemon response shaping and resource timing helpers.

use std::collections::BTreeMap;
use std::time::Instant;

use layerstack::Manifest;
use operation::OpError;
use protocol::{FaultDetails, OperationEnvelope, OperationFault, ResponseMeta};
use serde::Serialize;
use serde_json::{json, Value};
use trace::usize_to_f64_saturating;

use crate::wire::ErrorKind;

pub(crate) fn to_wire_value(output: impl serde::Serialize) -> Value {
    serde_json::to_value(output).expect("operation output DTO serializes to JSON")
}

pub(crate) fn ok_envelope(output: impl Serialize) -> Value {
    let output = to_wire_value(output);
    if is_operation_envelope(&output) {
        return output;
    }
    to_wire_value(OperationEnvelope::ok(output, ResponseMeta::default()))
}

pub(crate) fn rejected_envelope(error: OpError) -> Value {
    to_wire_value(OperationEnvelope::<Value>::rejected(
        operation_fault(
            error.kind,
            error.message,
            error.details.unwrap_or_else(|| serde_json::json!({})),
        ),
        ResponseMeta::default(),
    ))
}

pub(crate) fn rejected_fault_envelope(
    kind: &'static str,
    message: impl Into<String>,
    details: Value,
) -> Value {
    rejected_envelope(OpError {
        kind,
        message: message.into(),
        details: Some(details),
    })
}

pub(crate) fn error_envelope(kind: ErrorKind, message: impl Into<String>, details: Value) -> Value {
    let fault = if kind == ErrorKind::InternalError {
        OperationFault::internal(message, fault_details(details))
    } else {
        operation_fault(error_kind_wire_name(kind), message, details)
    };
    to_wire_value(OperationEnvelope::<Value>::error(
        fault,
        ResponseMeta::default(),
    ))
}

pub(crate) fn is_operation_envelope(value: &Value) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    let Some("ok" | "running" | "rejected" | "cancelled" | "timed_out" | "error") =
        object.get("status").and_then(Value::as_str)
    else {
        return false;
    };
    object.contains_key("meta") && (object.contains_key("result") || object.contains_key("error"))
}

fn operation_fault(
    kind: impl Into<String>,
    message: impl Into<String>,
    details: Value,
) -> OperationFault {
    OperationFault::new(kind, message).with_details(fault_details(details))
}

fn fault_details(details: Value) -> FaultDetails {
    match details {
        Value::Null => FaultDetails::default(),
        Value::Object(fields) if fields.is_empty() => FaultDetails::default(),
        Value::Object(fields) => fields
            .into_iter()
            .fold(FaultDetails::default(), |details, (key, value)| {
                details.with_field(key, value)
            }),
        value => FaultDetails::default().with_field("value", value),
    }
}

fn error_kind_wire_name(kind: ErrorKind) -> &'static str {
    kind.as_str()
}

pub(crate) fn u64_to_f64_saturating(value: u64) -> f64 {
    const U32_FACTOR: f64 = 4_294_967_296.0;
    let high = u32::try_from(value >> 32).unwrap_or(u32::MAX);
    let low = u32::try_from(value & u64::from(u32::MAX)).unwrap_or(u32::MAX);
    f64::from(high).mul_add(U32_FACTOR, f64::from(low))
}

#[cfg(test)]
#[derive(Clone, Debug, Default)]
pub(crate) struct TreeResourceStats {
    exists: f64,
    bytes: f64,
    file_count: f64,
    dir_count: f64,
    entry_count: f64,
    truncated: f64,
    read_error_count: f64,
    first_error_path: Option<String>,
}

#[cfg(test)]
impl TreeResourceStats {
    pub(crate) fn from_host(stats: &workspace::overlay::tree::TreeResourceStats) -> Self {
        let file_entries = stats.files.saturating_add(stats.symlinks);
        let entry_count = file_entries.saturating_add(stats.dirs);
        Self {
            exists: if entry_count > 0 { 1.0 } else { 0.0 },
            bytes: u64_to_f64_saturating(stats.bytes),
            file_count: u64_to_f64_saturating(file_entries),
            dir_count: u64_to_f64_saturating(stats.dirs),
            entry_count: u64_to_f64_saturating(entry_count),
            truncated: if stats.truncated { 1.0 } else { 0.0 },
            read_error_count: u64_to_f64_saturating(stats.read_error_count),
            first_error_path: stats.first_error_path.clone(),
        }
    }
}

pub(crate) fn resource_timings(
    manifest: &Manifest,
    changed_path_count: usize,
) -> serde_json::Map<String, Value> {
    let mut timings = serde_json::Map::new();
    timings.insert(
        "resource.command_exec.changed_path_count".to_owned(),
        json!(usize_to_f64_saturating(changed_path_count)),
    );
    timings.insert(
        "resource.layer_stack.manifest_depth".to_owned(),
        json!(usize_to_f64_saturating(manifest.depth())),
    );
    timings.insert(
        "resource.layer_stack.manifest_path_count".to_owned(),
        json!(usize_to_f64_saturating(manifest.layers.len())),
    );
    // Tree stats appear only when a path actually paid for a walk; absence
    // means "not sampled", never a fabricated zero walk.
    insert_cgroup_process_resource_timings(&mut timings);
    timings
}

pub(crate) fn insert_cgroup_process_resource_timings(timings: &mut serde_json::Map<String, Value>) {
    let sampler_start = Instant::now();
    insert_cgroup_resource_timings(timings);
    insert_process_resource_timings(timings);
    timings.insert(
        "resource.sampler.cgroup_process_duration_us".to_owned(),
        json!(sampler_start.elapsed().as_micros()),
    );
}

#[cfg(test)]
pub(crate) fn insert_tree_resource_timings(
    timings: &mut serde_json::Map<String, Value>,
    prefix: &str,
    stats: &TreeResourceStats,
) {
    timings.insert(format!("{prefix}_tree_exists"), json!(stats.exists));
    timings.insert(format!("{prefix}_tree_bytes"), json!(stats.bytes));
    timings.insert(format!("{prefix}_tree_file_count"), json!(stats.file_count));
    timings.insert(format!("{prefix}_tree_dir_count"), json!(stats.dir_count));
    timings.insert(
        format!("{prefix}_tree_entry_count"),
        json!(stats.entry_count),
    );
    timings.insert(format!("{prefix}_tree_truncated"), json!(stats.truncated));
    timings.insert(
        format!("{prefix}_tree_read_error_count"),
        json!(stats.read_error_count),
    );
    if let Some(path) = &stats.first_error_path {
        timings.insert(format!("{prefix}_tree_first_error_path"), json!(path));
    }
}

fn insert_cgroup_resource_timings(timings: &mut serde_json::Map<String, Value>) {
    if let Ok(raw) = std::fs::read_to_string("/sys/fs/cgroup/cpu.stat") {
        for line in raw.lines() {
            let mut parts = line.split_whitespace();
            let Some(name) = parts.next() else {
                continue;
            };
            let Some(value) = parts.next().and_then(|raw| raw.parse::<f64>().ok()) else {
                continue;
            };
            timings.insert(format!("resource.cgroup.cpu_{name}"), json!(value));
        }
    }

    for (path, key) in [
        (
            "/sys/fs/cgroup/memory.current",
            "resource.cgroup.memory_current_bytes",
        ),
        (
            "/sys/fs/cgroup/memory.peak",
            "resource.cgroup.memory_peak_bytes",
        ),
        (
            "/sys/fs/cgroup/memory.swap.current",
            "resource.cgroup.memory_swap_current_bytes",
        ),
        (
            "/sys/fs/cgroup/memory.swap.peak",
            "resource.cgroup.memory_swap_peak_bytes",
        ),
    ] {
        if let Ok(raw) = std::fs::read_to_string(path) {
            if let Ok(value) = raw.trim().parse::<f64>() {
                timings.insert(key.to_owned(), json!(value));
            }
        }
    }

    if let Ok(raw) = std::fs::read_to_string("/sys/fs/cgroup/memory.events") {
        for line in raw.lines() {
            let mut parts = line.split_whitespace();
            let Some(name) = parts.next() else {
                continue;
            };
            let Some(value) = parts.next().and_then(|raw| raw.parse::<f64>().ok()) else {
                continue;
            };
            timings.insert(
                format!("resource.cgroup.memory_events_{name}"),
                json!(value),
            );
        }
    }

    if let Ok(raw) = std::fs::read_to_string("/sys/fs/cgroup/io.stat") {
        let mut totals = BTreeMap::<&str, f64>::from([
            ("rbytes", 0.0),
            ("wbytes", 0.0),
            ("rios", 0.0),
            ("wios", 0.0),
            ("dbytes", 0.0),
            ("dios", 0.0),
        ]);
        for line in raw.lines() {
            for token in line.split_whitespace().skip(1) {
                let Some((name, raw_value)) = token.split_once('=') else {
                    continue;
                };
                let Some(total) = totals.get_mut(name) else {
                    continue;
                };
                if let Ok(value) = raw_value.parse::<f64>() {
                    *total += value;
                }
            }
        }
        for (name, value) in totals {
            timings.insert(format!("resource.cgroup.io_{name}"), json!(value));
        }
    }

    for (path, prefix) in [
        ("/sys/fs/cgroup/cpu.pressure", "cpu"),
        ("/sys/fs/cgroup/memory.pressure", "memory"),
        ("/sys/fs/cgroup/io.pressure", "io"),
    ] {
        if let Ok(raw) = std::fs::read_to_string(path) {
            insert_pressure_timings(timings, prefix, &raw);
        }
    }
}

fn insert_pressure_timings(timings: &mut serde_json::Map<String, Value>, prefix: &str, raw: &str) {
    for (key, value) in parse_pressure_metrics(prefix, raw) {
        timings.insert(format!("resource.cgroup.psi_{key}"), json!(value));
    }
}

fn parse_pressure_metrics(prefix: &str, raw: &str) -> BTreeMap<String, f64> {
    let mut metrics = BTreeMap::new();
    for line in raw.lines() {
        let mut tokens = line.split_whitespace();
        let Some(level @ ("some" | "full")) = tokens.next() else {
            continue;
        };
        for token in tokens {
            let Some((name @ ("avg10" | "avg60" | "avg300" | "total"), raw_value)) =
                token.split_once('=')
            else {
                continue;
            };
            if let Ok(value) = raw_value.parse::<f64>() {
                metrics.insert(format!("{prefix}_{level}_{name}"), value);
            }
        }
    }
    metrics
}

/// Emit daemon process memory from `/proc/self/status`: `VmRSS` (current
/// resident set) and `VmHWM` (peak resident set), reported in bytes. These are
/// gauges, not run deltas, and are absent on non-Linux dev hosts where the file
/// does not exist.
fn insert_process_resource_timings(timings: &mut serde_json::Map<String, Value>) {
    let Ok(status) = std::fs::read_to_string("/proc/self/status") else {
        return;
    };
    for line in status.lines() {
        let key = match line.split(':').next() {
            Some("VmRSS") => "resource.process.rss_bytes",
            Some("VmHWM") => "resource.process.max_rss_bytes",
            _ => continue,
        };
        if let Some(kib) = line
            .split_whitespace()
            .nth(1)
            .and_then(|value| value.parse::<f64>().ok())
        {
            timings.insert(key.to_owned(), json!(kib * 1024.0));
        }
    }
}

#[cfg(test)]
#[path = "../tests/unit/response/mod.rs"]
mod tests;
