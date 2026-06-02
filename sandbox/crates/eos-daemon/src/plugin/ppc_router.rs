//! Daemon-side PPC request/reply transport.
//!
//! This is the synchronous boundary the daemon uses once a plugin service has
//! connected its `AF_UNIX` socket. The normal path sends exactly one
//! [`eos_plugin::PpcEnvelope`] and waits for the matching reply envelope. The
//! self-managed plugin path can additionally service plugin-originated callback
//! requests on the same socket before the final operation reply arrives.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::time::Duration;

use eos_plugin::{PluginError, PpcDirection, PpcEnvelope};

use crate::error::DaemonError;

pub(super) const DEFAULT_PLUGIN_PPC_TIMEOUT_MS: u64 = 5_000;

const MAX_PPC_FRAME_BYTES: usize = eos_protocol::MAX_REQUEST_BYTES;

#[derive(Debug)]
pub(super) struct PpcClient {
    pub(super) stream: UnixStream,
}

impl PpcClient {
    pub(super) fn round_trip(
        &mut self,
        request: &PpcEnvelope,
        timeout: Duration,
    ) -> Result<PpcEnvelope, DaemonError> {
        let request_id = request.message_id.clone();
        self.round_trip_with_callbacks(request, timeout, move |callback| {
            Err(PluginError::Ppc(format!(
                "unexpected plugin PPC callback {} while waiting for reply {}",
                callback.op, request_id
            ))
            .into())
        })
    }

    pub(super) fn round_trip_with_callbacks<F>(
        &mut self,
        request: &PpcEnvelope,
        timeout: Duration,
        mut handle_callback: F,
    ) -> Result<PpcEnvelope, DaemonError>
    where
        F: FnMut(PpcEnvelope) -> Result<PpcEnvelope, DaemonError>,
    {
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

        loop {
            let frame = PpcEnvelope::decode(&read_frame(&mut self.stream)?)?;
            match frame.direction {
                PpcDirection::Reply => {
                    if frame.message_id != request.message_id {
                        return Err(PluginError::Ppc(format!(
                            "plugin PPC reply message_id {} did not match request {}",
                            frame.message_id, request.message_id
                        ))
                        .into());
                    }
                    return Ok(frame);
                }
                PpcDirection::Request => {
                    let callback_message_id = frame.message_id.clone();
                    let callback_reply = handle_callback(frame)?;
                    if callback_reply.direction != PpcDirection::Reply {
                        return Err(PluginError::Ppc(
                            "plugin PPC callback response must use reply direction".to_owned(),
                        )
                        .into());
                    }
                    if callback_reply.message_id != callback_message_id {
                        return Err(PluginError::Ppc(format!(
                            "plugin PPC callback response message_id {} did not match callback {}",
                            callback_reply.message_id, callback_message_id
                        ))
                        .into());
                    }
                    self.stream.write_all(&callback_reply.encode()?)?;
                    self.stream.flush()?;
                }
            }
        }
    }
}

pub(super) fn read_frame(stream: &mut UnixStream) -> Result<Vec<u8>, DaemonError> {
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

    type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn ppc_client_round_trip_requires_matching_reply() -> TestResult {
        let (client_stream, mut server_stream) = UnixStream::pair()?;
        let server = thread::spawn(move || -> TestResult {
            let request = PpcEnvelope::decode(&read_frame(&mut server_stream)?)?;
            let reply = PpcEnvelope {
                message_id: request.message_id,
                direction: PpcDirection::Reply,
                op: "reply".to_owned(),
                body: r#"{"success":true}"#.to_owned(),
            };
            server_stream.write_all(&reply.encode()?)?;
            Ok(())
        });

        let mut client = PpcClient {
            stream: client_stream,
        };
        let reply = client.round_trip(
            &PpcEnvelope {
                message_id: "msg-1".to_owned(),
                direction: PpcDirection::Request,
                op: "plugin.echo.ping".to_owned(),
                body: r#"{"value":1}"#.to_owned(),
            },
            Duration::from_secs(1),
        )?;

        assert_eq!(reply.message_id, "msg-1");
        assert_eq!(reply.body, r#"{"success":true}"#);
        join_server(server)?;
        Ok(())
    }

    #[test]
    fn ppc_client_rejects_mismatched_message_id() -> TestResult {
        let (client_stream, mut server_stream) = UnixStream::pair()?;
        let server = thread::spawn(move || -> TestResult {
            let _request = PpcEnvelope::decode(&read_frame(&mut server_stream)?)?;
            let reply = PpcEnvelope {
                message_id: "different".to_owned(),
                direction: PpcDirection::Reply,
                op: "reply".to_owned(),
                body: "{}".to_owned(),
            };
            server_stream.write_all(&reply.encode()?)?;
            Ok(())
        });

        let mut client = PpcClient {
            stream: client_stream,
        };
        let Err(err) = client.round_trip(
            &PpcEnvelope {
                message_id: "msg-1".to_owned(),
                direction: PpcDirection::Request,
                op: "plugin.echo.ping".to_owned(),
                body: "{}".to_owned(),
            },
            Duration::from_secs(1),
        ) else {
            return Err("mismatched reply unexpectedly succeeded".into());
        };

        assert!(err.to_string().contains("did not match request msg-1"));
        join_server(server)?;
        Ok(())
    }

    #[test]
    fn ppc_client_services_callback_before_final_reply() -> TestResult {
        let (client_stream, mut server_stream) = UnixStream::pair()?;
        let server = thread::spawn(move || -> TestResult {
            let request = PpcEnvelope::decode(&read_frame(&mut server_stream)?)?;
            assert_eq!(request.message_id, "msg-1");

            let callback = PpcEnvelope {
                message_id: "callback-1".to_owned(),
                direction: PpcDirection::Request,
                op: "daemon.occ.apply_changeset".to_owned(),
                body: r#"{"changes":[]}"#.to_owned(),
            };
            server_stream.write_all(&callback.encode()?)?;

            let callback_reply = PpcEnvelope::decode(&read_frame(&mut server_stream)?)?;
            assert_eq!(callback_reply.message_id, "callback-1");
            assert_eq!(callback_reply.direction, PpcDirection::Reply);
            assert_eq!(callback_reply.body, r#"{"published":[]}"#);

            let reply = PpcEnvelope {
                message_id: request.message_id,
                direction: PpcDirection::Reply,
                op: "reply".to_owned(),
                body: r#"{"success":true}"#.to_owned(),
            };
            server_stream.write_all(&reply.encode()?)?;
            Ok(())
        });

        let mut client = PpcClient {
            stream: client_stream,
        };
        let reply = client.round_trip_with_callbacks(
            &PpcEnvelope {
                message_id: "msg-1".to_owned(),
                direction: PpcDirection::Request,
                op: "plugin.lsp.apply".to_owned(),
                body: r#"{"path":"main.py"}"#.to_owned(),
            },
            Duration::from_secs(1),
            |callback| {
                assert_eq!(callback.message_id, "callback-1");
                assert_eq!(callback.op, "daemon.occ.apply_changeset");
                Ok(PpcEnvelope {
                    message_id: callback.message_id,
                    direction: PpcDirection::Reply,
                    op: "reply".to_owned(),
                    body: r#"{"published":[]}"#.to_owned(),
                })
            },
        )?;

        assert_eq!(reply.message_id, "msg-1");
        assert_eq!(reply.body, r#"{"success":true}"#);
        join_server(server)?;
        Ok(())
    }

    #[test]
    fn ppc_client_services_multiple_callbacks_before_final_reply() -> TestResult {
        let (client_stream, mut server_stream) = UnixStream::pair()?;
        let server = thread::spawn(move || -> TestResult {
            let request = PpcEnvelope::decode(&read_frame(&mut server_stream)?)?;
            assert_eq!(request.message_id, "msg-1");

            for index in 0..2 {
                let callback = PpcEnvelope {
                    message_id: format!("callback-{index}"),
                    direction: PpcDirection::Request,
                    op: "daemon.occ.apply_changeset".to_owned(),
                    body: format!(r#"{{"changes":[{{"path":"file-{index}.txt"}}]}}"#),
                };
                server_stream.write_all(&callback.encode()?)?;

                let callback_reply = PpcEnvelope::decode(&read_frame(&mut server_stream)?)?;
                assert_eq!(callback_reply.message_id, format!("callback-{index}"));
                assert_eq!(callback_reply.direction, PpcDirection::Reply);
                assert_eq!(
                    callback_reply.body,
                    format!(r#"{{"published":["file-{index}.txt"]}}"#)
                );
            }

            let reply = PpcEnvelope {
                message_id: request.message_id,
                direction: PpcDirection::Reply,
                op: "reply".to_owned(),
                body: r#"{"success":true,"callback_count":2}"#.to_owned(),
            };
            server_stream.write_all(&reply.encode()?)?;
            Ok(())
        });

        let mut callback_count = 0_usize;
        let mut client = PpcClient {
            stream: client_stream,
        };
        let reply = client.round_trip_with_callbacks(
            &PpcEnvelope {
                message_id: "msg-1".to_owned(),
                direction: PpcDirection::Request,
                op: "plugin.lsp.apply_multi".to_owned(),
                body: r#"{"paths":["file-0.txt","file-1.txt"]}"#.to_owned(),
            },
            Duration::from_secs(1),
            |callback| {
                let expected_id = format!("callback-{callback_count}");
                let expected_body =
                    format!(r#"{{"changes":[{{"path":"file-{callback_count}.txt"}}]}}"#);
                assert_eq!(callback.message_id, expected_id);
                assert_eq!(callback.op, "daemon.occ.apply_changeset");
                assert_eq!(callback.body, expected_body);
                let body = format!(r#"{{"published":["file-{callback_count}.txt"]}}"#);
                callback_count += 1;
                Ok(PpcEnvelope {
                    message_id: callback.message_id,
                    direction: PpcDirection::Reply,
                    op: "reply".to_owned(),
                    body,
                })
            },
        )?;

        assert_eq!(callback_count, 2);
        assert_eq!(reply.message_id, "msg-1");
        assert_eq!(reply.body, r#"{"success":true,"callback_count":2}"#);
        join_server(server)?;
        Ok(())
    }

    #[test]
    fn ppc_client_rejects_bad_callback_reply_message_id() -> TestResult {
        let (client_stream, mut server_stream) = UnixStream::pair()?;
        let server = thread::spawn(move || -> TestResult {
            let _request = PpcEnvelope::decode(&read_frame(&mut server_stream)?)?;
            let callback = PpcEnvelope {
                message_id: "callback-1".to_owned(),
                direction: PpcDirection::Request,
                op: "daemon.occ.apply_changeset".to_owned(),
                body: "{}".to_owned(),
            };
            server_stream.write_all(&callback.encode()?)?;
            Ok(())
        });

        let mut client = PpcClient {
            stream: client_stream,
        };
        let Err(err) = client.round_trip_with_callbacks(
            &PpcEnvelope {
                message_id: "msg-1".to_owned(),
                direction: PpcDirection::Request,
                op: "plugin.lsp.apply".to_owned(),
                body: "{}".to_owned(),
            },
            Duration::from_secs(1),
            |_callback| {
                Ok(PpcEnvelope {
                    message_id: "wrong".to_owned(),
                    direction: PpcDirection::Reply,
                    op: "reply".to_owned(),
                    body: "{}".to_owned(),
                })
            },
        ) else {
            return Err("bad callback reply id unexpectedly succeeded".into());
        };

        assert!(err
            .to_string()
            .contains("did not match callback callback-1"));
        join_server(server)?;
        Ok(())
    }

    fn join_server(server: thread::JoinHandle<TestResult>) -> TestResult {
        server
            .join()
            .unwrap_or_else(|_| Err(std::io::Error::other("server thread panicked").into()))
    }
}
