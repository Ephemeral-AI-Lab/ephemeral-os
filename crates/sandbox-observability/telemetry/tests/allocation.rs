//! Allocation conformance lives in its own test binary so the counting window
//! cannot observe allocations made by unrelated parallel tests.

use std::alloc::{GlobalAlloc, Layout, System};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};

use sandbox_observability_telemetry::{
    Attrs, RawFilter, Reader, Record, Sample, Sink, DEFAULT_MAX_DISK_BYTES, MAX_LINE_BYTES,
    MAX_RESPONSE_BYTES,
};
use serde_json::json;

struct CountingAllocator;

static ENABLED: AtomicBool = AtomicBool::new(false);
static ALLOCATIONS: AtomicU64 = AtomicU64::new(0);
static LIVE_BYTES: AtomicUsize = AtomicUsize::new(0);
static PEAK_BYTES: AtomicUsize = AtomicUsize::new(0);

#[global_allocator]
static ALLOCATOR: CountingAllocator = CountingAllocator;

// SAFETY: every allocation operation is delegated unchanged to the system
// allocator. The atomics only observe successful calls and never affect the
// returned pointer, layout, or allocator ordering requirements.
unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // SAFETY: `layout` is passed through from the caller unchanged.
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() && ENABLED.load(Ordering::SeqCst) {
            record_allocation(layout.size());
        }
        pointer
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        // SAFETY: `layout` is passed through from the caller unchanged.
        let pointer = unsafe { System.alloc_zeroed(layout) };
        if !pointer.is_null() && ENABLED.load(Ordering::SeqCst) {
            record_allocation(layout.size());
        }
        pointer
    }

    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        if ENABLED.load(Ordering::SeqCst) {
            subtract_live(layout.size());
        }
        // SAFETY: `pointer` and `layout` came from the delegated allocator.
        unsafe { System.dealloc(pointer, layout) };
    }

    unsafe fn realloc(&self, pointer: *mut u8, old: Layout, new_size: usize) -> *mut u8 {
        // SAFETY: the inputs are passed through from the caller unchanged.
        let replacement = unsafe { System.realloc(pointer, old, new_size) };
        if !replacement.is_null() && ENABLED.load(Ordering::SeqCst) {
            ALLOCATIONS.fetch_add(1, Ordering::SeqCst);
            if new_size >= old.size() {
                add_live(new_size - old.size());
            } else {
                subtract_live(old.size() - new_size);
            }
        }
        replacement
    }
}

fn record_allocation(bytes: usize) {
    ALLOCATIONS.fetch_add(1, Ordering::SeqCst);
    add_live(bytes);
}

fn add_live(bytes: usize) {
    let live = LIVE_BYTES.fetch_add(bytes, Ordering::SeqCst) + bytes;
    let mut peak = PEAK_BYTES.load(Ordering::SeqCst);
    while live > peak {
        match PEAK_BYTES.compare_exchange_weak(peak, live, Ordering::SeqCst, Ordering::SeqCst) {
            Ok(_) => break,
            Err(current) => peak = current,
        }
    }
}

fn subtract_live(bytes: usize) {
    let _ = LIVE_BYTES.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |live| {
        Some(live.saturating_sub(bytes))
    });
}

fn measure<T>(operation: impl FnOnce() -> T) -> (T, u64, usize) {
    ALLOCATIONS.store(0, Ordering::SeqCst);
    LIVE_BYTES.store(0, Ordering::SeqCst);
    PEAK_BYTES.store(0, Ordering::SeqCst);
    ENABLED.store(true, Ordering::SeqCst);
    let value = operation();
    ENABLED.store(false, Ordering::SeqCst);
    (
        value,
        ALLOCATIONS.load(Ordering::SeqCst),
        PEAK_BYTES.load(Ordering::SeqCst),
    )
}

fn temp_log(label: &str) -> PathBuf {
    std::env::temp_dir()
        .join(format!(
            "sandbox-obs-allocation-{label}-{}",
            std::process::id()
        ))
        .join("observability.ndjson")
}

fn sample(blob: String) -> Record {
    let metrics: Attrs = json!({ "blob": blob })
        .as_object()
        .cloned()
        .expect("object");
    Record::Sample(Sample {
        ts: 1,
        scope: "sandbox".to_owned(),
        metrics,
    })
}

#[test]
fn encoder_and_streaming_reader_have_fixed_allocation_bounds() {
    let append_path = temp_log("append");
    let sink = Sink::with_budget(append_path.clone(), MAX_LINE_BYTES, DEFAULT_MAX_DISK_BYTES);
    sink.append(&sample("prime".to_owned()))
        .expect("prime sink");
    let normal = sample("normal".to_owned());
    let truncated = sample("🦀\\\"".repeat(MAX_LINE_BYTES));

    let (normal_result, normal_allocations, _) = measure(|| sink.append(&normal));
    normal_result.expect("normal append");
    assert_eq!(normal_allocations, 0, "normal encoder allocation count");

    let (truncated_result, truncated_allocations, _) = measure(|| sink.append(&truncated));
    truncated_result.expect("truncated append");
    assert_eq!(
        truncated_allocations, 0,
        "truncation marker encoder allocation count"
    );

    let line = serde_json::to_vec(&sample("reader".repeat(8))).expect("serialize fixture");
    let mut peaks = Vec::new();
    let mut allocations = Vec::new();
    for count in [1_usize, 10, 1_000] {
        let path = temp_log(&format!("reader-{count}"));
        fs::create_dir_all(path.parent().expect("parent")).expect("create reader parent");
        let mut file = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&path)
            .expect("open fixture");
        for _ in 0..count {
            file.write_all(&line).expect("write line");
            file.write_all(b"\n").expect("write newline");
        }
        drop(file);
        let reader = Reader::with_limits(
            path.clone(),
            path.with_extension("absent"),
            MAX_LINE_BYTES,
            1,
            256 * 1024,
        );
        let (result, allocation_count, peak) = measure(|| reader.raw(RawFilter::default()));
        assert_eq!(result.len(), 1);
        assert!(
            peak < MAX_LINE_BYTES + 8 * 1024,
            "{count} records used {peak} peak bytes"
        );
        peaks.push(peak);
        allocations.push(allocation_count);
        fs::remove_dir_all(path.parent().expect("parent")).expect("cleanup reader fixture");
    }
    assert_eq!(
        allocations[1], allocations[2],
        "discarded history lines must not allocate: {allocations:?}"
    );
    assert!(
        peaks[1].abs_diff(peaks[2]) <= 128,
        "reader peak grew from 10 to 1,000 records: {peaks:?}"
    );
    assert!(
        peaks[0].abs_diff(peaks[1]) <= 2 * 1024,
        "one fixed incoming-candidate cost was exceeded: {peaks:?}"
    );

    let event_line = br#"{"kind":"event","ts":1,"trace":"reader","name":"reader.event","attrs":{"payload":"fixed"}}"#;
    let mut event_allocations = Vec::new();
    for count in [10_usize, 1_000] {
        let path = temp_log(&format!("events-{count}"));
        fs::create_dir_all(path.parent().expect("parent")).expect("create event parent");
        let mut file = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&path)
            .expect("open event fixture");
        for _ in 0..count {
            file.write_all(event_line).expect("write event line");
            file.write_all(b"\n").expect("write event newline");
        }
        drop(file);
        let reader = Reader::with_limits(
            path.clone(),
            path.with_extension("absent"),
            MAX_LINE_BYTES,
            1,
            256 * 1024,
        );
        let (result, allocation_count, peak) = measure(|| reader.events(RawFilter::default()));
        assert_eq!(result.len(), 1);
        assert!(
            peak < MAX_LINE_BYTES + 8 * 1024,
            "{count} event records used {peak} peak bytes"
        );
        event_allocations.push(allocation_count);
        fs::remove_dir_all(path.parent().expect("parent")).expect("cleanup event fixture");
    }
    assert_eq!(
        event_allocations[0], event_allocations[1],
        "discarded event history lines must not allocate: {event_allocations:?}"
    );

    let mut raw_arena_allocations = Vec::new();
    for count in [500_usize, 10_000] {
        let path = temp_log(&format!("raw-events-{count}"));
        fs::create_dir_all(path.parent().expect("parent")).expect("create raw event parent");
        let mut file = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&path)
            .expect("open raw event fixture");
        for _ in 0..count {
            file.write_all(event_line).expect("write raw event line");
            file.write_all(b"\n").expect("write raw event newline");
        }
        drop(file);
        let reader = Reader::with_limits(
            path.clone(),
            path.with_extension("absent"),
            MAX_LINE_BYTES,
            500,
            256 * 1024,
        );
        let (records, allocation_count, peak) =
            measure(|| reader.raw_json_events(RawFilter::default()));
        assert_eq!(records.len(), 500);
        assert!(
            peak <= MAX_RESPONSE_BYTES + 2 * MAX_LINE_BYTES,
            "{count} raw event records used {peak} peak bytes"
        );
        let array_len = records.json_array_len(None, 256 * 1024);
        let mut encoded = String::with_capacity(array_len);
        records.write_json_array(&mut encoded, None, 256 * 1024);
        assert_eq!(encoded.len(), array_len);
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&encoded)
                .expect("raw array parses")
                .as_array()
                .map(Vec::len),
            Some(500)
        );
        raw_arena_allocations.push(allocation_count);
        fs::remove_dir_all(path.parent().expect("parent")).expect("cleanup raw event fixture");
    }
    assert_eq!(
        raw_arena_allocations[0], raw_arena_allocations[1],
        "discarded raw event history must not allocate: {raw_arena_allocations:?}"
    );

    let maximum_path = temp_log("reader-maximum-store");
    fs::create_dir_all(maximum_path.parent().expect("parent")).expect("create maximum parent");
    let mut maximum = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&maximum_path)
        .expect("open maximum fixture");
    let mut written = 0_u64;
    while written + (line.len() + 1) as u64 <= DEFAULT_MAX_DISK_BYTES {
        maximum.write_all(&line).expect("write maximum line");
        maximum.write_all(b"\n").expect("write maximum newline");
        written += (line.len() + 1) as u64;
    }
    drop(maximum);
    let maximum_reader = Reader::with_limits(
        maximum_path.clone(),
        maximum_path.with_extension("absent"),
        MAX_LINE_BYTES,
        1,
        256 * 1024,
    );
    let (result, _, peak) = measure(|| maximum_reader.raw(RawFilter::default()));
    assert_eq!(result.len(), 1);
    assert!(
        peak < MAX_LINE_BYTES + 8 * 1024,
        "maximum store peak {peak}"
    );

    fs::remove_dir_all(append_path.parent().expect("append parent")).expect("cleanup append");
    fs::remove_dir_all(maximum_path.parent().expect("maximum parent")).expect("cleanup maximum");
}
