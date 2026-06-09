//! Test-only fault injection for the enter/exit lifecycle.
//!
//! `maybe_inject_phase` reads `EOS_ISOLATED_WORKSPACE_TEST_*` env vars to force a
//! timeout, failure, or delay at a named pipeline phase so rollback paths can be
//! exercised without real kernel faults.

use std::time::Duration;

use crate::isolated::error::IsolatedError;

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
