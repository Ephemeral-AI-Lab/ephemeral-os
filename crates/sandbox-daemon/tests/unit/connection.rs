use tokio::io::AsyncReadExt as _;

use crate::server::SandboxDaemonError;
use crate::MAX_REQUEST_BYTES;

#[tokio::test]
async fn read_request_line_rejects_oversized_payloads() {
    let mut reader = tokio::io::repeat(b'x').take(
        u64::try_from(MAX_REQUEST_BYTES)
            .expect("max request bytes fits u64")
            .saturating_add(1),
    );
    let err = read_request_line_with_timeout(&mut reader, 0.1)
        .await
        .expect_err("oversized request rejected");
    assert!(matches!(err, SandboxDaemonError::RequestTooLarge { .. }));
}

#[tokio::test]
async fn read_request_line_times_out_waiting_for_line() {
    let (_writer, mut reader) = tokio::io::duplex(64);
    let err = read_request_line_with_timeout(&mut reader, 0.1)
        .await
        .expect_err("hanging request times out");
    assert!(
        matches!(err, SandboxDaemonError::Io(ref source) if source.kind() == std::io::ErrorKind::TimedOut),
        "{err:?}"
    );
}
