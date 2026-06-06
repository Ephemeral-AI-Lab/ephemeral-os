//! Engine telemetry events emitted for one agent run.

use eos_audit::{AuditEvent, AuditNode, AuditSource};
use eos_audit::{AGENT_RUN_COMPLETED, OS_RESOURCE_SAMPLED};
use eos_types::{JsonObject, SystemClock};
use serde_json::{json, Value};

use crate::query::{QueryContext, QueryExitReason};
use crate::runtime::EngineRunHandles;

use super::resource_sample::capture_process_resource_sample;

pub(crate) fn publish_agent_run_completed(
    handles: &EngineRunHandles,
    ctx: &QueryContext,
    duration_ms: f64,
    error: Option<&str>,
) {
    let mut section = JsonObject::new();
    section.insert("duration_ms".to_owned(), json!(duration_ms));
    section.insert(
        "status".to_owned(),
        json!(if error.is_some() { "error" } else { "ok" }),
    );
    section.insert(
        "exit_reason".to_owned(),
        json!(ctx.exit_reason.map(exit_reason_value)),
    );
    if let Some(error) = error {
        section.insert("error".to_owned(), json!(error));
    }

    let mut payload = JsonObject::new();
    payload.insert("agent_run".to_owned(), Value::Object(section));
    publish_audit_event(handles, ctx, AGENT_RUN_COMPLETED, payload);
}

pub(crate) fn publish_os_resource_sampled(handles: &EngineRunHandles, ctx: &QueryContext) {
    if !handles.audit.enabled() {
        return;
    }
    let Some(sample) = capture_process_resource_sample() else {
        return;
    };

    let mut payload = JsonObject::new();
    payload.insert(
        "os_resource".to_owned(),
        Value::Object(sample.into_payload()),
    );
    publish_audit_event(handles, ctx, OS_RESOURCE_SAMPLED, payload);
}

fn publish_audit_event(
    handles: &EngineRunHandles,
    ctx: &QueryContext,
    event_type: &str,
    payload: JsonObject,
) {
    let event = AuditEvent::new(
        AuditSource::Engine,
        event_type,
        agent_run_audit_node(ctx),
        payload,
        &SystemClock,
    );
    if let Err(err) = handles.audit.publish(&event) {
        tracing::warn!(
            error = %err,
            agent_run_id = ctx.agent_run_id.as_str(),
            event_type,
            "obs publish failed"
        );
    }
}

fn agent_run_audit_node(ctx: &QueryContext) -> AuditNode {
    let mut node = AuditNode::builder()
        .agent_name(ctx.agent_name.clone())
        .agent_run_id(ctx.agent_run_id.clone());
    if let Some(request_id) = &ctx.tool_metadata.request_id {
        node = node.request_id(request_id.clone());
    }
    if let Some(task_id) = ctx
        .task_id
        .clone()
        .or_else(|| ctx.tool_metadata.task_id.clone())
    {
        node = node.task_id(task_id);
    }
    if let Some(sandbox_id) = &ctx.tool_metadata.sandbox_id {
        node = node.sandbox_id(sandbox_id.clone());
    }
    node.build()
}

const fn exit_reason_value(reason: QueryExitReason) -> &'static str {
    match reason {
        QueryExitReason::ToolStop => "tool_stop",
        QueryExitReason::TerminalNotSubmitted => "terminal_not_submitted",
    }
}
