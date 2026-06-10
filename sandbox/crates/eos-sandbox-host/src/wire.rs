//! Host-side copy of the daemon wire vocabulary.
//!
//! These values are deliberately duplicated from the in-box protocol code: no
//! compiled code crosses the host/box boundary, so the host carries its own
//! constants and proves them against `contract/fixtures/` plus
//! `contract/ops.json` in its conformance tests. Never change a value here
//! without the contract artifact changing first.

/// Top-level envelope key carrying the TCP auth token; popped by the daemon
/// before dispatch.
pub const DAEMON_AUTH_FIELD: &str = "_eos_daemon_auth_token";

/// Args key carrying the protocol version (currently inert daemon-side).
pub const DAEMON_PROTOCOL_FIELD: &str = "_eos_daemon_protocol_version";

/// Protocol version stamped into `args` by the host.
pub const DAEMON_PROTOCOL_VERSION: i64 = 1;

/// Maximum bytes in one request frame, both hops.
pub const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;

/// Backoff between connect retries after a failure, then one final attempt
/// (inherited from the frozen host behavior).
pub const CONNECT_RETRY_DELAYS_S: [f64; 4] = [0.25, 0.5, 1.0, 2.0];

/// The liveness op, in its fixture-pinned legacy spelling. The bring-up ready
/// gate polls it until the daemon answers with success: `sandbox.runtime.ready`
/// cannot gate provisioning because its `control_plane` probe only turns
/// `ready: true` once a workspace base exists, and provisioning seeds none.
pub const HEARTBEAT_OP: &str = "api.v1.heartbeat";

/// The readiness probe op, in its fixture-pinned legacy spelling. Requires a
/// `layer_stack_root` arg; used for status embedding and recovery diagnostics,
/// not the provision gate.
pub const READY_OP: &str = "api.runtime.ready";
