use std::time::Instant;

use anyhow::{anyhow, Result};
use serde_json::{json, Value};

use crate::trace_store::{ResponseMissingInput, ResponsePersistedInput, TraceEventInput};

use super::response::duration_ms;
use super::{ForwardTraceContext, SandboxHost};

impl SandboxHost {
    pub fn record_trace_event(
        &self,
        sandbox_id: &str,
        trace: &ForwardTraceContext,
        module: &str,
        event: &str,
        details: Value,
    ) {
        let _ = self
            .trace_store
            .append_trace_event_or_loss(TraceEventInput {
                sandbox_id,
                trace_id: &trace.trace_id,
                request_id: Some(&trace.request_id),
                span_id: None,
                module,
                event,
                details,
            });
    }

    pub(crate) fn record_operator_trace_read(
        &self,
        sandbox_id: Option<&str>,
        trace: &ForwardTraceContext,
        op: &str,
        args: &Value,
        outcome: Value,
    ) {
        let _ = self
            .trace_store
            .append_trace_event_or_loss(TraceEventInput {
                sandbox_id: sandbox_id.unwrap_or("_host"),
                trace_id: &trace.trace_id,
                request_id: Some(&trace.request_id),
                span_id: None,
                module: "host.trace_query",
                event: "operator_read",
                details: json!({
                    "op": op,
                    "args": trace::budget::redact_for_audit(args.clone()),
                    "outcome": outcome,
                }),
            });
    }

    pub(crate) fn record_host_gateway_events(&self, sandbox_id: &str, trace: &ForwardTraceContext) {
        for event in &trace.gateway_events {
            self.record_host_lifecycle_event(
                sandbox_id,
                trace,
                &event.event,
                json!({"module": event.module.clone(), "details": event.details.clone()}),
            );
        }
    }

    pub(crate) fn record_host_lifecycle_event(
        &self,
        sandbox_id: &str,
        trace: &ForwardTraceContext,
        event: &str,
        details: Value,
    ) {
        let _ = self
            .trace_store
            .append_trace_event_or_loss(TraceEventInput {
                sandbox_id,
                trace_id: &trace.trace_id,
                request_id: Some(&trace.request_id),
                span_id: None,
                module: "host.lifecycle",
                event,
                details,
            });
    }

    fn record_host_response(
        &self,
        sandbox_id: &str,
        trace: &ForwardTraceContext,
        response: &Value,
        started: Instant,
    ) -> Result<()> {
        let raw_response_bytes = serde_json::to_vec(response)?;
        self.trace_store
            .record_response_persisted(ResponsePersistedInput {
                sandbox_id,
                trace_id: &trace.trace_id,
                request_id: &trace.request_id,
                response,
                raw_response_bytes: &raw_response_bytes,
                host_rtt_ms: duration_ms(started.elapsed()),
            })?;
        Ok(())
    }

    pub(crate) fn record_host_response_or_missing(
        &self,
        sandbox_id: &str,
        trace: &ForwardTraceContext,
        response: &Value,
        started: Instant,
    ) -> Result<()> {
        if let Err(err) = self.record_host_response(sandbox_id, trace, response, started) {
            let message = format!("host response persistence failed after lifecycle result: {err}");
            let _ = self
                .trace_store
                .record_response_missing(ResponseMissingInput {
                    sandbox_id,
                    trace_id: &trace.trace_id,
                    request_id: &trace.request_id,
                    status: "uncertain",
                    error_kind: "trace_response_persist_failed",
                    message: &message,
                });
            return Err(anyhow!(message));
        }
        Ok(())
    }
}
