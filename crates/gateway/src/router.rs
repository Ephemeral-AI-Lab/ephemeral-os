use std::time::Instant;

use protocol::{HostGatewayErrorKind, ProtocolErrorKind};
use serde_json::{json, Value};

use crate::engine::Engine;
use crate::wire::{elapsed_us, error_response_for, ok_response, ClientRequest};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Surface {
    Client,
    Operator,
}

impl Surface {
    pub(crate) const fn label(self) -> &'static str {
        match self {
            Self::Client => "client",
            Self::Operator => "operator",
        }
    }
}

pub(crate) fn handle(engine: &dyn Engine, surface: Surface, request: &ClientRequest) -> Value {
    match request.op.as_str() {
        "host.sandbox.acquire" => match engine.acquire(&request.trace, &request.args) {
            Ok(sandbox_id) => ok_response(request, json!({"sandbox_id": sandbox_id})),
            Err(err) => error_response_for(
                request,
                HostGatewayErrorKind::TraceUnavailable.as_str(),
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
            match engine.release(sandbox_id, &request.trace, &request.args) {
                Ok(true) => ok_response(request, json!({"sandbox_id": sandbox_id})),
                Ok(false) => unknown_sandbox(request, sandbox_id),
                Err(err) => error_response_for(
                    request,
                    HostGatewayErrorKind::TraceUnavailable.as_str(),
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
        "host.image_profiles.list" => host_value_response(
            request,
            engine.image_profiles_list(&request.trace, &request.args),
        ),
        "host.trace.requests" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.trace_requests(trace, args)
            })
        }
        "host.trace.show" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.trace_show(trace, args)
            })
        }
        "host.trace.verify" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.trace_verify(trace, args)
            })
        }
        "host.image.list" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.image_list(trace, args)
            })
        }
        "host.image.pull" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.image_pull(trace, args)
            })
        }
        "host.container.list" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.container_list(trace, args)
            })
        }
        "host.container.start" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.container_start(trace, args)
            })
        }
        "host.container.adopt" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.container_adopt(trace, args)
            })
        }
        "host.container.stop" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.container_stop(trace, args)
            })
        }
        "host.container.remove" => {
            operator_host_value(surface, engine, request, |engine, trace, args| {
                engine.container_remove(trace, args)
            })
        }
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
                return forbidden_socket(engine, surface, request);
            }
            forward(engine, request, true)
        }
        "sandbox.runtime.ready" | "sandbox.trace.export" | "sandbox.trace.export_ack" => {
            forbidden_socket(engine, surface, request)
        }
        _ => unknown_op(engine, surface, request),
    }
}

fn operator_host_value(
    surface: Surface,
    engine: &dyn Engine,
    request: &ClientRequest,
    call: impl FnOnce(&dyn Engine, &host::ForwardTraceContext, &Value) -> anyhow::Result<Value>,
) -> Value {
    if surface != Surface::Operator {
        return forbidden_socket(engine, surface, request);
    }
    host_value_response(request, call(engine, &request.trace, &request.args))
}

fn forward(engine: &dyn Engine, request: &ClientRequest, mutates_state: bool) -> Value {
    let Some(sandbox_id) = request.sandbox_id.as_deref() else {
        return error_response_for(
            request,
            ProtocolErrorKind::InvalidRequest.as_str(),
            "sandbox_id is required for this op",
        );
    };
    let mut trace = request.trace.clone();
    trace.push_gateway_event(
        "gateway.route",
        "route_selected",
        json!({
            "op": request.op,
            "sandbox_id": sandbox_id,
            "route": "daemon",
            "mutates_state": mutates_state,
        }),
    );
    trace.push_gateway_event(
        "gateway.route",
        "engine_forward_started",
        json!({
            "op": request.op,
            "sandbox_id": sandbox_id,
            "mutates_state": mutates_state,
        }),
    );
    let trace_for_result = trace.clone();
    let started = Instant::now();
    match engine.forward(host::HostForwardRequest {
        sandbox_id,
        mutates_state,
        op: &request.op,
        invocation_id: &request.invocation_id,
        args: &request.args,
        trace,
    }) {
        Some(Ok(mut response)) => {
            engine.record_trace_event(
                sandbox_id,
                &trace_for_result,
                "gateway.route",
                "engine_forward_finished",
                json!({
                    "op": request.op,
                    "sandbox_id": sandbox_id,
                    "duration_us": elapsed_us(started),
                }),
            );
            host::strip_trace_sidecar(&mut response);
            response
        }
        Some(Err(err)) => {
            let (kind, message) = match err {
                host::ForwardError::TraceUnavailable(e) => (
                    HostGatewayErrorKind::TraceUnavailable.as_str(),
                    e.to_string(),
                ),
                host::ForwardError::UncertainOutcome(m) => {
                    (HostGatewayErrorKind::UncertainOutcome.as_str(), m)
                }
                host::ForwardError::SandboxUnavailable(m) => {
                    (HostGatewayErrorKind::SandboxUnavailable.as_str(), m)
                }
            };
            engine.record_trace_event(
                sandbox_id,
                &trace_for_result,
                "gateway.route",
                "engine_forward_failed",
                json!({"op": request.op, "sandbox_id": sandbox_id, "error_kind": kind, "duration_us": elapsed_us(started)}),
            );
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

fn forbidden_socket(engine: &dyn Engine, surface: Surface, request: &ClientRequest) -> Value {
    record_route_rejection(
        engine,
        surface,
        request,
        ProtocolErrorKind::Forbidden.as_str(),
    );
    error_response_for(
        request,
        ProtocolErrorKind::Forbidden.as_str(),
        &format!("op {} is not served on this socket", request.op),
    )
}

fn unknown_op(engine: &dyn Engine, surface: Surface, request: &ClientRequest) -> Value {
    record_route_rejection(
        engine,
        surface,
        request,
        ProtocolErrorKind::UnknownOp.as_str(),
    );
    error_response_for(
        request,
        ProtocolErrorKind::UnknownOp.as_str(),
        &format!("unknown op: {}", request.op),
    )
}

fn record_route_rejection(
    engine: &dyn Engine,
    surface: Surface,
    request: &ClientRequest,
    error_kind: &str,
) {
    if let Some(sandbox_id) = request.sandbox_id.as_deref() {
        engine.record_trace_event(
            sandbox_id,
            &request.trace,
            "gateway.route",
            "route_rejected",
            json!({
                "op": request.op,
                "route": "rejected",
                "surface": surface.label(),
                "error_kind": error_kind,
            }),
        );
    }
}

fn unknown_sandbox(request: &ClientRequest, sandbox_id: &str) -> Value {
    error_response_for(
        request,
        HostGatewayErrorKind::UnknownSandbox.as_str(),
        &format!("unknown sandbox: {sandbox_id}"),
    )
}
