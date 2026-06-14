use std::path::Path;

use overlay::LayerChange;

use crate::tree::TreeResourceStats;

/// Captured upperdir changes and resource stats.
#[derive(Debug, Clone, PartialEq)]
pub struct CapturedChanges {
    pub changes: Vec<LayerChange>,
    pub stats: TreeResourceStats,
    pub capture_s: f64,
}

/// Error raised while capturing an overlay upperdir.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureError {
    pub reason: String,
    pub failing_path: Option<String>,
}

impl std::fmt::Display for CaptureError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self.failing_path.as_deref() {
            Some(path) => write!(formatter, "capture failed at {path}: {}", self.reason),
            None => write!(formatter, "capture failed: {}", self.reason),
        }
    }
}

impl std::error::Error for CaptureError {}

/// Capture an upperdir delta and resource stats.
///
/// # Errors
///
/// Returns [`CaptureError`] when the upperdir walk fails.
pub fn capture_upperdir(upperdir: &Path) -> Result<CapturedChanges, CaptureError> {
    let start = std::time::Instant::now();
    let captured =
        overlay::capture_upperdir_with_stats(upperdir).map_err(|error| CaptureError {
            failing_path: error.failing_path().map(|path| path.display().to_string()),
            reason: error.to_string(),
        })?;
    Ok(CapturedChanges {
        changes: captured.changes,
        stats: TreeResourceStats::from(captured.stats),
        capture_s: start.elapsed().as_secs_f64(),
    })
}
