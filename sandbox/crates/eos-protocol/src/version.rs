//! Frozen wire/protocol constants.
//!
//! Invariant: these values mirror the live Python daemon byte-for-byte. The
//! protocol-version field is carried INSIDE `args` and the daemon never reads
//! it (an inert versioning hook) — see [`crate::envelope`]. Source anchors are
//! cited per constant against `backend/src/sandbox`.

/// Daemon protocol version. Carried inside request `args`, never gated on by the
/// daemon. `// PORT backend/src/sandbox/host/daemon_client.py:46`.
pub const DAEMON_PROTOCOL_VERSION: i64 = 1;

/// Key under which [`DAEMON_PROTOCOL_VERSION`] is injected into request `args`.
/// `// PORT backend/src/sandbox/host/daemon_client.py:47`.
pub const DAEMON_PROTOCOL_FIELD: &str = "_eos_daemon_protocol_version";

/// Top-level (TCP-only, conditional) auth-token envelope key; popped before
/// dispatch. `// PORT backend/src/sandbox/host/daemon_client.py:48`.
pub const DAEMON_AUTH_FIELD: &str = "_eos_daemon_auth_token";

/// On-disk manifest schema version (the current and only supported schema).
/// `// PORT backend/src/sandbox/layer_stack/manifest.py:22`.
pub const MANIFEST_SCHEMA_VERSION: i64 = 1;

/// Thin-client exit code: connect to the daemon socket failed.
/// `// PORT backend/src/sandbox/host/daemon_client.py:37`, `thin_client.py:9`.
pub const CONNECT_FAILED: i32 = 97;

/// Thin-client exit code: connected but the request/response stream failed.
/// `// PORT backend/src/sandbox/host/daemon_client.py:38`, `thin_client.py:10`.
pub const IO_FAILED: i32 = 98;

/// Maximum single-request size (16 MiB); the readline buffer `limit=`.
/// `// PORT backend/src/sandbox/daemon/rpc/server.py:58`.
pub const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;

/// Per-request read timeout (seconds) wrapping `reader.readline()`.
/// `// PORT backend/src/sandbox/daemon/rpc/server.py:62`.
pub const REQUEST_READ_TIMEOUT_S: f64 = 30.0;

/// Post-respawn connect-retry backoff delays (seconds).
/// `// PORT backend/src/sandbox/host/daemon_client.py:45`.
pub const CONNECT_RETRY_DELAYS_S: [f64; 4] = [0.25, 0.5, 1.0, 2.0];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constants_match_python() {
        assert_eq!(DAEMON_PROTOCOL_VERSION, 1);
        assert_eq!(DAEMON_PROTOCOL_FIELD, "_eos_daemon_protocol_version");
        assert_eq!(DAEMON_AUTH_FIELD, "_eos_daemon_auth_token");
        assert_eq!(MANIFEST_SCHEMA_VERSION, 1);
        assert_eq!(CONNECT_FAILED, 97);
        assert_eq!(IO_FAILED, 98);
        assert_eq!(MAX_REQUEST_BYTES, 16_777_216);
        assert!((REQUEST_READ_TIMEOUT_S - 30.0).abs() < f64::EPSILON);
        assert_eq!(
            CONNECT_RETRY_DELAYS_S.map(f64::to_bits),
            [0.25_f64, 0.5, 1.0, 2.0].map(f64::to_bits)
        );
    }
}
