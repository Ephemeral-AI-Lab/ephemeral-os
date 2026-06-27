//! SpanRegistry + TerminalHook: `launch` parks on `Ok` and exposes a child
//! context; `on_terminal` records one async span (key attrs folded); `launch`
//! cancels on `Err` (no bogus span); cross-thread terminal works; never-parked
//! ids no-op; the Drop sweep records leftovers as Cancelled; `NoopHook` is inert.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use sandbox_observability::record::proc;
use sandbox_observability::{
    NoopHook, Observer, ObserverConfig, RawFilter, Reader, Sink, Span, SpanRegistry, SpanStatus,
    TerminalHook, TraceContext,
};
use serde_json::{json, Value};

static NEXT: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, PartialEq, Eq, Hash)]
struct FakeId(String);

fn temp_log(label: &str) -> PathBuf {
    std::env::temp_dir()
        .join(format!(
            "sandbox-obs-registry-{label}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
        .join("observability.ndjson")
}

fn observer(path: &Path) -> Observer {
    Observer::new(
        ObserverConfig {
            proc: proc::DAEMON,
            enabled: true,
        },
        Sink::new(path.to_path_buf()),
    )
}

fn ctx(trace: &str, parent: Option<&str>) -> TraceContext {
    TraceContext {
        trace: Arc::from(trace),
        parent: parent.map(Arc::from),
    }
}

fn spans(path: &Path) -> Vec<Span> {
    let reader = Reader::new(path.to_path_buf(), path.with_extension("ndjson.absent"));
    reader
        .raw(RawFilter::default())
        .iter()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .filter(|value| value["kind"] == "span")
        .map(|value| serde_json::from_value::<Span>(value).expect("span"))
        .collect()
}

#[test]
fn launch_ok_parks_and_terminal_records_async_span() {
    let path = temp_log("launch-ok");
    let registry = SpanRegistry::<FakeId>::new(observer(&path));
    let id = FakeId("e1".to_owned());

    let child = registry
        .launch(
            id.clone(),
            Some(ctx("req", Some("d-5"))),
            "command.exec",
            |child| {
                let child = child.expect("ctx Some ⇒ child context");
                assert_eq!(child.trace.as_ref(), "req", "trace preserved");
                assert!(child.parent.is_some(), "child parent = the minted span id");
                Ok::<TraceContext, ()>(child)
            },
        )
        .expect("ok body");
    assert!(child.parent.is_some());

    // The engine calls the terminal edge right after the work finishes.
    registry.on_terminal(&id, SpanStatus::Completed, Some(0));

    let spans = spans(&path);
    assert_eq!(spans.len(), 1);
    let span = &spans[0];
    assert_eq!(span.name, "command.exec");
    assert_eq!(span.trace, "req");
    assert_eq!(
        span.parent.as_deref(),
        Some("d-5"),
        "async span parent stamped at launch"
    );
    assert_eq!(span.status, SpanStatus::Completed);
    assert_eq!(span.attrs["exit_code"], 0, "blanket hook folds exit_code");
}

#[test]
fn launch_err_cancels_and_writes_no_span() {
    let path = temp_log("launch-err");
    let registry = SpanRegistry::<FakeId>::new(observer(&path));
    let id = FakeId("e2".to_owned());

    let result: Result<(), &str> =
        registry.launch(id.clone(), Some(ctx("req", None)), "command.exec", |_| {
            Err("boom")
        });
    assert!(result.is_err());

    // Terminal after a cancelled launch is a no-op (id no longer parked).
    registry.on_terminal(&id, SpanStatus::Completed, None);
    assert!(
        spans(&path).is_empty(),
        "failed launch writes no bogus cancelled span"
    );
}

#[test]
fn terminal_from_another_thread_records() {
    let path = temp_log("cross-thread");
    let registry = Arc::new(SpanRegistry::<FakeId>::new(observer(&path)));
    let id = FakeId("e3".to_owned());
    registry
        .launch(
            id.clone(),
            Some(ctx("req", None)),
            "command.exec",
            Ok::<Option<TraceContext>, ()>,
        )
        .expect("ok");

    let worker_registry = Arc::clone(&registry);
    let worker_id = id.clone();
    std::thread::spawn(move || {
        worker_registry.on_terminal(&worker_id, SpanStatus::Completed, Some(0));
    })
    .join()
    .expect("worker");

    assert_eq!(
        spans(&path).len(),
        1,
        "Sink serializes the cross-thread terminal write"
    );
}

#[test]
fn record_and_cancel_on_never_parked_id_noop() {
    let path = temp_log("never-parked");
    let registry = SpanRegistry::<FakeId>::new(observer(&path));
    registry.record(
        &FakeId("ghost".to_owned()),
        SpanStatus::Completed,
        json!({}),
    );
    assert!(
        spans(&path).is_empty(),
        "record on a never-parked id writes nothing"
    );
}

#[test]
fn drop_sweep_records_leftovers_as_cancelled() {
    let path = temp_log("drop-sweep");
    {
        let registry = SpanRegistry::<FakeId>::new(observer(&path));
        registry
            .launch(
                FakeId("leak".to_owned()),
                Some(ctx("req", None)),
                "command.exec",
                Ok::<Option<TraceContext>, ()>,
            )
            .expect("ok");
        // Never terminal — leaked until the shutdown sweep.
    }

    let spans = spans(&path);
    assert_eq!(spans.len(), 1);
    assert_eq!(
        spans[0].status,
        SpanStatus::Cancelled,
        "Drop sweep records leftovers"
    );
    assert_eq!(spans[0].name, "command.exec");
}

#[test]
fn noop_hook_is_inert_for_any_key() {
    let hook = NoopHook;
    // Compiles and is callable for an arbitrary K; records nothing anywhere.
    TerminalHook::on_terminal(
        &hook,
        &FakeId("x".to_owned()),
        SpanStatus::Completed,
        Some(0),
    );
    TerminalHook::on_terminal(&hook, &7_u32, SpanStatus::Cancelled, None);
}

#[test]
fn disabled_registry_parks_nothing() {
    let path = temp_log("disabled");
    let observer = Observer::new(
        ObserverConfig {
            proc: proc::DAEMON,
            enabled: false,
        },
        Sink::new(path.clone()),
    );
    let registry = SpanRegistry::<FakeId>::new(observer);
    let id = FakeId("e".to_owned());
    registry
        .launch(
            id.clone(),
            Some(ctx("req", None)),
            "command.exec",
            Ok::<Option<TraceContext>, ()>,
        )
        .expect("ok");
    registry.on_terminal(&id, SpanStatus::Completed, Some(0));
    assert!(!path.exists(), "disabled registry is a near-free no-op");
}
