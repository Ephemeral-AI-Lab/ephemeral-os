//! Daemon trace assembly: request sidecar span/event-tree building with sidecar
//! budget enforcement, plus envelope-meta stamping/rollups rendered from the
//! trace record. This module is a thin facade; the implementation lives in the
//! submodules below.

mod envelope_meta;
mod sidecar;
mod spool;

pub(crate) use sidecar::attach_request_sidecar;
#[cfg(test)]
pub(crate) use spool::now_ms;
#[cfg(test)]
pub(crate) use spool::RequestTraceEvent;
pub(crate) use spool::{next_connection_id, RequestTraceFacts};
