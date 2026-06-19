mod budget;
pub(crate) mod build;
pub(crate) mod events;
mod resources;

use super::envelope_meta::stamp_pending_envelope_meta;
use super::spool::{daemon_boot_id, now_ms, RequestTraceEvent, RequestTraceFacts};
use crate::wire::RequestTraceContext;

pub(crate) use build::attach_request_sidecar;
