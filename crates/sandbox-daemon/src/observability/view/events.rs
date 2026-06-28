use sandbox_observability::RawFilter;
use sandbox_protocol::{Request, Response};
use serde_json::{json, Value};

use crate::observability::DaemonObservability;

pub(super) fn events_view_response(
    observability: Option<&DaemonObservability>,
    request: &Request,
) -> Response {
    let Some(observability) = observability else {
        return super::observability_unconfigured();
    };
    let filter = match event_filter(request) {
        Ok(filter) => filter,
        Err(response) => return response,
    };
    let last_n = match request.optional_u64("last_n") {
        Ok(last_n) => last_n,
        Err(response) => return response,
    };
    let mut events = observability.events(filter);
    if let Some(last_n) = last_n {
        let keep = usize::try_from(last_n)
            .unwrap_or(usize::MAX)
            .min(events.len());
        events.drain(..events.len() - keep);
    }
    let events = serde_json::to_value(events).unwrap_or_else(|_| Value::Array(Vec::new()));
    Response::ok(json!({ "view": "events", "events": events }))
}

fn event_filter(request: &Request) -> Result<RawFilter, Response> {
    Ok(RawFilter {
        name: optional_filter(request, "name")?,
        since_ms: since_ms(request)?,
        ..RawFilter::default()
    })
}

fn optional_filter(request: &Request, field: &str) -> Result<Option<String>, Response> {
    Ok(request
        .optional_string(field)?
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty()))
}

fn since_ms(request: &Request) -> Result<i64, Response> {
    Ok(request
        .optional_u64("since_ms")?
        .map(|value| i64::try_from(value).unwrap_or(i64::MAX))
        .unwrap_or(0))
}
