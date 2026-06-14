use std::collections::VecDeque;

use crate::codec::{encode_trace_batch, encoded_trace_record_len};
use crate::ids::ExportId;
use crate::record::TraceRecord;
use crate::{sha256_hex, TraceBatch};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SpoolInsertOutcome {
    Stored,
    DroppedNew,
    DroppedOld { count: u64 },
}

#[derive(Debug)]
pub struct TraceSpool {
    max_bytes: usize,
    current_bytes: usize,
    records: VecDeque<(TraceRecord, usize)>,
    dropped_traces: u64,
    acked_dropped_traces: u64,
    active_export: Option<ActiveTraceExport>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TraceExportBatch {
    pub export_id: Option<ExportId>,
    pub record_count: usize,
    pub spool_pending_after: usize,
    pub dropped_traces: u64,
    pub batch_sha256: Option<String>,
    pub trace_batch_bytes: Option<Vec<u8>>,
}

#[derive(Debug, Clone)]
struct ActiveTraceExport {
    export_id: ExportId,
    record_count: usize,
    dropped_traces: u64,
    batch_sha256: String,
    trace_batch_bytes: Vec<u8>,
}

impl TraceSpool {
    #[must_use]
    pub fn new(max_bytes: usize) -> Self {
        Self {
            max_bytes,
            current_bytes: 0,
            records: VecDeque::new(),
            dropped_traces: 0,
            acked_dropped_traces: 0,
            active_export: None,
        }
    }

    #[must_use]
    pub fn dropped_traces(&self) -> u64 {
        self.dropped_traces
    }

    #[must_use]
    pub fn pending_len(&self) -> usize {
        self.records.len()
    }

    pub fn push(&mut self, record: TraceRecord) -> SpoolInsertOutcome {
        let record_bytes = encoded_trace_record_len(&record);
        if record_bytes > self.max_bytes {
            self.dropped_traces = self.dropped_traces.saturating_add(1);
            return SpoolInsertOutcome::DroppedNew;
        }

        let mut dropped_old = 0_u64;
        while self.current_bytes.saturating_add(record_bytes) > self.max_bytes {
            let protected = self
                .active_export
                .as_ref()
                .map_or(0, |export| export.record_count);
            if self.records.len() <= protected {
                self.dropped_traces = self.dropped_traces.saturating_add(1);
                return SpoolInsertOutcome::DroppedNew;
            }
            let Some((_, dropped_bytes)) = self.records.remove(protected) else {
                self.dropped_traces = self.dropped_traces.saturating_add(1);
                return SpoolInsertOutcome::DroppedNew;
            };
            self.current_bytes = self.current_bytes.saturating_sub(dropped_bytes);
            self.dropped_traces = self.dropped_traces.saturating_add(1);
            dropped_old = dropped_old.saturating_add(1);
        }

        self.current_bytes = self.current_bytes.saturating_add(record_bytes);
        self.records.push_back((record, record_bytes));
        if dropped_old == 0 {
            SpoolInsertOutcome::Stored
        } else {
            SpoolInsertOutcome::DroppedOld { count: dropped_old }
        }
    }

    #[must_use]
    pub fn lease_batch(
        &mut self,
        max_records: usize,
        daemon_boot_id: Option<String>,
    ) -> TraceExportBatch {
        if let Some(export) = &self.active_export {
            return export.to_batch(self.records.len());
        }

        let record_count = max_records.min(self.records.len());
        let dropped_changed = self.dropped_traces > self.acked_dropped_traces;
        if record_count == 0 && !dropped_changed {
            return TraceExportBatch {
                export_id: None,
                record_count: 0,
                spool_pending_after: self.records.len(),
                dropped_traces: self.dropped_traces,
                batch_sha256: None,
                trace_batch_bytes: None,
            };
        }

        let records = self
            .records
            .iter()
            .take(record_count)
            .map(|(record, _)| record.clone())
            .collect();
        let trace_batch_bytes = encode_trace_batch(&TraceBatch {
            records,
            dropped_traces: self.dropped_traces,
            daemon_boot_id,
        });
        let active = ActiveTraceExport {
            export_id: ExportId::new(),
            record_count,
            dropped_traces: self.dropped_traces,
            batch_sha256: sha256_hex(&trace_batch_bytes),
            trace_batch_bytes,
        };
        let batch = active.to_batch(self.records.len());
        self.active_export = Some(active);
        batch
    }

    pub fn ack_batch(
        &mut self,
        export_id: &ExportId,
        batch_sha256: &str,
        record_count: usize,
    ) -> bool {
        let Some(export) = &self.active_export else {
            return false;
        };
        if &export.export_id != export_id
            || export.batch_sha256 != batch_sha256
            || export.record_count != record_count
        {
            return false;
        }
        let dropped_traces = export.dropped_traces;
        for _ in 0..record_count {
            let Some((_, bytes)) = self.records.pop_front() else {
                break;
            };
            self.current_bytes = self.current_bytes.saturating_sub(bytes);
        }
        self.acked_dropped_traces = self.acked_dropped_traces.max(dropped_traces);
        self.active_export = None;
        true
    }
}

impl ActiveTraceExport {
    fn to_batch(&self, spool_pending_after: usize) -> TraceExportBatch {
        TraceExportBatch {
            export_id: Some(self.export_id.clone()),
            record_count: self.record_count,
            spool_pending_after,
            dropped_traces: self.dropped_traces,
            batch_sha256: Some(self.batch_sha256.clone()),
            trace_batch_bytes: Some(self.trace_batch_bytes.clone()),
        }
    }
}

impl Default for TraceSpool {
    fn default() -> Self {
        Self::new(4 * 1024 * 1024)
    }
}

#[cfg(test)]
mod tests {
    use crate::{EventRecord, SpanUid, TraceId};

    use super::*;

    #[test]
    fn leased_batch_replays_until_ack() {
        let mut spool = TraceSpool::default();
        let trace_id = TraceId::parse("trace-lease-replay").expect("trace id");
        spool.push(TraceRecord::new(trace_id.clone(), SpanUid::ROOT));

        let first = spool.lease_batch(16, Some("boot-1".to_owned()));
        assert_eq!(first.record_count, 1);
        assert_eq!(first.spool_pending_after, 1);
        let replay = spool.lease_batch(16, Some("boot-1".to_owned()));
        assert_eq!(replay.export_id, first.export_id);
        assert_eq!(replay.batch_sha256, first.batch_sha256);
        assert_eq!(spool.pending_len(), 1);

        assert!(!spool.ack_batch(
            first.export_id.as_ref().expect("export id"),
            "wrong-digest",
            1
        ));
        assert_eq!(spool.pending_len(), 1);
        assert!(spool.ack_batch(
            first.export_id.as_ref().expect("export id"),
            first.batch_sha256.as_deref().expect("batch sha"),
            1
        ));
        assert_eq!(spool.pending_len(), 0);
    }

    #[test]
    fn leased_records_are_not_evicted_under_pressure() {
        let mut spool = TraceSpool::new(330);
        let mut leased = TraceRecord::new(
            TraceId::parse("trace-protected").expect("trace id"),
            SpanUid::ROOT,
        );
        leased.events.push(EventRecord::new(
            SpanUid::ROOT,
            "leased",
            "trace.test",
            serde_json::json!({"payload": "a".repeat(80)}),
        ));
        assert_eq!(spool.push(leased), SpoolInsertOutcome::Stored);
        let export = spool.lease_batch(1, Some("boot-protected".to_owned()));
        assert_eq!(export.record_count, 1);

        let mut pressure = TraceRecord::new(
            TraceId::parse("trace-pressure").expect("trace id"),
            SpanUid::ROOT,
        );
        pressure.events.push(EventRecord::new(
            SpanUid::ROOT,
            "pressure",
            "trace.test",
            serde_json::json!({"payload": "b".repeat(80)}),
        ));
        let _ = spool.push(pressure);

        let replay = spool.lease_batch(1, Some("boot-protected".to_owned()));
        assert_eq!(replay.export_id, export.export_id);
        assert!(spool.ack_batch(
            export.export_id.as_ref().expect("export id"),
            export.batch_sha256.as_deref().expect("batch sha"),
            1,
        ));
    }
}
