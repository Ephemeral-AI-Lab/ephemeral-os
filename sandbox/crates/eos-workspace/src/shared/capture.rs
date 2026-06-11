use std::path::Path;

use eos_overlay::LayerChange;

use super::TreeResourceStats;

/// Captured upperdir changes and resource stats.
#[derive(Debug, Clone, PartialEq)]
pub struct CapturedChanges {
    pub changes: Vec<LayerChange>,
    pub stats: TreeResourceStats,
    pub capture_s: f64,
}

/// Error raised while capturing an overlay upperdir.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("capture failed: {reason}")]
pub struct CaptureError {
    pub reason: String,
}

/// Capture an upperdir delta and resource stats.
///
/// # Errors
///
/// Returns [`CaptureError`] when the upperdir walk fails.
pub fn capture_upperdir(upperdir: &Path) -> Result<CapturedChanges, CaptureError> {
    let start = std::time::Instant::now();
    let changes = eos_overlay::capture_upperdir(upperdir).map_err(|error| CaptureError {
        reason: error.to_string(),
    })?;
    Ok(CapturedChanges {
        changes,
        stats: TreeResourceStats::collect(upperdir),
        capture_s: start.elapsed().as_secs_f64(),
    })
}
