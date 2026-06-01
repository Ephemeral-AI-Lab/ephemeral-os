//! Daemon-side PPC request/reply transport.
//!
//! This is the synchronous boundary the daemon uses once a plugin service has
//! connected its AF_UNIX socket. It deliberately does one thing: send exactly
//! one [`eos_plugin::PpcEnvelope`] and wait for the matching reply envelope.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::time::Duration;

use eos_plugin::{PluginError, PpcDirection, PpcEnvelope};

use crate::error::DaemonError;

pub(crate) const DEFAULT_PLUGIN_PPC_TIMEOUT_MS: u64 = 5_000;

const MAX_PPC_FRAME_BYTES: usize = eos_protocol::MAX_REQUEST_BYTES;

#[derive(Debug)]
pub(crate) struct PpcClient {
    pub(crate) stream: UnixStream,
}

impl PpcClient {
    pub(crate) fn round_trip(
        &mut self,
        request: &PpcEnvelope,
        timeout: Duration,
    ) -> Result<PpcEnvelope, DaemonError> {
        if request.direction != PpcDirection::Request {
            return Err(PluginError::Ppc(
                "daemon PPC round trip requires a request envelope".to_owned(),
            )
            .into());
        }
        self.stream.set_write_timeout(Some(timeout))?;
        self.stream.set_read_timeout(Some(timeout))?;
        self.stream.write_all(&request.encode()?)?;
        self.stream.flush()?;

        let reply = PpcEnvelope::decode(&read_frame(&mut self.stream)?)?;
        if reply.direction != PpcDirection::Reply {
            return Err(
                PluginError::Ppc("plugin PPC reply must use reply direction".to_owned()).into(),
            );
        }
        if reply.message_id != request.message_id {
            return Err(PluginError::Ppc(format!(
                "plugin PPC reply message_id {} did not match request {}",
                reply.message_id, request.message_id
            ))
            .into());
        }
        Ok(reply)
    }
}

pub(crate) fn read_frame(stream: &mut UnixStream) -> Result<Vec<u8>, DaemonError> {
    let mut bytes = Vec::new();
    let mut one = [0_u8; 1];
    loop {
        let read = stream.read(&mut one)?;
        if read == 0 {
            return Err(
                PluginError::Ppc("plugin PPC stream closed before reply".to_owned()).into(),
            );
        }
        bytes.push(one[0]);
        if one[0] == b'\n' {
            return Ok(bytes);
        }
        if bytes.len() >= MAX_PPC_FRAME_BYTES {
            return Err(PluginError::Ppc(format!(
                "plugin PPC reply exceeds {MAX_PPC_FRAME_BYTES} byte limit"
            ))
            .into());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;

    #[test]
    fn ppc_client_round_trip_requires_matching_reply() {
        let (client_stream, mut server_stream) = UnixStream::pair().expect("unix stream pair");
        let server = thread::spawn(move || {
            let request = PpcEnvelope::decode(&read_frame(&mut server_stream).expect("request"))
                .expect("decode request");
            let reply = PpcEnvelope {
                message_id: request.message_id,
                direction: PpcDirection::Reply,
                op: "reply".to_owned(),
                body: r#"{"success":true}"#.to_owned(),
            };
            server_stream
                .write_all(&reply.encode().expect("encode reply"))
                .expect("write reply");
        });

        let mut client = PpcClient {
            stream: client_stream,
        };
        let reply = client
            .round_trip(
                &PpcEnvelope {
                    message_id: "msg-1".to_owned(),
                    direction: PpcDirection::Request,
                    op: "plugin.echo.ping".to_owned(),
                    body: r#"{"value":1}"#.to_owned(),
                },
                Duration::from_secs(1),
            )
            .expect("round trip");

        assert_eq!(reply.message_id, "msg-1");
        assert_eq!(reply.body, r#"{"success":true}"#);
        server.join().expect("server thread");
    }

    #[test]
    fn ppc_client_rejects_mismatched_message_id() {
        let (client_stream, mut server_stream) = UnixStream::pair().expect("unix stream pair");
        let server = thread::spawn(move || {
            let _request = PpcEnvelope::decode(&read_frame(&mut server_stream).expect("request"))
                .expect("decode request");
            let reply = PpcEnvelope {
                message_id: "different".to_owned(),
                direction: PpcDirection::Reply,
                op: "reply".to_owned(),
                body: "{}".to_owned(),
            };
            server_stream
                .write_all(&reply.encode().expect("encode reply"))
                .expect("write reply");
        });

        let mut client = PpcClient {
            stream: client_stream,
        };
        let err = client
            .round_trip(
                &PpcEnvelope {
                    message_id: "msg-1".to_owned(),
                    direction: PpcDirection::Request,
                    op: "plugin.echo.ping".to_owned(),
                    body: "{}".to_owned(),
                },
                Duration::from_secs(1),
            )
            .expect_err("mismatched reply should fail");

        assert!(err.to_string().contains("did not match request msg-1"));
        server.join().expect("server thread");
    }
}
