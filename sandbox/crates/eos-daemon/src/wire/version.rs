//! Frozen wire/protocol constants.
//!
//! Invariant: these values mirror the live Rust daemon byte-for-byte. The
//! protocol-version field is carried INSIDE `args` and the daemon never reads
//! it (an inert versioning hook) — see [`super::envelope`].

/// Daemon protocol version. Carried inside request `args`, never gated on by the
/// daemon.
pub const DAEMON_PROTOCOL_VERSION: i64 = 1;

/// Key under which [`DAEMON_PROTOCOL_VERSION`] is injected into request `args`.
pub const DAEMON_PROTOCOL_FIELD: &str = "_eos_daemon_protocol_version";

/// Top-level (TCP-only, conditional) auth-token envelope key; popped before
/// dispatch.
pub const DAEMON_AUTH_FIELD: &str = "_eos_daemon_auth_token";

/// Thin-client exit code: connect to the daemon socket failed.
pub const CONNECT_FAILED: i32 = 97;

/// Thin-client exit code: connected but the request/response stream failed.
pub const IO_FAILED: i32 = 98;

/// Maximum single-request size (16 MiB); the readline buffer `limit=`.
pub const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;

/// Per-request read timeout (seconds) wrapping `reader.readline()`.
pub const REQUEST_READ_TIMEOUT_S: f64 = 30.0;

/// Post-respawn connect-retry backoff delays (seconds).
pub const CONNECT_RETRY_DELAYS_S: [f64; 4] = [0.25, 0.5, 1.0, 2.0];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constants_match_rust() {
        assert_eq!(DAEMON_PROTOCOL_VERSION, 1);
        assert_eq!(DAEMON_PROTOCOL_FIELD, "_eos_daemon_protocol_version");
        assert_eq!(DAEMON_AUTH_FIELD, "_eos_daemon_auth_token");
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
