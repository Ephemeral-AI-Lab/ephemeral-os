#![forbid(unsafe_code)]

pub mod budget;
pub mod codec;
pub mod ids;
pub mod num;
pub mod record;
pub mod resource_stats;
pub mod sidecar;
pub mod spool;

pub use budget::{sha256_hex, BoundedJson, DetailBudget};
pub use codec::{decode_trace_batch, encode_trace_batch, proto, DecodeTraceError, TraceBatch};
pub use ids::{BootId, ExportId, IdError, RequestId, SpanUid, TraceId};
pub use num::usize_to_f64_saturating;
pub use record::{
    EventRecord, SpanKind, SpanRecord, SpanStatus, SpanSubsystem, TraceKind, TraceLink,
    TraceLinkKind, TraceRecord, WorkspaceRoute,
};
pub use resource_stats::{ResourceStats, ResourceStatsKind, ResourceStatsMeta};
pub use sidecar::{TRACE_SIDECAR_ENCODING, TRACE_SIDECAR_FIELD, TRACE_SIDECAR_SCHEMA};
pub use spool::{SpoolInsertOutcome, TraceExportBatch, TraceSpool};
