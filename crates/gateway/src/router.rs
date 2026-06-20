use protocol::{HostGatewayErrorKind, ProtocolErrorKind};
use serde_json::{json, Value};

use crate::engine::Engine;
use crate::wire::{error_response_for, ok_response, ClientRequest};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Surface {
    Client,
    Operator,
}

pub(crate) fn handle(engine: &dyn Engine, surface: Surface, request: &ClientRequest) -> Value {
    match request.op.as_str() {
        "host.sandbox.acquire" => match engine.acquire(&request.args) {
            Ok(sandbox_id) => ok_response(request, json!({"sandbox_id": sandbox_id})),
            Err(err) => error_response_for(
                request,
                HostGatewayErrorKind::HostOperationFailed.as_str(),
                &format!("acquire failed: {err:#}"),
            ),
        },
        "host.sandbox.list" => ok_response(request, json!({"sandboxes": engine.list()})),
        "host.sandbox.release" => {
            let Some(sandbox_id) = request.sandbox_id.as_deref() else {
                return error_response_for(
                    request,
                    ProtocolErrorKind::InvalidRequest.as_str(),
                    "sandbox_id is required for this op",
                );
            };
            match engine.release(sandbox_id, &request.args) {
                Ok(true) => ok_response(request, json!({"sandbox_id": sandbox_id})),
                Ok(false) => unknown_sandbox(request, sandbox_id),
                Err(err) => error_response_for(
                    request,
                    HostGatewayErrorKind::HostOperationFailed.as_str(),
                    &format!("release failed: {err:#}"),
                ),
            }
        }
        "host.sandbox.status" => {
            let Some(sandbox_id) = request.sandbox_id.as_deref() else {
                return error_response_for(
                    request,
                    ProtocolErrorKind::InvalidRequest.as_str(),
                    "sandbox_id is required for this op",
                );
            };
            match engine.status(sandbox_id) {
                Some(status) => ok_response(request, status),
                None => unknown_sandbox(request, sandbox_id),
            }
        }
        "host.image_profiles.list" => {
            host_value_response(request, engine.image_profiles_list(&request.args))
        }
        "host.image.list" => operator_host_value(surface, engine, request, |engine, args| {
            engine.image_list(args)
        }),
        "host.image.pull" => operator_host_value(surface, engine, request, |engine, args| {
            engine.image_pull(args)
        }),
        "host.container.list" => operator_host_value(surface, engine, request, |engine, args| {
            engine.container_list(args)
        }),
        "host.container.start" => operator_host_value(surface, engine, request, |engine, args| {
            engine.container_start(args)
        }),
        "host.container.adopt" => operator_host_value(surface, engine, request, |engine, args| {
            engine.container_adopt(args)
        }),
        "host.container.stop" => operator_host_value(surface, engine, request, |engine, args| {
            engine.container_stop(args)
        }),
        "host.container.remove" => operator_host_value(surface, engine, request, |engine, args| {
            engine.container_remove(args)
        }),
        "sandbox.call.heartbeat"
        | "sandbox.call.cancel"
        | "sandbox.command.exec"
        | "sandbox.command.write_stdin"
        | "sandbox.command.poll"
        | "sandbox.command.cancel"
        | "sandbox.command.collect_completed"
        | "sandbox.run.end" => forward(engine, request, true),
        "sandbox.call.count" | "sandbox.command.count" => forward(engine, request, false),
        "sandbox.run.cancel_all" => {
            if surface != Surface::Operator {
                return forbidden_socket(request);
            }
            forward(engine, request, true)
        }
        "sandbox.runtime.ready" => forbidden_socket(request),
        _ => unknown_op(request),
    }
}

fn operator_host_value(
    surface: Surface,
    engine: &dyn Engine,
    request: &ClientRequest,
    call: impl FnOnce(&dyn Engine, &Value) -> anyhow::Result<Value>,
) -> Value {
    if surface != Surface::Operator {
        return forbidden_socket(request);
    }
    host_value_response(request, call(engine, &request.args))
}

fn forward(engine: &dyn Engine, request: &ClientRequest, mutates_state: bool) -> Value {
    let Some(sandbox_id) = request.sandbox_id.as_deref() else {
        return error_response_for(
            request,
            ProtocolErrorKind::InvalidRequest.as_str(),
            "sandbox_id is required for this op",
        );
    };
    match engine.forward(host::HostForwardRequest {
        sandbox_id,
        mutates_state,
        op: &request.op,
        invocation_id: &request.invocation_id,
        args: &request.args,
    }) {
        Some(Ok(response)) => response,
        Some(Err(err)) => {
            let (kind, message) = match err {
                host::ForwardError::UncertainOutcome(m) => {
                    (HostGatewayErrorKind::UncertainOutcome.as_str(), m)
                }
                host::ForwardError::SandboxUnavailable(m) => {
                    (HostGatewayErrorKind::SandboxUnavailable.as_str(), m)
                }
            };
            error_response_for(request, kind, &message)
        }
        None => unknown_sandbox(request, sandbox_id),
    }
}

fn host_value_response(request: &ClientRequest, result: anyhow::Result<Value>) -> Value {
    match result {
        Ok(value) => ok_response(request, value),
        Err(err) => error_response_for(request, host_error_kind(&err), &err.to_string()),
    }
}

fn host_error_kind(err: &anyhow::Error) -> &'static str {
    let message = err.to_string();
    if message.ends_with(" is required") || message.ends_with(" must be a non-empty string") {
        ProtocolErrorKind::InvalidRequest.as_str()
    } else {
        HostGatewayErrorKind::HostOperationFailed.as_str()
    }
}

fn forbidden_socket(request: &ClientRequest) -> Value {
    error_response_for(
        request,
        ProtocolErrorKind::Forbidden.as_str(),
        &format!("op {} is not served on this socket", request.op),
    )
}

fn unknown_op(request: &ClientRequest) -> Value {
    error_response_for(
        request,
        ProtocolErrorKind::UnknownOp.as_str(),
        &format!("unknown op: {}", request.op),
    )
}

fn unknown_sandbox(request: &ClientRequest, sandbox_id: &str) -> Value {
    error_response_for(
        request,
        HostGatewayErrorKind::UnknownSandbox.as_str(),
        &format!("unknown sandbox: {sandbox_id}"),
    )
}
