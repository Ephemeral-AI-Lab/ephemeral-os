pub(crate) mod layerstack;
mod service;
pub(crate) mod view;

pub use service::DaemonObservability;
pub(crate) use view::observability_view_response;

pub(crate) fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};

    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    i64::try_from(duration.as_millis()).unwrap_or(i64::MAX)
}
