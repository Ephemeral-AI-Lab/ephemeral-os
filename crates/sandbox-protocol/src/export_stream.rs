//! Shared vocabulary for the export spool stream (spec decision 19): the
//! daemon-HTTP route that delivers a sealed export spool to the manager,
//! gated by a single-use, expiring token minted inside the authenticated
//! `export_layerstack` start forward.

/// Route prefix on the daemon HTTP listener: `GET /export/<export_id>`.
pub const EXPORT_STREAM_PATH_PREFIX: &str = "/export/";

/// Request header carrying the single-use stream token.
pub const EXPORT_STREAM_TOKEN_HEADER: &str = "x-eos-export-token";

/// Field on the `export_layerstack` start result carrying the token.
pub const EXPORT_STREAM_TOKEN_FIELD: &str = "stream_token";

/// Seconds from mint after which an unclaimed stream token is rejected.
pub const EXPORT_STREAM_TOKEN_TTL_S: u64 = 30;
