use std::collections::BTreeSet;
use std::fs::OpenOptions;
use std::io::{self, Write as _};
use std::os::unix::fs::{OpenOptionsExt as _, PermissionsExt as _};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};

use sandbox_config::configs::observability::{DiagnosticsConfig, MAX_DIAGNOSTIC_ARTIFACT_BYTES};
use sandbox_observability_query::ports::DaemonMetricsRequestClass;
use sandbox_observability_telemetry::collect::process_topology::{
    DaemonDiagnosticCooldown, DaemonDiagnosticCpuInterval, DaemonDiagnosticMemory,
    DaemonDiagnosticRedaction, DaemonDiagnosticState, DaemonDiagnosticSummary,
    DaemonDiagnosticTrigger, DaemonDiagnosticWindow, DaemonDiagnosticWorkspaceHolder,
    DaemonOwnershipMetrics, DaemonProcessMetrics, DaemonRuntimeConfigMetrics, DaemonRuntimeUsage,
};
use serde::Serialize;
use sha2::{Digest as _, Sha256};

const MAX_WORKSPACE_IDS: usize = 128;
const MAX_WORKSPACE_ID_BYTES: usize = 256;
const MAX_ERROR_BYTES: usize = 512;

pub(crate) struct DiagnosticTracker {
    config: DiagnosticsConfig,
    artifact_path: PathBuf,
    state: Mutex<DiagnosticTrackerState>,
}

struct DiagnosticTrackerState {
    previous: Option<ProcessPoint>,
    cpu_window: Option<SustainedWindow>,
    memory_window: Option<SustainedWindow>,
    trigger_count: u64,
    cooldown_until_unix_ms: Option<u64>,
    capture_in_flight: Option<u64>,
    next_capture_sequence: u64,
    latest: Option<DaemonDiagnosticSummary>,
    last_error: Option<String>,
}

#[derive(Clone, Copy)]
struct ProcessPoint {
    sampled_at_unix_ms: u64,
    cpu_time_us: Option<u64>,
    activity_classes: ActivityClasses,
}

#[derive(Clone, Copy)]
struct SustainedWindow {
    started_at_unix_ms: u64,
    cpu_time_us: Option<u64>,
    activity: ActivityEvidence,
}

#[derive(Clone, Copy, Default)]
struct ActivityClasses(u8);

#[derive(Clone, Copy)]
struct ActivityEvidence {
    union: ActivityClasses,
    intersection: ActivityClasses,
}

struct PendingCapture {
    sequence: u64,
    payload: DiagnosticPayload,
}

#[derive(Clone, Serialize)]
struct DiagnosticPayload {
    schema_version: u8,
    captured_at_unix_ms: u64,
    trigger: DaemonDiagnosticTrigger,
    activity_classes: Vec<String>,
    cpu_interval: DaemonDiagnosticCpuInterval,
    memory: DaemonDiagnosticMemory,
    thread_count: Option<u64>,
    runtime_config: DaemonRuntimeConfigMetrics,
    runtime_usage: DaemonRuntimeUsage,
    ownership: DaemonOwnershipMetrics,
    workspace_ids: Vec<String>,
    workspace_holders: Vec<DaemonDiagnosticWorkspaceHolder>,
    workspace_ids_truncated: bool,
    omitted_workspace_id_count: usize,
    redaction: DaemonDiagnosticRedaction,
}

#[derive(Serialize)]
struct DiagnosticArtifact<'a> {
    id: &'a str,
    fingerprint: &'a str,
    #[serde(flatten)]
    payload: &'a DiagnosticPayload,
}

impl DiagnosticTracker {
    pub(crate) fn new(config: DiagnosticsConfig, artifact_path: PathBuf) -> Self {
        Self {
            config,
            artifact_path,
            state: Mutex::new(DiagnosticTrackerState {
                previous: None,
                cpu_window: None,
                memory_window: None,
                trigger_count: 0,
                cooldown_until_unix_ms: None,
                capture_in_flight: None,
                next_capture_sequence: 1,
                latest: None,
                last_error: None,
            }),
        }
    }

    pub(crate) fn observe(
        &self,
        request_class: DaemonMetricsRequestClass,
        process: &DaemonProcessMetrics,
        runtime_usage: &DaemonRuntimeUsage,
        ownership: &DaemonOwnershipMetrics,
        workspace_holders: &[DaemonDiagnosticWorkspaceHolder],
    ) -> DaemonDiagnosticState {
        let now = process.sampled_at_unix_ms;
        let point = ProcessPoint::new(process, request_class);
        let pending = {
            let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
            let Some(pending) = state.prepare_capture(
                &self.config,
                point,
                process,
                runtime_usage,
                ownership,
                workspace_holders,
            ) else {
                return self.public_state(&state, state.effective_now(now));
            };
            pending
        };

        let result = capture(
            &self.artifact_path,
            self.config.max_artifact_bytes,
            pending.payload,
        );
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.finish_capture(pending.sequence, result);
        self.public_state(&state, state.effective_now(now))
    }

    fn public_state(&self, state: &DiagnosticTrackerState, now: u64) -> DaemonDiagnosticState {
        let active = match (state.cpu_window, state.memory_window) {
            (Some(cpu), Some(memory)) if memory.started_at_unix_ms < cpu.started_at_unix_ms => {
                Some((DaemonDiagnosticTrigger::AnonymousMemory, memory))
            }
            (Some(cpu), _) => Some((DaemonDiagnosticTrigger::Cpu, cpu)),
            (None, Some(memory)) => Some((DaemonDiagnosticTrigger::AnonymousMemory, memory)),
            (None, None) => None,
        };
        let active_window =
            active.map_or_else(DaemonDiagnosticWindow::default, |(trigger, window)| {
                DaemonDiagnosticWindow {
                    trigger: Some(trigger),
                    started_at_unix_ms: Some(window.started_at_unix_ms),
                    elapsed_ms: now.saturating_sub(window.started_at_unix_ms),
                }
            });
        let cooldown =
            state
                .cooldown_until_unix_ms
                .map_or_else(DaemonDiagnosticCooldown::default, |until| {
                    DaemonDiagnosticCooldown {
                        active: now < until,
                        until_unix_ms: Some(until),
                        remaining_ms: until.saturating_sub(now),
                    }
                });
        DaemonDiagnosticState {
            enabled: self.config.enabled,
            max_artifact_bytes: self
                .config
                .max_artifact_bytes
                .min(MAX_DIAGNOSTIC_ARTIFACT_BYTES),
            trigger_count: state.trigger_count,
            active_window,
            cooldown,
            latest: state.latest.clone(),
            last_error: state.last_error.clone(),
        }
    }
}

impl DiagnosticTrackerState {
    fn prepare_capture(
        &mut self,
        config: &DiagnosticsConfig,
        point: ProcessPoint,
        process: &DaemonProcessMetrics,
        runtime_usage: &DaemonRuntimeUsage,
        ownership: &DaemonOwnershipMetrics,
        workspace_holders: &[DaemonDiagnosticWorkspaceHolder],
    ) -> Option<PendingCapture> {
        let now = point.sampled_at_unix_ms;
        if let Some(previous) = self.previous.as_mut() {
            if now < previous.sampled_at_unix_ms {
                return None;
            }
            if now == previous.sampled_at_unix_ms {
                previous.activity_classes.extend(point.activity_classes);
                previous.cpu_time_us = max_optional(previous.cpu_time_us, point.cpu_time_us);
                if let Some(window) = self.cpu_window.as_mut() {
                    window.activity.include_equal(point.activity_classes);
                }
                if let Some(window) = self.memory_window.as_mut() {
                    window.activity.include_equal(point.activity_classes);
                }
                return None;
            }
        }

        if !config.enabled {
            self.previous = Some(point);
            return None;
        }

        let current_interval = self
            .previous
            .and_then(|previous| cpu_interval(previous, point));
        let interval_activity = self.previous.map_or(point.activity_classes, |previous| {
            previous.activity_classes.union(point.activity_classes)
        });
        let cpu_high = current_interval
            .as_ref()
            .and_then(|interval| interval.percent_of_one_core)
            .is_some_and(|percent| percent > config.cpu_threshold_percent);
        update_window(
            &mut self.cpu_window,
            cpu_high,
            now,
            process.cpu_time_us,
            interval_activity,
        );
        let memory_high = process
            .anonymous_memory_bytes
            .is_some_and(|bytes| bytes > config.anonymous_memory_threshold_bytes);
        update_window(
            &mut self.memory_window,
            memory_high,
            now,
            process.cpu_time_us,
            point.activity_classes,
        );

        let trigger = self.ready_trigger(config, now, ownership, point.activity_classes);
        let in_cooldown = self.cooldown_until_unix_ms.is_some_and(|until| now < until);
        let can_capture = self.capture_in_flight.is_none() && !in_cooldown;
        let pending = trigger.filter(|_| can_capture).map(|(trigger, window)| {
            let interval = match trigger {
                DaemonDiagnosticTrigger::Cpu | DaemonDiagnosticTrigger::AnonymousMemory => {
                    window_cpu_interval(window, point)
                }
                DaemonDiagnosticTrigger::ExitedUnreapedHolder => {
                    current_interval.unwrap_or_default()
                }
            };
            let sequence = self.next_capture_sequence;
            self.next_capture_sequence = self.next_capture_sequence.saturating_add(1);
            self.capture_in_flight = Some(sequence);
            self.cooldown_until_unix_ms = Some(now.saturating_add(config.cooldown_ms));
            self.cpu_window = None;
            self.memory_window = None;
            PendingCapture {
                sequence,
                payload: diagnostic_payload(
                    trigger,
                    window.activity.resolved(),
                    process,
                    interval,
                    runtime_usage,
                    ownership,
                    workspace_holders,
                ),
            }
        });
        self.previous = Some(point);
        pending
    }

    fn ready_trigger(
        &self,
        config: &DiagnosticsConfig,
        now: u64,
        ownership: &DaemonOwnershipMetrics,
        activity_classes: ActivityClasses,
    ) -> Option<(DaemonDiagnosticTrigger, SustainedWindow)> {
        if ownership.exited_unreaped_holders.unwrap_or(0) > 0 {
            return Some((
                DaemonDiagnosticTrigger::ExitedUnreapedHolder,
                SustainedWindow {
                    started_at_unix_ms: now,
                    cpu_time_us: None,
                    activity: ActivityEvidence::new(activity_classes),
                },
            ));
        }
        let window_ms = config.sustained_window_ms;
        let cpu = self
            .cpu_window
            .filter(|window| now.saturating_sub(window.started_at_unix_ms) >= window_ms);
        let memory = self
            .memory_window
            .filter(|window| now.saturating_sub(window.started_at_unix_ms) >= window_ms);
        match (cpu, memory) {
            (Some(cpu), Some(memory)) if memory.started_at_unix_ms < cpu.started_at_unix_ms => {
                Some((DaemonDiagnosticTrigger::AnonymousMemory, memory))
            }
            (Some(cpu), _) => Some((DaemonDiagnosticTrigger::Cpu, cpu)),
            (None, Some(memory)) => Some((DaemonDiagnosticTrigger::AnonymousMemory, memory)),
            (None, None) => None,
        }
    }

    fn finish_capture(&mut self, sequence: u64, result: io::Result<DaemonDiagnosticSummary>) {
        if self.capture_in_flight != Some(sequence) {
            return;
        }
        self.capture_in_flight = None;
        match result {
            Ok(summary) => {
                self.trigger_count = self.trigger_count.saturating_add(1);
                self.latest = Some(summary);
                self.last_error = None;
            }
            Err(error) => {
                self.last_error = Some(truncate_utf8(&error.to_string(), MAX_ERROR_BYTES));
            }
        }
    }

    fn effective_now(&self, now: u64) -> u64 {
        self.previous
            .map_or(now, |previous| now.max(previous.sampled_at_unix_ms))
    }
}

impl ProcessPoint {
    fn new(value: &DaemonProcessMetrics, request_class: DaemonMetricsRequestClass) -> Self {
        Self {
            sampled_at_unix_ms: value.sampled_at_unix_ms,
            cpu_time_us: value.cpu_time_us,
            activity_classes: ActivityClasses::from_request(request_class),
        }
    }
}

impl ActivityClasses {
    const LEGACY_CGROUP: u8 = 1 << 0;
    const TOPOLOGY: u8 = 1 << 1;
    const DAEMON_SELF: u8 = 1 << 2;

    fn from_request(request_class: DaemonMetricsRequestClass) -> Self {
        Self(match request_class {
            DaemonMetricsRequestClass::LegacyCgroup => Self::LEGACY_CGROUP,
            DaemonMetricsRequestClass::Topology => Self::TOPOLOGY,
            DaemonMetricsRequestClass::DaemonSelf => Self::DAEMON_SELF,
        })
    }

    fn extend(&mut self, other: Self) {
        self.0 |= other.0;
    }

    fn union(self, other: Self) -> Self {
        Self(self.0 | other.0)
    }

    fn intersection(self, other: Self) -> Self {
        Self(self.0 & other.0)
    }

    fn is_empty(self) -> bool {
        self.0 == 0
    }

    fn into_vec(self) -> Vec<String> {
        let mut activity = vec!["rpc.observability".to_owned()];
        for (bit, name) in [
            (Self::LEGACY_CGROUP, "observability.cgroup"),
            (Self::TOPOLOGY, "observability.topology"),
            (Self::DAEMON_SELF, "observability.daemon"),
        ] {
            if self.0 & bit != 0 {
                activity.push(name.to_owned());
            }
        }
        activity
    }
}

impl ActivityEvidence {
    fn new(activity_classes: ActivityClasses) -> Self {
        Self {
            union: activity_classes,
            intersection: activity_classes,
        }
    }

    fn observe(&mut self, activity_classes: ActivityClasses) {
        self.union.extend(activity_classes);
        self.intersection = self.intersection.intersection(activity_classes);
    }

    fn include_equal(&mut self, activity_classes: ActivityClasses) {
        self.union.extend(activity_classes);
        self.intersection.extend(activity_classes);
    }

    fn resolved(self) -> ActivityClasses {
        if self.intersection.is_empty() {
            self.union
        } else {
            self.intersection
        }
    }
}

fn max_optional(left: Option<u64>, right: Option<u64>) -> Option<u64> {
    match (left, right) {
        (Some(left), Some(right)) => Some(left.max(right)),
        (Some(value), None) | (None, Some(value)) => Some(value),
        (None, None) => None,
    }
}

fn update_window(
    window: &mut Option<SustainedWindow>,
    above: bool,
    now: u64,
    cpu_time_us: Option<u64>,
    activity_classes: ActivityClasses,
) {
    if above {
        match window {
            Some(window) => window.activity.observe(activity_classes),
            None => {
                *window = Some(SustainedWindow {
                    started_at_unix_ms: now,
                    cpu_time_us,
                    activity: ActivityEvidence::new(activity_classes),
                });
            }
        }
    } else {
        *window = None;
    }
}

fn cpu_interval(
    previous: ProcessPoint,
    current: ProcessPoint,
) -> Option<DaemonDiagnosticCpuInterval> {
    let elapsed_ms = current
        .sampled_at_unix_ms
        .checked_sub(previous.sampled_at_unix_ms)?;
    if elapsed_ms == 0 {
        return None;
    }
    let cpu_time_delta_us = match (previous.cpu_time_us, current.cpu_time_us) {
        (Some(previous), Some(current)) => current.checked_sub(previous),
        _ => None,
    };
    let percent_of_one_core = cpu_time_delta_us.map(|delta| {
        let elapsed_us = elapsed_ms as f64 * 1_000.0;
        delta as f64 * 100.0 / elapsed_us
    });
    Some(DaemonDiagnosticCpuInterval {
        elapsed_ms,
        cpu_time_delta_us,
        percent_of_one_core,
    })
}

fn window_cpu_interval(
    window: SustainedWindow,
    current: ProcessPoint,
) -> DaemonDiagnosticCpuInterval {
    cpu_interval(
        ProcessPoint {
            sampled_at_unix_ms: window.started_at_unix_ms,
            cpu_time_us: window.cpu_time_us,
            activity_classes: window.activity.resolved(),
        },
        current,
    )
    .unwrap_or_default()
}

fn diagnostic_payload(
    trigger: DaemonDiagnosticTrigger,
    activity_classes: ActivityClasses,
    process: &DaemonProcessMetrics,
    cpu_interval: DaemonDiagnosticCpuInterval,
    runtime_usage: &DaemonRuntimeUsage,
    ownership: &DaemonOwnershipMetrics,
    workspace_holders: &[DaemonDiagnosticWorkspaceHolder],
) -> DiagnosticPayload {
    let (normalized_holders, omitted_workspace_id_count) =
        normalize_workspace_holders(workspace_holders);
    let normalized_ids = normalized_holders
        .iter()
        .map(|holder| holder.workspace_id.clone())
        .collect();
    DiagnosticPayload {
        schema_version: 1,
        captured_at_unix_ms: process.sampled_at_unix_ms,
        trigger,
        activity_classes: activity_classes.into_vec(),
        cpu_interval,
        memory: DaemonDiagnosticMemory {
            resident_memory_bytes: process.resident_memory_bytes,
            proportional_set_size_bytes: process.proportional_set_size_bytes,
            anonymous_memory_bytes: process.anonymous_memory_bytes,
            private_dirty_bytes: process.private_dirty_bytes,
            anonymous_huge_pages_bytes: process.anonymous_huge_pages_bytes,
        },
        thread_count: process.thread_count,
        runtime_config: process.runtime_config.clone(),
        runtime_usage: runtime_usage.clone(),
        ownership: ownership.clone(),
        workspace_ids: normalized_ids,
        workspace_holders: normalized_holders,
        workspace_ids_truncated: omitted_workspace_id_count > 0,
        omitted_workspace_id_count,
        redaction: DaemonDiagnosticRedaction::default(),
    }
}

fn capture(
    artifact_path: &Path,
    max_artifact_bytes: usize,
    mut payload: DiagnosticPayload,
) -> io::Result<DaemonDiagnosticSummary> {
    let cap = max_artifact_bytes.min(MAX_DIAGNOSTIC_ARTIFACT_BYTES);
    let mut omitted_workspace_id_count = payload.omitted_workspace_id_count;
    loop {
        let payload_bytes = serde_json::to_vec(&payload).map_err(io::Error::other)?;
        let fingerprint = hex_digest(Sha256::digest(&payload_bytes));
        let id = format!("diagnostic-{}", &fingerprint[..24]);
        let artifact = DiagnosticArtifact {
            id: &id,
            fingerprint: &fingerprint,
            payload: &payload,
        };
        let bytes = serde_json::to_vec(&artifact).map_err(io::Error::other)?;
        if bytes.len() <= cap {
            write_artifact(artifact_path, &bytes)?;
            return Ok(DaemonDiagnosticSummary {
                id,
                fingerprint,
                size_bytes: bytes.len(),
                captured_at_unix_ms: payload.captured_at_unix_ms,
                trigger: payload.trigger,
                activity_classes: payload.activity_classes,
                cpu_interval: payload.cpu_interval,
                memory: payload.memory,
                thread_count: payload.thread_count,
                runtime_config: payload.runtime_config,
                runtime_usage: payload.runtime_usage,
                ownership: payload.ownership,
                workspace_ids: payload.workspace_ids,
                workspace_holders: payload.workspace_holders,
                workspace_ids_truncated: payload.workspace_ids_truncated,
                omitted_workspace_id_count: payload.omitted_workspace_id_count,
                redaction: payload.redaction,
            });
        }
        if payload.workspace_ids.pop().is_none() {
            return Err(io::Error::other(format!(
                "diagnostic artifact minimum size {} exceeds cap {cap}",
                bytes.len()
            )));
        }
        payload.workspace_holders.pop();
        omitted_workspace_id_count = omitted_workspace_id_count.saturating_add(1);
        payload.omitted_workspace_id_count = omitted_workspace_id_count;
        payload.workspace_ids_truncated = true;
    }
}

fn normalize_workspace_holders(
    workspace_holders: &[DaemonDiagnosticWorkspaceHolder],
) -> (Vec<DaemonDiagnosticWorkspaceHolder>, usize) {
    let mut retained = BTreeSet::new();
    for holder in workspace_holders {
        retained.insert(DaemonDiagnosticWorkspaceHolder {
            workspace_id: truncate_utf8(&holder.workspace_id, MAX_WORKSPACE_ID_BYTES),
            holder_pid: holder.holder_pid,
        });
        if retained.len() > MAX_WORKSPACE_IDS {
            retained.pop_last();
        }
    }
    let omitted = workspace_holders.len().saturating_sub(retained.len());
    (retained.into_iter().collect(), omitted)
}

fn write_artifact(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let Some(parent) = path.parent() else {
        return Err(io::Error::other("diagnostic artifact path has no parent"));
    };
    std::fs::create_dir_all(parent)?;
    let temporary = path.with_extension("tmp");
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o600)
        .open(&temporary)?;
    file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
    file.write_all(bytes)?;
    file.sync_all()?;
    std::fs::rename(temporary, path)
}

fn truncate_utf8(value: &str, max_bytes: usize) -> String {
    if value.len() <= max_bytes {
        return value.to_owned();
    }
    let mut end = max_bytes;
    while !value.is_char_boundary(end) {
        end = end.saturating_sub(1);
    }
    value[..end].to_owned()
}

fn hex_digest(bytes: impl AsRef<[u8]>) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let bytes = bytes.as_ref();
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}
