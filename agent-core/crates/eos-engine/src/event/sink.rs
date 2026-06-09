//! Engine event observation sink.

use std::sync::Arc;

use super::StreamEvent;

/// Per-run stream/tool/system event sink.
pub type EngineEventSink = Arc<dyn Fn(&StreamEvent) + Send + Sync>;
