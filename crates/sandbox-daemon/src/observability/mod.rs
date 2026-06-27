pub(crate) mod layerstack;
mod service;
pub(crate) mod view;

pub use service::DaemonObservability;
pub(crate) use view::observability_view_response;

/// Maximum resource/trend lookback window honored by the observability views.
pub(crate) const MAX_RESOURCE_WINDOW_MS: u64 = 600_000;

pub(crate) fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};

    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    i64::try_from(duration.as_millis()).unwrap_or(i64::MAX)
}
