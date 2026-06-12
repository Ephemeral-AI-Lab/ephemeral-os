use eos_trace::{subscriber::registry_with_trace_layer, SpanKind, TraceId, TraceSpoolLayer};
use tracing::{event, span, Level};

#[test]
fn closes_root_into_one_request_trace() {
    let trace_id = TraceId::parse("trace-layer").expect("trace id");
    let layer = TraceSpoolLayer::new();
    let subscriber = registry_with_trace_layer(layer.clone());

    tracing::subscriber::with_default(subscriber, || {
        let root = span!(
            Level::INFO,
            "op_request",
            trace_id = trace_id.as_str(),
            request_id = "request-layer",
            span_kind = "op_request",
        );
        let _root_guard = root.enter();
        event!(
            Level::INFO,
            event = "request_received",
            op = "sandbox.ready"
        );
        let dispatch = span!(Level::INFO, "dispatch", span_kind = "dispatch");
        dispatch.in_scope(|| {
            event!(Level::INFO, event = "op_resolved", op = "sandbox.ready");
            let op = span!(
                Level::INFO,
                "op.runtime.ready",
                span_kind = "operation",
                op = "sandbox.runtime.ready",
            );
            op.in_scope(|| {
                event!(Level::INFO, event = "ready_checked", ready = true);
            });
        });
    });

    let record = layer
        .take_finished(&trace_id)
        .expect("finished request trace");

    assert_eq!(record.trace_id, trace_id);
    assert_eq!(record.spans.len(), 3);
    assert_eq!(record.spans[0].kind, SpanKind::OpRequest);
    assert_eq!(record.spans[1].kind, SpanKind::Dispatch);
    assert_eq!(record.spans[2].kind, SpanKind::Operation);
    assert_eq!(record.spans[2].name, "op.runtime.ready");
    assert_eq!(
        record.spans[1].parent_span_id,
        Some(record.spans[0].span_id)
    );
    assert_eq!(
        record.spans[2].parent_span_id,
        Some(record.spans[1].span_id)
    );
    assert_eq!(record.events.len(), 3);
    assert!(layer.take_finished(&record.trace_id).is_none());
}
