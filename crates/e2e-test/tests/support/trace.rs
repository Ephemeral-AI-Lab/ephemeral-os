#![allow(dead_code)]

use anyhow::{bail, Context, Result};
use e2e_test::client::{decode_trace_sidecar_base64, take_trace_sidecar_checked};
use protocol::{OperationEnvelope, ResponseMeta, TraceRef};
use serde_json::Value;
use trace::{decode_trace_batch, TraceRecord};

pub(crate) fn operation_envelope(response: &Value) -> Result<OperationEnvelope<Value>> {
    serde_json::from_value(response.clone())
        .with_context(|| format!("decode OperationEnvelope from response: {response}"))
}

pub(crate) fn envelope_meta(response: &Value) -> Result<ResponseMeta> {
    serde_json::from_value(
        response
            .get("meta")
            .cloned()
            .with_context(|| format!("response missing envelope meta: {response}"))?,
    )
    .with_context(|| format!("decode ResponseMeta from response: {response}"))
}

pub(crate) fn envelope_result(response: &Value) -> Result<&Value> {
    response
        .get("result")
        .with_context(|| format!("response missing envelope result: {response}"))
}

pub(crate) fn envelope_status(response: &Value) -> Result<&str> {
    response
        .get("status")
        .and_then(Value::as_str)
        .with_context(|| format!("response missing envelope status: {response}"))
}

pub(crate) fn envelope_error_kind(response: &Value) -> Result<&str> {
    response
        .get("error")
        .and_then(|error| error.get("kind"))
        .and_then(Value::as_str)
        .with_context(|| format!("response missing envelope error kind: {response}"))
}

pub(crate) fn envelope_error_kind_or_status(response: &Value) -> Result<String> {
    if response.get("error").is_some() {
        Ok(envelope_error_kind(response)?.to_owned())
    } else {
        Ok(envelope_status(response)?.to_owned())
    }
}

pub(crate) fn trace_record(response: &Value) -> Result<TraceRecord> {
    let mut stripped = response.clone();
    let sidecar = take_trace_sidecar_checked(&mut stripped)
        .map_err(|err| anyhow::anyhow!("malformed trace sidecar {}: {response}", err.kind()))?
        .with_context(|| format!("response missing trace sidecar: {response}"))?;
    let batch = decode_trace_batch(&sidecar).context("decode trace sidecar")?;
    let mut records = batch.records;
    if records.len() != 1 {
        bail!(
            "expected one trace record in response sidecar, got {}",
            records.len()
        );
    }
    Ok(records.remove(0))
}

pub(crate) fn trace_export_records(response: &Value) -> Result<Vec<TraceRecord>> {
    let Some(encoded) = response.get("trace_batch_base64").and_then(Value::as_str) else {
        if response.get("record_count").and_then(Value::as_i64) == Some(0) {
            return Ok(Vec::new());
        }
        bail!("trace export missing trace_batch_base64: {response}");
    };
    let bytes = decode_trace_sidecar_base64(encoded).context("decode trace export batch")?;
    Ok(decode_trace_batch(&bytes)
        .context("decode trace export protobuf")?
        .records)
}

pub(crate) fn has_trace_event(
    record: &TraceRecord,
    module: &str,
    name: &str,
    predicate: impl Fn(&Value) -> bool,
) -> bool {
    record.events.iter().any(|event| {
        event.module == module && event.name == name && predicate(&event.details.value)
    })
}

fn assert_consistent_trace_ref(trace: &TraceRef, request_id: &str) -> Result<()> {
    if trace.trace_id.is_empty() {
        bail!("response trace ref did not include trace_id");
    }
    if request_id.is_empty() {
        bail!("response meta did not include request_id");
    }
    if let Some(trace_request_id) = trace.request_id.as_deref() {
        if trace_request_id != request_id {
            bail!(
                "response meta.request_id {request_id} did not match trace.request_id {trace_request_id}"
            );
        }
    }
    Ok(())
}
