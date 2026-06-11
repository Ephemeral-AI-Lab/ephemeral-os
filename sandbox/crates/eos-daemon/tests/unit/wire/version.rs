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
