use anyhow::Result;
use serde_json::{json, Value};

use super::args::required_string_arg;
use super::{
    ForwardTraceContext, SandboxHost, HOST_TRACE_REQUESTS, HOST_TRACE_SHOW, HOST_TRACE_VERIFY,
    TRACE_SHOW_DEFAULT_SECTION_LIMIT, TRACE_SHOW_MAX_SECTION_LIMIT,
};

impl SandboxHost {
    pub fn trace_requests(&self, trace: &ForwardTraceContext, args: &Value) -> Result<Value> {
        let sandbox_id = args.get("sandbox_id").and_then(Value::as_str);
        let limit = args
            .get("limit")
            .and_then(Value::as_u64)
            .unwrap_or(100)
            .clamp(1, 1_000) as usize;
        let result = self.trace_store.recent_requests(sandbox_id, limit);
        self.record_operator_trace_read(
            sandbox_id,
            trace,
            HOST_TRACE_REQUESTS,
            args,
            match &result {
                Ok(requests) => json!({"status": "ok", "result_count": requests.len()}),
                Err(err) => json!({"status": "error", "message": err.to_string()}),
            },
        );
        Ok(result.map(|requests| json!({"requests": requests}))?)
    }

    pub fn trace_show(&self, trace: &ForwardTraceContext, args: &Value) -> Result<Value> {
        let trace_id = required_string_arg(args, "trace_id")?;
        let section_limit = trace_show_section_limit(args);
        let query_limit = section_limit.saturating_add(1);
        let result: std::result::Result<Value, crate::trace_store::TraceStoreError> = (|| {
            let (requests, requests_truncated) = trim_limited(
                self.trace_store
                    .requests_for_trace_limited(trace_id, query_limit)?,
                section_limit,
            );
            let (spans, spans_truncated) = trim_limited(
                self.trace_store
                    .spans_for_trace_limited(trace_id, query_limit)?,
                section_limit,
            );
            let (events, events_truncated) = trim_limited(
                self.trace_store
                    .events_for_trace_limited(trace_id, query_limit)?,
                section_limit,
            );
            let (resources, resources_truncated) = trim_limited(
                self.trace_store
                    .resources_for_trace_limited(trace_id, query_limit)?,
                section_limit,
            );
            let (links, links_truncated) = trim_limited(
                self.trace_store
                    .links_for_trace_limited(trace_id, query_limit)?,
                section_limit,
            );
            let (audit_entries, audit_entries_truncated) = trim_limited(
                self.trace_store
                    .audit_entries_for_trace_limited(trace_id, query_limit)?,
                section_limit,
            );
            Ok(json!({
                "trace_id": trace_id,
                "limits": {
                    "per_section": section_limit,
                },
                "counts": {
                    "requests": requests.len(),
                    "spans": spans.len(),
                    "events": events.len(),
                    "resources": resources.len(),
                    "links": links.len(),
                    "audit_entries": audit_entries.len(),
                },
                "truncated": {
                    "requests": requests_truncated,
                    "spans": spans_truncated,
                    "events": events_truncated,
                    "resources": resources_truncated,
                    "links": links_truncated,
                    "audit_entries": audit_entries_truncated,
                },
                "requests": requests,
                "spans": spans,
                "events": events,
                "resources": resources,
                "links": links,
                "audit_entries": audit_entries,
            }))
        })();
        self.record_operator_trace_read(
            args.get("sandbox_id").and_then(Value::as_str),
            trace,
            HOST_TRACE_SHOW,
            args,
            match &result {
                Ok(value) => json!({
                    "status": "ok",
                    "request_count": value["requests"].as_array().map_or(0, Vec::len),
                    "span_count": value["spans"].as_array().map_or(0, Vec::len),
                    "event_count": value["events"].as_array().map_or(0, Vec::len),
                    "audit_entry_count": value["audit_entries"].as_array().map_or(0, Vec::len),
                    "truncated": value["truncated"].clone(),
                    "limit": section_limit,
                }),
                Err(err) => json!({"status": "error", "message": err.to_string()}),
            },
        );
        Ok(result?)
    }

    pub fn trace_verify(&self, trace: &ForwardTraceContext, args: &Value) -> Result<Value> {
        let trace_id = args.get("trace_id").and_then(Value::as_str);
        let report = self.trace_store.verify_audit(trace_id)?;
        self.record_operator_trace_read(
            args.get("sandbox_id").and_then(Value::as_str),
            trace,
            HOST_TRACE_VERIFY,
            args,
            json!({
                "status": "ok",
                "ok": report.ok,
                "scope": report.scope.as_str(),
                "checked_entries": report.checked_entries,
                "first_error_kind": report.first_error.as_ref().map(|failure| failure.kind.as_str()),
            }),
        );
        Ok(serde_json::to_value(report)?)
    }
}

fn trace_show_section_limit(args: &Value) -> usize {
    args.get("limit")
        .and_then(Value::as_u64)
        .unwrap_or(u64::try_from(TRACE_SHOW_DEFAULT_SECTION_LIMIT).unwrap_or(u64::MAX))
        .clamp(
            1,
            u64::try_from(TRACE_SHOW_MAX_SECTION_LIMIT).unwrap_or(u64::MAX),
        ) as usize
}

fn trim_limited<T>(mut rows: Vec<T>, limit: usize) -> (Vec<T>, bool) {
    let truncated = rows.len() > limit;
    if truncated {
        rows.truncate(limit);
    }
    (rows, truncated)
}
