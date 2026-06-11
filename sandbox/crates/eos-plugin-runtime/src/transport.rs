//! Daemon-side PPC request/reply transport.
//!
//! This is the boundary the daemon uses once a plugin service has connected its
//! `AF_UNIX` socket. Daemon callers use a synchronous API, but plugin operation
//! serialization is forbidden: the connection itself can carry many in-flight
//! operation requests. A dedicated reader thread routes reply frames by
//! `message_id`; self-managed plugin operations can also service
//! plugin-originated callback requests on the same socket before their final
//! operation reply arrives. Concurrent callback-capable operations are routed by
//! `parent_message_id` in the callback body.

#[path = "transport_frame_io.rs"]
mod frame_io;
#[path = "transport_pending.rs"]
mod pending;

use std::os::unix::net::UnixStream;
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::Duration;

use eos_plugin::{PluginError, PpcDirection, PpcEnvelope};
use serde_json::json;

use self::frame_io::FrameWriter;
use self::pending::{CallbackHandler, PendingCalls};
use crate::PpcError;

pub use self::frame_io::read_frame;

/// A connected plugin service's PPC client: a synchronous request/reply façade
/// over the service socket, multiplexed by a background reader thread.
pub struct PpcClient {
    writer: FrameWriter,
    pending: PendingCalls,
}

impl std::fmt::Debug for PpcClient {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.debug_struct("PpcClient").finish_non_exhaustive()
    }
}

impl PpcClient {
    /// Wrap a connected service `stream`, spawning the reply-routing reader.
    pub fn new(stream: UnixStream) -> Result<Self, PpcError> {
        let reader_stream = stream.try_clone()?;
        let writer = FrameWriter::new(stream);
        let pending = PendingCalls::default();
        spawn_reader_thread(reader_stream, writer.clone(), pending.clone())?;
        Ok(Self { writer, pending })
    }

    /// Send a request and await its reply (no callbacks serviced).
    pub fn round_trip(
        &self,
        request: &PpcEnvelope,
        timeout: Duration,
    ) -> Result<PpcEnvelope, PpcError> {
        self.send_request(request, timeout, None)
    }

    /// Send a request and await its reply, servicing plugin-originated callback
    /// requests with `handle_callback` until the final reply arrives.
    pub fn round_trip_with_callbacks<F>(
        &self,
        request: &PpcEnvelope,
        timeout: Duration,
        handle_callback: F,
    ) -> Result<PpcEnvelope, PpcError>
    where
        F: FnMut(PpcEnvelope) -> Result<PpcEnvelope, PpcError> + Send + 'static,
    {
        let callback = Arc::new(Mutex::new(handle_callback));
        let handler: CallbackHandler = Arc::new(move |frame| {
            let mut callback = callback
                .lock()
                .map_err(|_| PpcError::LockPoisoned("plugin ppc callback handler"))?;
            callback(frame)
        });
        self.send_request(request, timeout, Some(handler))
    }

    fn send_request(
        &self,
        request: &PpcEnvelope,
        timeout: Duration,
        callback_handler: Option<CallbackHandler>,
    ) -> Result<PpcEnvelope, PpcError> {
        if request.direction != PpcDirection::Request {
            return Err(PluginError::Ppc(
                "daemon PPC round trip requires a request envelope".to_owned(),
            )
            .into());
        }

        let message_id = request.message_id.clone();
        let reply_rx = self
            .pending
            .register(message_id.clone(), callback_handler)?;

        if let Err(err) = self.writer.write_with_timeout(request, timeout) {
            let _ = self.pending.discard(&message_id);
            return Err(err);
        }

        match reply_rx.recv_timeout(timeout) {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let _ = self.pending.discard(&message_id);
                Err(PluginError::Ppc(format!(
                    "timed out waiting for plugin PPC reply {message_id}"
                ))
                .into())
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(PluginError::Ppc(format!(
                "plugin PPC reply channel closed for {message_id}"
            ))
            .into()),
        }
    }
}

fn spawn_reader_thread(
    mut stream: UnixStream,
    writer: FrameWriter,
    pending: PendingCalls,
) -> Result<(), PpcError> {
    thread::Builder::new()
        .name("eos-plugin-ppc-reader".to_owned())
        .spawn(move || reader_loop(&mut stream, &writer, &pending))?;
    Ok(())
}

fn reader_loop(stream: &mut UnixStream, writer: &FrameWriter, pending: &PendingCalls) {
    loop {
        let frame = match frame_io::read_envelope(stream) {
            Ok(frame) => frame,
            Err(err) => {
                pending.fail_all(err.to_string());
                return;
            }
        };

        match frame.direction {
            PpcDirection::Reply => pending.complete_reply(frame),
            PpcDirection::Request => handle_callback(frame, writer, pending),
        }
    }
}

fn handle_callback(frame: PpcEnvelope, writer: &FrameWriter, pending: &PendingCalls) {
    let callback_message_id = frame.message_id.clone();
    let (owner_id, handler) = match pending.callback_handler_for_frame(&frame) {
        Ok(found) => found,
        Err(message) => {
            let _ = write_callback_error(writer, &callback_message_id, &message);
            return;
        }
    };

    match handler(frame) {
        Ok(reply) => {
            if reply.direction != PpcDirection::Reply {
                pending.fail_one(
                    &owner_id,
                    "plugin PPC callback response must use reply direction".to_owned(),
                );
                return;
            }
            if reply.message_id != callback_message_id {
                pending.fail_one(
                    &owner_id,
                    format!(
                        "plugin PPC callback response message_id {} did not match callback {}",
                        reply.message_id, callback_message_id
                    ),
                );
                return;
            }
            if let Err(err) = writer.write(&reply) {
                pending.fail_one(&owner_id, err.to_string());
            }
        }
        Err(err) => {
            let message = err.to_string();
            let _ = write_callback_error(writer, &callback_message_id, &message);
            pending.fail_one(&owner_id, message);
        }
    }
}

fn write_callback_error(
    writer: &FrameWriter,
    callback_message_id: &str,
    message: &str,
) -> Result<(), PpcError> {
    let body = json!({
        "success": false,
        "error": {
            "kind": "ppc_callback_error",
            "message": message,
        },
    });
    writer.write(&PpcEnvelope {
        message_id: callback_message_id.to_owned(),
        direction: PpcDirection::Reply,
        op: "reply".to_owned(),
        body: body.to_string(),
    })
}
