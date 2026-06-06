//! WebSocket transport for the milestone stream.

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::response::Response;

use eos_types::RequestId;

use crate::router::AppState;

/// Accept the upgrade and pump milestone records to the client as JSON text
/// frames (replay from `last_seq`, then live).
pub fn upgrade(
    upgrade: WebSocketUpgrade,
    state: AppState,
    request_id: RequestId,
    last_seq: i64,
) -> Response {
    upgrade.on_upgrade(move |socket| pump(socket, state, request_id, last_seq))
}

/// Subscribe and forward each [`EventRecord`](eos_backend_types::EventRecord) as
/// a JSON text frame until the stream ends, the client disconnects, or a store
/// error occurs.
async fn pump(mut socket: WebSocket, state: AppState, request_id: RequestId, last_seq: i64) {
    let mut subscription = match state.event_bus.subscribe(&request_id, last_seq).await {
        Ok(subscription) => subscription,
        Err(err) => {
            tracing::error!(error = %err, "ws subscribe failed; closing");
            let _ = socket.send(Message::Close(None)).await;
            return;
        }
    };
    loop {
        match subscription.recv().await {
            Ok(Some(record)) => {
                let Ok(text) = serde_json::to_string(&record) else {
                    continue;
                };
                if socket.send(Message::Text(text.into())).await.is_err() {
                    break; // client gone
                }
            }
            Ok(None) => break,
            Err(err) => {
                tracing::error!(error = %err, "ws replay refill failed; closing");
                break;
            }
        }
    }
    let _ = socket.send(Message::Close(None)).await;
}
