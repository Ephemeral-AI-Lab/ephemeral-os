use std::sync::Arc;

use super::DaemonServer;
use crate::error::DaemonError;
use daemon_operation::OperationRequest;
use daemon_rpc_protocol::{
    decode_request_object, error_kind, ArgsPresence, DAEMON_AUTH_FIELD, DAEMON_FORWARD_AUTH_FIELD,
};
use serde_json::{Map, Value};

impl DaemonServer {
    pub(super) async fn dispatch_bytes(&self, bytes: Vec<u8>, is_tcp: bool) -> serde_json::Value {
        let value = match serde_json::from_slice::<serde_json::Value>(&bytes) {
            Ok(value) => value,
            Err(err) => {
                return super::error_response(
                    error_kind::BAD_JSON,
                    format!("bad json: {err}"),
                    serde_json::json!({}),
                );
            }
        };
        let value = if is_tcp {
            match self.strip_tcp_auth(value) {
                Ok(authenticated) => {
                    if let Err(err) =
                        enforce_tcp_visibility(&authenticated.value, authenticated.authority)
                    {
                        return super::error_response(
                            err.response_kind(),
                            err.to_string(),
                            serde_json::json!({}),
                        );
                    }
                    authenticated.value
                }
                Err(err) => {
                    return super::error_response(
                        err.response_kind(),
                        err.to_string(),
                        serde_json::json!({}),
                    );
                }
            }
        } else {
            value
        };
        match parse_request(value) {
            Ok((op, request_id, args)) => self.dispatch_request(op, request_id, args).await,
            Err(response) => response,
        }
    }

    async fn dispatch_request(
        &self,
        op: String,
        request_id: String,
        args: serde_json::Value,
    ) -> serde_json::Value {
        let op_for_error = op.clone();
        let operations = Arc::clone(&self.operations);
        let task = tokio::task::spawn_blocking(move || {
            daemon_operation::dispatch_operation(
                &operations,
                OperationRequest::new(&op, &request_id, &args),
            )
            .into_json_value()
        });
        let response = match task.await {
            Ok(response) => response,
            Err(err) if err.is_cancelled() => super::error_response(
                error_kind::INTERNAL_ERROR,
                "daemon request cancelled",
                serde_json::json!({"op": op_for_error}),
            ),
            Err(err) => super::error_response(
                error_kind::INTERNAL_ERROR,
                format!("daemon request failed: {err}"),
                serde_json::json!({"op": op_for_error}),
            ),
        };
        response
    }

    fn strip_tcp_auth(
        &self,
        mut value: serde_json::Value,
    ) -> Result<AuthenticatedTcpRequest, DaemonError> {
        let expected_forward = configured_token(self.config.forward_auth_token.as_deref());
        let expected_raw = configured_token(self.config.auth_token.as_deref());
        let (forward_token, raw_token) = match value.as_object_mut() {
            Some(object) => (
                remove_token(object, DAEMON_FORWARD_AUTH_FIELD, expected_forward),
                remove_token(object, DAEMON_AUTH_FIELD, expected_raw),
            ),
            None => (TokenMatch::Missing, TokenMatch::Missing),
        };

        if expected_forward.is_some() {
            if forward_token == TokenMatch::Matches {
                return Ok(AuthenticatedTcpRequest {
                    value,
                    authority: TcpAuthority::HostForward,
                });
            }
            if forward_token == TokenMatch::Mismatch {
                return Err(DaemonError::Unauthorized);
            }
        }

        if expected_raw.is_some() {
            if raw_token != TokenMatch::Matches {
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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TokenMatch {
    Missing,
    Mismatch,
    Matches,
}

fn remove_token(
    object: &mut Map<String, Value>,
    field: &str,
    expected: Option<&str>,
) -> TokenMatch {
    let Some(Value::String(token)) = object.remove(field) else {
        return TokenMatch::Missing;
    };
    if expected == Some(token.as_str()) {
        TokenMatch::Matches
    } else {
        TokenMatch::Mismatch
    }
}

struct AuthenticatedTcpRequest {
    value: Value,
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

fn enforce_tcp_visibility(value: &Value, authority: TcpAuthority) -> Result<(), DaemonError> {
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

fn parse_request(value: Value) -> Result<(String, String, Value), Value> {
    let Value::Object(object) = value else {
        return Err(super::error_response(
            error_kind::BAD_JSON,
            "request message must be a json object",
            serde_json::json!({}),
        ));
    };
    let request = decode_request_object(object, ArgsPresence::Required)
        .map_err(|err| invalid_request(err.message()))?;
    Ok((request.op, request.request_id, request.args))
}

fn invalid_request(message: impl Into<String>) -> serde_json::Value {
    super::error_response(error_kind::INVALID_REQUEST, message, serde_json::json!({}))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::parse_request;

    #[test]
    fn parse_request_preserves_request_fields() {
        let args = json!({
            "command": "echo hi",
            "payload": ["large-ish", "owned", "args"],
        });
        let request = json!({
            "op": "command.exec",
            "request_id": "req-1",
            "args": args.clone(),
        });

        let (op, request_id, parsed_args) = parse_request(request).expect("valid request parses");

        assert_eq!(op, "command.exec");
        assert_eq!(request_id, "req-1");
        assert_eq!(parsed_args, args);
    }

    #[test]
    fn parse_request_rejects_non_object_args() {
        let request = json!({
            "op": "command.exec",
            "request_id": "req-1",
            "args": "not an object",
        });

        let response = parse_request(request).expect_err("non-object args rejected");

        assert_eq!(response["status"], "error");
        assert_eq!(response["error"]["kind"], "invalid_request");
        assert_eq!(response["error"]["message"], "args must be an object");
    }
}
