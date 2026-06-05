//! Layer-A stepping: pull a live [`QueryStream`] to a chosen checkpoint, then
//! return so the caller can `drop` the stream (releasing the `&mut QueryContext`
//! borrow) and inspect the context — no final closure required (TESTING_SPEC §4,
//! the `hard_ceiling_exit_*` seam).

use eos_engine::{QueryStream, StreamEvent};
use futures::StreamExt;

/// Pull `stream` forward, collecting each streamed [`StreamEvent`] until `stop`
/// returns `true` for one (inclusive) or the stream ends, then return the
/// collected events. The caller drops the stream and inspects the
/// `QueryContext` at the checkpoint.
///
/// # Panics
/// Panics if the stream yields an `EngineError` item (a test-harness assertion).
pub async fn run_until<F>(stream: &mut QueryStream<'_>, mut stop: F) -> Vec<StreamEvent>
where
    F: FnMut(&StreamEvent) -> bool,
{
    let mut events = Vec::new();
    while let Some(item) = stream.next().await {
        let (event, _usage) = item.expect("query stream item");
        let reached = stop(&event);
        events.push(event);
        if reached {
            break;
        }
    }
    events
}
