use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::isolated::error::IsolatedError;

use super::WorkspaceHandle;

pub(super) fn maybe_inject_phase(phase: &str) -> Result<(), IsolatedError> {
    if let Some(target) = env_trimmed("EOS_ISOLATED_WORKSPACE_TEST_HANG_AT") {
        if phase_matches(&target, phase) {
            return Err(IsolatedError::SetupTimeout { step: target });
        }
    }
    if let Some(target) = env_trimmed("EOS_ISOLATED_WORKSPACE_TEST_FAIL_AT") {
        if phase_matches(&target, phase) {
            return Err(IsolatedError::SetupFailed { step: target });
        }
    }
    if let Some(delays) = env_trimmed("EOS_ISOLATED_WORKSPACE_TEST_PHASE_DELAY") {
        for spec in delays.split(',') {
            let Some((target, delay_ms)) = spec.split_once(':') else {
                continue;
            };
            if !phase_matches(target, phase) {
                continue;
            }
            let delay_ms = delay_ms.trim().trim_end_matches("ms").trim();
            let Ok(delay_ms) = delay_ms.parse::<f64>() else {
                continue;
            };
            if delay_ms.is_finite() && delay_ms > 0.0 {
                std::thread::sleep(Duration::from_secs_f64(delay_ms / 1000.0));
            }
        }
    }
    Ok(())
}

fn phase_matches(target: &str, phase: &str) -> bool {
    let target = target.trim();
    target == phase || matches!((target, phase), ("overlay_mount", "mount_overlay"))
}

fn env_trimmed(key: &str) -> Option<String> {
    let value = std::env::var(key).ok()?.trim().to_owned();
    if value.is_empty() {
        return None;
    }
    Some(value)
}

pub(super) fn close_handle_fds(handle: &WorkspaceHandle) {
    for fd in handle.ns_fds.values().copied() {
        if fd >= 0 {
            let _ = nix::unistd::close(fd);
        }
    }
    for fd in [handle.readiness_fd, handle.control_fd] {
        if fd >= 0 {
            let _ = nix::unistd::close(fd);
        }
    }
}

pub(super) fn next_handle_id() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(1);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let counter = COUNTER.fetch_add(1, Ordering::Relaxed) & 0x00ff_ffff;
    format!("{counter:06x}{nanos:016x}")
}

pub(super) fn monotonic_seconds() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

pub(super) fn directory_file_bytes(path: &Path) -> u64 {
    let mut total = 0_u64;
    let Ok(entries) = std::fs::read_dir(path) else {
        return 0;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(metadata) = entry.metadata() else {
            continue;
        };
        if metadata.is_file() {
            total = total.saturating_add(metadata.len());
        } else if metadata.is_dir() {
            total = total.saturating_add(directory_file_bytes(&path));
        }
    }
    total
}

pub(super) fn mountinfo_reference_count(paths: &[&Path]) -> Option<usize> {
    let mountinfo = std::fs::read_to_string("/proc/self/mountinfo").ok()?;
    let needles = paths
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .filter(|path| !path.is_empty())
        .collect::<Vec<_>>();
    Some(
        mountinfo
            .lines()
            .filter(|line| needles.iter().any(|needle| line.contains(needle)))
            .count(),
    )
}
