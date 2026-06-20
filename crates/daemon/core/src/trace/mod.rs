//! Daemon trace assembly: request sidecar span/event-tree building with sidecar
//! budget enforcement, plus envelope-meta stamping/rollups rendered from the
//! trace record. This module is a thin facade; the implementation lives in the
//! submodules below.

mod envelope_meta;
pub(crate) mod sidecar;
mod spool;

pub(crate) use sidecar::attach_request_sidecar;
#[allow(unused_imports)]
pub(crate) use spool::now_ms;
pub(crate) use spool::{next_connection_id, RequestTraceFacts};
