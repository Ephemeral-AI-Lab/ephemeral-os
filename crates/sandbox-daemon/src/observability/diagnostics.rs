use std::collections::BTreeSet;
use std::fs::OpenOptions;
use std::io::{self, Write as _};
use std::os::unix::fs::{OpenOptionsExt as _, PermissionsExt as _};
use std::path::{Path, PathBuf};

use sandbox_config::configs::observability::{DiagnosticsConfig, MAX_DIAGNOSTIC_ARTIFACT_BYTES};
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
    previous: Option<ProcessPoint>,
    cpu_window: Option<SustainedWindow>,
    memory_window: Option<SustainedWindow>,
    trigger_count: u64,
    cooldown_until_unix_ms: Option<u64>,
    latest: Option<DaemonDiagnosticSummary>,
    last_error: Option<String>,
}

#[derive(Clone, Copy)]
struct ProcessPoint {
    sampled_at_unix_ms: u64,
    cpu_time_us: Option<u64>,
}

#[derive(Clone, Copy)]
struct SustainedWindow {
    started_at_unix_ms: u64,
    cpu_time_us: Option<u64>,
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
            previous: None,
            cpu_window: None,
            memory_window: None,
            trigger_count: 0,
            cooldown_until_unix_ms: None,
            latest: None,
            last_error: None,
        }
    }

    pub(crate) fn observe(
        &mut self,
        process: &DaemonProcessMetrics,
        runtime_usage: &DaemonRuntimeUsage,
        ownership: &DaemonOwnershipMetrics,
        workspace_holders: &[DaemonDiagnosticWorkspaceHolder],
    ) -> DaemonDiagnosticState {
        let now = process.sampled_at_unix_ms;
        if !self.config.enabled {
            self.previous = Some(ProcessPoint::from(process));
            return self.state(now);
        }

        let current_interval = self
            .previous
            .and_then(|previous| cpu_interval(previous, ProcessPoint::from(process)));
        let cpu_high = current_interval
            .as_ref()
            .and_then(|interval| interval.percent_of_one_core)
            .is_some_and(|percent| percent > self.config.cpu_threshold_percent);
        update_window(&mut self.cpu_window, cpu_high, now, process.cpu_time_us);
        let memory_high = process
            .anonymous_memory_bytes
            .is_some_and(|bytes| bytes > self.config.anonymous_memory_threshold_bytes);
        update_window(
            &mut self.memory_window,
            memory_high,
            now,
            process.cpu_time_us,
        );

        let trigger = self.ready_trigger(now, ownership);
        let in_cooldown = self.cooldown_until_unix_ms.is_some_and(|until| now < until);
        if let Some((trigger, window)) = trigger.filter(|_| !in_cooldown) {
            let interval = match trigger {
                DaemonDiagnosticTrigger::Cpu | DaemonDiagnosticTrigger::AnonymousMemory => {
                    window_cpu_interval(window, ProcessPoint::from(process))
                }
                DaemonDiagnosticTrigger::ExitedUnreapedHolder => {
                    current_interval.unwrap_or_else(DaemonDiagnosticCpuInterval::default)
                }
            };
            match capture(
                &self.artifact_path,
                self.config.max_artifact_bytes,
                trigger,
                process,
                interval,
                runtime_usage,
                ownership,
                workspace_holders,
            ) {
                Ok(summary) => {
                    self.trigger_count = self.trigger_count.saturating_add(1);
                    self.latest = Some(summary);
                    self.last_error = None;
                    self.cooldown_until_unix_ms = Some(now.saturating_add(self.config.cooldown_ms));
                    self.cpu_window = None;
                    self.memory_window = None;
                }
                Err(error) => {
                    self.last_error = Some(truncate_utf8(&error.to_string(), MAX_ERROR_BYTES));
                    self.cooldown_until_unix_ms = Some(now.saturating_add(self.config.cooldown_ms));
                    self.cpu_window = None;
                    self.memory_window = None;
                }
            }
        }
        self.previous = Some(ProcessPoint::from(process));
        self.state(now)
    }

    fn ready_trigger(
        &self,
        now: u64,
        ownership: &DaemonOwnershipMetrics,
    ) -> Option<(DaemonDiagnosticTrigger, SustainedWindow)> {
        if ownership.exited_unreaped_holders.unwrap_or(0) > 0 {
            return Some((
                DaemonDiagnosticTrigger::ExitedUnreapedHolder,
                SustainedWindow {
                    started_at_unix_ms: now,
                    cpu_time_us: None,
                },
            ));
        }
        let window_ms = self.config.sustained_window_ms;
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

    fn state(&self, now: u64) -> DaemonDiagnosticState {
        let active = match (self.cpu_window, self.memory_window) {
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
            self.cooldown_until_unix_ms
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
            trigger_count: self.trigger_count,
            active_window,
            cooldown,
            latest: self.latest.clone(),
            last_error: self.last_error.clone(),
        }
    }
}

impl From<&DaemonProcessMetrics> for ProcessPoint {
    fn from(value: &DaemonProcessMetrics) -> Self {
        Self {
            sampled_at_unix_ms: value.sampled_at_unix_ms,
            cpu_time_us: value.cpu_time_us,
        }
    }
}

fn update_window(
    window: &mut Option<SustainedWindow>,
    above: bool,
    now: u64,
    cpu_time_us: Option<u64>,
) {
    if above {
        window.get_or_insert(SustainedWindow {
            started_at_unix_ms: now,
            cpu_time_us,
        });
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
        },
        current,
    )
    .unwrap_or_default()
}

fn capture(
    artifact_path: &Path,
    max_artifact_bytes: usize,
    trigger: DaemonDiagnosticTrigger,
    process: &DaemonProcessMetrics,
    cpu_interval: DaemonDiagnosticCpuInterval,
    runtime_usage: &DaemonRuntimeUsage,
    ownership: &DaemonOwnershipMetrics,
    workspace_holders: &[DaemonDiagnosticWorkspaceHolder],
) -> io::Result<DaemonDiagnosticSummary> {
    let cap = max_artifact_bytes.min(MAX_DIAGNOSTIC_ARTIFACT_BYTES);
    let (normalized_holders, mut omitted_workspace_id_count) =
        normalize_workspace_holders(workspace_holders);
    let normalized_ids = normalized_holders
        .iter()
        .map(|holder| holder.workspace_id.clone())
        .collect();
    let mut payload = DiagnosticPayload {
        schema_version: 1,
        captured_at_unix_ms: process.sampled_at_unix_ms,
        trigger,
        activity_classes: vec![
            "rpc.observability".to_owned(),
            "observability.topology".to_owned(),
        ],
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
    };

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
