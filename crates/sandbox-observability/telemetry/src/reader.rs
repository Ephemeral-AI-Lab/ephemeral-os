//! Streaming, bounded reads over the rotated and active event segments.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use serde::Serialize;
use serde_json::Value;

use crate::lines::for_each_complete_line;
use crate::record::{Attrs, Event, Record, Span, COUNTERS_METRIC_KEY, MAX_LINE_BYTES};
use crate::unix_now_ms;

pub const MAX_RESPONSE_RECORDS: usize = 500;
pub const MAX_RESPONSE_BYTES: usize = 256 * 1024;

pub struct Reader {
    primary: PathBuf,
    rotated: PathBuf,
    max_line_bytes: usize,
    max_records: usize,
    max_response_bytes: usize,
}

#[derive(Default, Clone, Debug)]
pub struct RawFilter {
    pub kind: Option<String>,
    pub name: Option<String>,
    pub trace: Option<String>,
    pub since_ms: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SpanNode {
    pub span: Span,
    pub offset_ms: f64,
    pub children: Vec<SpanNode>,
    pub events: Vec<EventNode>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct EventNode {
    pub offset_ms: f64,
    pub event: Event,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SampleDelta {
    pub ts: i64,
    pub scope: String,
    pub metrics: Attrs,
    pub deltas: Attrs,
    pub sample_delta_ms: Option<i64>,
}

struct Entry {
    record: Record,
    line: String,
}

impl Reader {
    #[must_use]
    pub fn new(primary: PathBuf, rotated: PathBuf) -> Self {
        Self::with_limits(
            primary,
            rotated,
            MAX_LINE_BYTES,
            MAX_RESPONSE_RECORDS,
            MAX_RESPONSE_BYTES,
        )
    }

    #[must_use]
    pub fn with_limits(
        primary: PathBuf,
        rotated: PathBuf,
        max_line_bytes: usize,
        max_records: usize,
        max_response_bytes: usize,
    ) -> Self {
        Self {
            primary,
            rotated,
            max_line_bytes: max_line_bytes.min(MAX_LINE_BYTES),
            max_records: max_records.min(MAX_RESPONSE_RECORDS),
            max_response_bytes: max_response_bytes.min(MAX_RESPONSE_BYTES),
        }
    }

    #[must_use]
    pub fn trace(&self, id: &str) -> Vec<SpanNode> {
        let mut entries = self.collect(|record| record_trace(record) == Some(id));
        loop {
            let (spans, events) = split_trace_entries(&entries);
            let forest = build_trace_forest(spans, events);
            if serialized_len(&forest) <= self.max_response_bytes || entries.is_empty() {
                return forest;
            }
            entries.remove(0);
        }
    }

    #[must_use]
    pub fn latest_root_trace(&self) -> Option<String> {
        let mut latest: Option<Span> = None;
        self.for_each_record(|record, _| {
            let Record::Span(span) = record else { return };
            if span.parent.is_some() {
                return;
            }
            if latest
                .as_ref()
                .is_none_or(|candidate| span_start(&span) > span_start(candidate))
            {
                latest = Some(span);
            }
        });
        latest.map(|span| span.trace)
    }

    #[must_use]
    pub fn samples(&self, scope: &str, window_ms: i64) -> Vec<SampleDelta> {
        let since = unix_now_ms().saturating_sub(window_ms);
        let entries = self.collect(|record| {
            matches!(record, Record::Sample(sample) if sample.scope == scope && sample.ts >= since)
        });
        let values = entries
            .into_iter()
            .filter_map(|entry| match entry.record {
                Record::Sample(sample) => Some((sample.ts, sample.metrics)),
                _ => None,
            })
            .collect();
        let mut samples = sample_deltas(scope, values);
        trim_serialized(&mut samples, self.max_records, self.max_response_bytes);
        samples
    }

    #[must_use]
    pub fn latest_samples(&self, scopes: &[&str]) -> HashMap<String, SampleDelta> {
        let requested: HashSet<&str> = scopes.iter().copied().collect();
        let mut samples: HashMap<String, Vec<(i64, Attrs, usize)>> = HashMap::new();
        let mut retained_bytes = 2_usize;
        self.for_each_record(|record, line| {
            let Record::Sample(sample) = record else {
                return;
            };
            if !requested.contains(sample.scope.as_str()) {
                return;
            }
            let latest = samples.entry(sample.scope).or_default();
            retained_bytes = retained_bytes.saturating_add(line.len() + 1);
            latest.push((sample.ts, sample.metrics, line.len() + 1));
            latest.sort_by_key(|(ts, _, _)| *ts);
            if latest.len() > 2 {
                let removed = latest.remove(0);
                retained_bytes = retained_bytes.saturating_sub(removed.2);
            }
            while samples.len() > self.max_records || retained_bytes > self.max_response_bytes {
                let Some(oldest) = samples
                    .iter()
                    .min_by_key(|(_, values)| values.last().map_or(i64::MIN, |(ts, _, _)| *ts))
                    .map(|(scope, _)| scope.clone())
                else {
                    break;
                };
                if let Some(removed) = samples.remove(&oldest) {
                    retained_bytes = retained_bytes
                        .saturating_sub(removed.iter().map(|(_, _, bytes)| *bytes).sum::<usize>());
                }
            }
        });
        let mut result: HashMap<String, SampleDelta> = samples
            .into_iter()
            .filter_map(|(scope, samples)| {
                sample_deltas(
                    &scope,
                    samples
                        .into_iter()
                        .map(|(ts, metrics, _)| (ts, metrics))
                        .collect(),
                )
                .pop()
                .map(|sample| (scope, sample))
            })
            .collect();
        while serialized_len(&result) > self.max_response_bytes && !result.is_empty() {
            let Some(oldest) = result
                .iter()
                .min_by_key(|(_, sample)| sample.ts)
                .map(|(scope, _)| scope.clone())
            else {
                break;
            };
            result.remove(&oldest);
        }
        result
    }

    #[must_use]
    pub fn events(&self, filter: RawFilter) -> Vec<Event> {
        let mut events: Vec<Event> = self
            .collect(
                |record| matches!(record, Record::Event(event) if event_matches(event, &filter)),
            )
            .into_iter()
            .filter_map(|entry| match entry.record {
                Record::Event(event) => Some(event),
                _ => None,
            })
            .collect();
        trim_serialized(&mut events, self.max_records, self.max_response_bytes);
        events
    }

    #[must_use]
    pub fn raw(&self, filter: RawFilter) -> Vec<String> {
        let mut lines: Vec<String> = self
            .collect(|record| raw_matches(record, &filter))
            .into_iter()
            .map(|entry| entry.line)
            .collect();
        trim_serialized(&mut lines, self.max_records, self.max_response_bytes);
        lines
    }

    fn collect(&self, matches: impl Fn(&Record) -> bool) -> Vec<Entry> {
        let mut entries = Vec::new();
        let mut retained_bytes = 2_usize;
        self.for_each_record(|record, line| {
            if !matches(&record) {
                return;
            }
            retained_bytes = retained_bytes.saturating_add(line.len() + 1);
            entries.push(Entry {
                record,
                line: line.to_owned(),
            });
            entries.sort_by_key(|entry| record_ts(&entry.record));
            while entries.len() > self.max_records || retained_bytes > self.max_response_bytes {
                if entries.is_empty() {
                    break;
                }
                let removed = entries.remove(0);
                retained_bytes = retained_bytes.saturating_sub(removed.line.len() + 1);
            }
        });
        entries
    }

    fn for_each_record(&self, mut visit: impl FnMut(Record, &str)) {
        for path in [&self.rotated, &self.primary] {
            let _ = for_each_complete_line(path, self.max_line_bytes, |line| {
                let Ok(text) = std::str::from_utf8(line) else {
                    return Ok(());
                };
                let Ok(record) = serde_json::from_slice::<Record>(line) else {
                    return Ok(());
                };
                visit(record, text);
                Ok(())
            });
        }
    }
}

fn serialized_len(value: &impl Serialize) -> usize {
    struct Count(usize);
    impl std::io::Write for Count {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0 = self.0.saturating_add(bytes.len());
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut count = Count(0);
    let _ = serde_json::to_writer(&mut count, value);
    count.0
}

fn trim_serialized<T: Serialize>(values: &mut Vec<T>, max_records: usize, max_bytes: usize) {
    while (values.len() > max_records || serialized_len(values) > max_bytes) && !values.is_empty() {
        values.remove(0);
    }
}

fn split_trace_entries(entries: &[Entry]) -> (Vec<Span>, Vec<Event>) {
    let mut spans = Vec::new();
    let mut events = Vec::new();
    for entry in entries {
        match &entry.record {
            Record::Span(span) => spans.push(span.clone()),
            Record::Event(event) => events.push(event.clone()),
            Record::Sample(_) => {}
        }
    }
    (spans, events)
}

fn record_ts(record: &Record) -> i64 {
    match record {
        Record::Span(span) => span.ts,
        Record::Event(event) => event.ts,
        Record::Sample(sample) => sample.ts,
    }
}

fn record_kind(record: &Record) -> &'static str {
    match record {
        Record::Span(_) => "span",
        Record::Event(_) => "event",
        Record::Sample(_) => "sample",
    }
}

fn record_name(record: &Record) -> Option<&str> {
    match record {
        Record::Span(span) => Some(&span.name),
        Record::Event(event) => Some(&event.name),
        Record::Sample(_) => None,
    }
}

fn record_trace(record: &Record) -> Option<&str> {
    match record {
        Record::Span(span) => Some(&span.trace),
        Record::Event(event) => Some(&event.trace),
        Record::Sample(_) => None,
    }
}

fn raw_matches(record: &Record, filter: &RawFilter) -> bool {
    record_ts(record) >= filter.since_ms
        && filter
            .kind
            .as_ref()
            .is_none_or(|kind| record_kind(record) == kind)
        && filter
            .name
            .as_ref()
            .is_none_or(|name| record_name(record) == Some(name.as_str()))
        && filter
            .trace
            .as_ref()
            .is_none_or(|trace| record_trace(record) == Some(trace.as_str()))
}

fn event_matches(event: &Event, filter: &RawFilter) -> bool {
    event.ts >= filter.since_ms
        && filter
            .name
            .as_ref()
            .is_none_or(|name| event.name == name.as_str())
        && filter
            .trace
            .as_ref()
            .is_none_or(|trace| event.trace == trace.as_str())
}

fn span_start(span: &Span) -> f64 {
    span.ts as f64 - span.dur_ms
}

fn in_trace_parent(span: &Span, span_ids: &HashSet<String>) -> Option<String> {
    match &span.parent {
        Some(parent) if span_ids.contains(parent) => Some(parent.clone()),
        _ => None,
    }
}

fn build_trace_forest(spans: Vec<Span>, events: Vec<Event>) -> Vec<SpanNode> {
    if spans.is_empty() {
        return Vec::new();
    }
    let trace_start = spans.iter().map(span_start).fold(f64::INFINITY, f64::min);
    let mut events_by_parent: HashMap<Option<String>, Vec<Event>> = HashMap::new();
    for event in events {
        events_by_parent
            .entry(event.parent.clone())
            .or_default()
            .push(event);
    }
    let span_ids: HashSet<String> = spans.iter().map(|span| span.span.clone()).collect();
    let mut children_by_parent: HashMap<Option<String>, Vec<Span>> = HashMap::new();
    for span in spans {
        let key = in_trace_parent(&span, &span_ids);
        children_by_parent.entry(key).or_default().push(span);
    }
    build_nodes(
        None,
        trace_start,
        &mut children_by_parent,
        &mut events_by_parent,
    )
}

fn build_nodes(
    parent: Option<&str>,
    trace_start: f64,
    children_by_parent: &mut HashMap<Option<String>, Vec<Span>>,
    events_by_parent: &mut HashMap<Option<String>, Vec<Event>>,
) -> Vec<SpanNode> {
    let key = parent.map(str::to_owned);
    let mut spans = children_by_parent.remove(&key).unwrap_or_default();
    spans.sort_by(|a, b| span_start(a).total_cmp(&span_start(b)));
    spans
        .into_iter()
        .map(|span| {
            let span_id = span.span.clone();
            let children = build_nodes(
                Some(&span_id),
                trace_start,
                children_by_parent,
                events_by_parent,
            );
            let mut events = events_by_parent.remove(&Some(span_id)).unwrap_or_default();
            events.sort_by_key(|event| event.ts);
            let events = events
                .into_iter()
                .map(|event| EventNode {
                    offset_ms: event.ts as f64 - trace_start,
                    event,
                })
                .collect();
            SpanNode {
                offset_ms: span_start(&span) - trace_start,
                span,
                children,
                events,
            }
        })
        .collect()
}

fn counter_keys(metrics: &Attrs) -> Vec<String> {
    metrics
        .get(COUNTERS_METRIC_KEY)
        .and_then(Value::as_array)
        .map(|entries| {
            entries
                .iter()
                .filter_map(|entry| entry.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

fn presented_metrics(metrics: &Attrs) -> Attrs {
    metrics
        .iter()
        .filter(|(key, _)| key.as_str() != COUNTERS_METRIC_KEY)
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

fn counter_delta(previous: &Value, current: &Value) -> Option<Value> {
    if let (Some(prev), Some(cur)) = (previous.as_i64(), current.as_i64()) {
        return Some(Value::from(cur - prev));
    }
    if let (Some(prev), Some(cur)) = (previous.as_f64(), current.as_f64()) {
        return Some(Value::from(cur - prev));
    }
    None
}

fn sample_deltas(scope: &str, samples: Vec<(i64, Attrs)>) -> Vec<SampleDelta> {
    let mut series = Vec::with_capacity(samples.len());
    let mut previous: Option<(i64, Attrs)> = None;
    for (ts, metrics) in samples {
        let mut deltas = Attrs::new();
        let mut sample_delta_ms = None;
        if let Some((prev_ts, prev_metrics)) = &previous {
            sample_delta_ms = Some(ts - prev_ts);
            for key in counter_keys(&metrics) {
                if let (Some(prev_value), Some(cur_value)) =
                    (prev_metrics.get(&key), metrics.get(&key))
                {
                    if let Some(delta) = counter_delta(prev_value, cur_value) {
                        deltas.insert(key, delta);
                    }
                }
            }
        }
        series.push(SampleDelta {
            ts,
            scope: scope.to_owned(),
            metrics: presented_metrics(&metrics),
            deltas,
            sample_delta_ms,
        });
        previous = Some((ts, metrics));
    }
    series
}
