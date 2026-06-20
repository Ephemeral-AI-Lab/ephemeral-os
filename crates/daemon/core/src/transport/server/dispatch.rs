use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc as std_mpsc, Arc};

use super::DaemonServer;
use crate::error::DaemonError;
use crate::wire::{decode_value, ErrorKind, Request, WireMessage};

impl DaemonServer {
    pub(super) async fn dispatch_bytes(&self, bytes: Vec<u8>, is_tcp: bool) -> serde_json::Value {
        let value = match serde_json::from_slice::<serde_json::Value>(&bytes) {
            Ok(value) => value,
            Err(err) => {
                return crate::dispatcher::error_response(
                    ErrorKind::BadJson,
                    crate::wire::ProtocolError::from(err).to_string(),
                    serde_json::json!({}),
                )
            }
        };
        let value = if is_tcp {
            match self.strip_tcp_auth(value) {
                Ok(authenticated) => {
                    if let Err(err) =
                        enforce_tcp_visibility(&authenticated.value, authenticated.authority)
                    {
                        return crate::dispatcher::error_response(
                            err.wire_kind(),
                            err.to_string(),
                            serde_json::json!({}),
                        );
                    }
                    authenticated.value
                }
                Err(err) => {
                    return crate::dispatcher::error_response(
                        err.wire_kind(),
                        err.to_string(),
                        serde_json::json!({}),
                    )
                }
            }
        } else {
            value
        };
        let protocol_version_value = value
            .get("args")
            .and_then(|args| args.get(crate::wire::DAEMON_PROTOCOL_FIELD));
        if let Some(response) = protocol_version_error(protocol_version_value) {
            return response;
        }
        match decode_value(value) {
            Ok(WireMessage::Request(request)) => self.dispatch_request(request).await,
            Ok(_) => crate::dispatcher::error_response(
                ErrorKind::InvalidRequest,
                "request must include op, invocation_id, and args",
                serde_json::json!({}),
            ),
            Err(err) => crate::dispatcher::error_response(
                ErrorKind::BadJson,
                err.to_string(),
                serde_json::json!({}),
            ),
        }
    }

    async fn dispatch_request(&self, request: Request) -> serde_json::Value {
        let invocation_id = request.invocation_id.clone();
        let caller_id = trimmed_string(&request.args, "caller_id");
        let op = request.op.clone();
        let registry = Arc::clone(&self.invocation_registry);
        let (start_tx, start_rx) = std_mpsc::channel::<()>();
        let task_started = Arc::new(AtomicBool::new(false));
        let registered_started = Arc::clone(&task_started);
        let task = tokio::task::spawn_blocking(move || {
            let _ = start_rx.recv();
            task_started.store(true, Ordering::SeqCst);
            crate::dispatcher::dispatch(&request)
        });
        registry.register_blocking(
            &invocation_id,
            task.abort_handle(),
            registered_started,
            &caller_id,
        );
        let _ = start_tx.send(());
        let response = match task.await {
            Ok(response) => response,
            Err(err) if err.is_cancelled() => crate::dispatcher::error_response(
                ErrorKind::InternalError,
                "daemon invocation cancelled",
                serde_json::json!({"op": op}),
            ),
            Err(err) => crate::dispatcher::error_response(
                ErrorKind::InternalError,
                format!("daemon invocation failed: {err}"),
                serde_json::json!({"op": op}),
            ),
        };
        registry.deregister(&invocation_id);
        response
    }

    fn strip_tcp_auth(
        &self,
        mut value: serde_json::Value,
    ) -> Result<AuthenticatedTcpRequest, DaemonError> {
        let expected_forward = configured_token(self.config.forward_auth_token.as_deref());
        let expected_raw = configured_token(self.config.auth_token.as_deref());
        let forward_token = value
            .as_object_mut()
            .and_then(|object| object.remove(crate::wire::DAEMON_FORWARD_AUTH_FIELD))
            .and_then(|value| value.as_str().map(str::to_owned));
        let raw_token = value
            .as_object_mut()
            .and_then(|object| object.remove(crate::wire::DAEMON_AUTH_FIELD))
            .and_then(|value| value.as_str().map(str::to_owned));

        if let Some(expected) = expected_forward {
            if forward_token.as_deref() == Some(expected) {
                return Ok(AuthenticatedTcpRequest {
                    value,
                    authority: TcpAuthority::HostForward,
                });
            }
            if forward_token.is_some() {
                return Err(DaemonError::Unauthorized);
            }
        }

        if let Some(expected) = expected_raw {
            if raw_token.as_deref() != Some(expected) {
                return Err(DaemonError::Unauthorized);
            }
            return Ok(AuthenticatedTcpRequest {
                value,
                authority: TcpAuthority::Raw,
            });
        }

        if expected_forward.is_some() {
            return Err(DaemonError::Unauthorized);
        }
        Ok(AuthenticatedTcpRequest {
            value,
            authority: TcpAuthority::Raw,
        })
    }
}

struct AuthenticatedTcpRequest {
    value: serde_json::Value,
    authority: TcpAuthority,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TcpAuthority {
    Raw,
    HostForward,
}

fn configured_token(token: Option<&str>) -> Option<&str> {
    token.filter(|token| !token.is_empty())
}

fn enforce_tcp_visibility(
    value: &serde_json::Value,
    authority: TcpAuthority,
) -> Result<(), DaemonError> {
    if authority == TcpAuthority::HostForward {
        return Ok(());
    }
    let Some(op) = value.get("op").and_then(serde_json::Value::as_str) else {
        return Ok(());
    };
    if is_known_non_public_op(op) {
        return Err(DaemonError::Forbidden(format!(
            "raw daemon TCP may not invoke non-public op {op}"
        )));
    }
    Ok(())
}

fn is_known_non_public_op(op: &str) -> bool {
    matches!(op, "sandbox.runtime.ready" | "sandbox.run.cancel_all")
}

/// Transport-level caller extraction for in-flight registry keys; runs before
/// any operation parse, so it deliberately applies no default-caller fallback.
fn trimmed_string(args: &serde_json::Value, key: &str) -> String {
    args.get(key)
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned()
}

pub(crate) fn protocol_version_error(
    value: Option<&serde_json::Value>,
) -> Option<serde_json::Value> {
    let Some(value) = value else {
        return Some(crate::dispatcher::error_response(
            ErrorKind::InvalidRequest,
            "daemon protocol version is required",
            serde_json::json!({
                "expected": crate::wire::DAEMON_PROTOCOL_VERSION,
                "found": serde_json::Value::Null,
            }),
        ));
    };
    match value.as_i64() {
        Some(crate::wire::DAEMON_PROTOCOL_VERSION) => None,
        Some(found) => Some(crate::dispatcher::error_response(
            ErrorKind::InvalidRequest,
            "unsupported daemon protocol version",
            serde_json::json!({
                "expected": crate::wire::DAEMON_PROTOCOL_VERSION,
                "found": found,
            }),
        )),
        None => Some(crate::dispatcher::error_response(
            ErrorKind::InvalidRequest,
            "daemon protocol version must be an integer",
            serde_json::json!({
                "expected": crate::wire::DAEMON_PROTOCOL_VERSION,
                "found": value,
            }),
        )),
    }
}
