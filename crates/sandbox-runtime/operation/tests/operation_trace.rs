use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use sandbox_protocol::{CliOperationScope, Request};
use sandbox_runtime::layerstack::LayerStackService;
use sandbox_runtime::{OperationTrace, SandboxRuntimeOperations};
use serde_json::json;

mod support;
use support::{build_services, FakeWorkspaceService};

#[test]
fn operation_trace_records_call_order_and_parentage() {
    let trace = OperationTrace::new();
    {
        let _root = trace.enter("root");
        {
            let _child = trace.enter("child");
        }
        {
            let _sibling = trace.enter("sibling");
        }
    }

    let completed = trace.complete();
    let root = span(&completed, "root");
    let child = span(&completed, "child");
    let sibling = span(&completed, "sibling");
    assert_eq!(root.call_index, 0);
    assert_eq!(root.parent_call_index, None);
    assert_eq!(child.call_index, 1);
    assert_eq!(child.parent_call_index, Some(0));
    assert_eq!(sibling.call_index, 2);
    assert_eq!(sibling.parent_call_index, Some(0));
}

#[test]
fn operation_trace_closes_span_on_early_return() {
    fn return_early(trace: &OperationTrace) {
        let _span = trace.enter("early_return");
        if std::hint::black_box(true) {
            return;
        }
        let _not_reached = trace.enter("not_reached");
    }

    let trace = OperationTrace::new();
    return_early(&trace);

    let completed = trace.complete();
    let span = span(&completed, "early_return");
    assert_eq!(span.status, "ok");
    assert!(span.duration_ms >= 0.0);
}

#[test]
fn operation_trace_marks_span_closed_during_panic_unwind() {
    let trace = OperationTrace::new();
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _span = trace.enter("panic_span");
        panic!("intentional trace test panic");
    }));

    assert!(result.is_err());
    let completed = trace.complete();
    assert_eq!(span(&completed, "panic_span").status, "panic");
}

#[test]
fn operation_trace_records_selected_squash_span_set() -> Result<(), Box<dyn std::error::Error>> {
    let services = build_services(Arc::new(FakeWorkspaceService::new()));
    let operations =
        SandboxRuntimeOperations::new(Arc::clone(&services.command), layerstack_service()?);
    let trace = OperationTrace::new();

    let response = sandbox_runtime::dispatch_operation(
        &operations,
        &Request::new(
            "squash",
            "req-squash",
            CliOperationScope::system(),
            json!({}),
        ),
        Some(&trace),
    )
    .into_json_value();

    assert_eq!(response["squashed"], false);
    let completed = trace.complete();
    let mut spans = completed.spans.iter().collect::<Vec<_>>();
    spans.sort_by_key(|span| span.call_index);
    let names = spans
        .iter()
        .map(|span| span.method_name)
        .collect::<Vec<_>>();
    assert_eq!(
        names,
        vec![
            "dispatch_operation",
            "squash::dispatch",
            "LayerStackService::squash",
        ]
    );
    assert_eq!(spans[0].parent_call_index, None);
    assert_eq!(spans[1].parent_call_index, Some(0));
    assert_eq!(spans[2].parent_call_index, Some(1));
    Ok(())
}

fn span<'a>(
    trace: &'a sandbox_runtime::CompletedOperationTrace,
    method_name: &str,
) -> &'a sandbox_runtime::CompletedOperationSpan {
    trace
        .spans
        .iter()
        .find(|span| span.method_name == method_name)
        .expect("span recorded")
}

fn layerstack_service() -> Result<Arc<LayerStackService>, Box<dyn std::error::Error>> {
    let base = temp_root();
    let root = base.join("layer-stack");
    let workspace = base.join("workspace");
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&workspace)?;
    sandbox_runtime_layerstack::build_workspace_base(&root, &workspace, false)?;
    Ok(Arc::new(LayerStackService::new(root)?))
}

fn temp_root() -> PathBuf {
    static NEXT_TEST: AtomicU64 = AtomicU64::new(0);
    std::env::temp_dir().join(format!(
        "sandbox-runtime-operation-trace-{}-{}",
        std::process::id(),
        NEXT_TEST.fetch_add(1, Ordering::Relaxed)
    ))
}
