use std::sync::Arc;

use super::{ConnectionLimiter, MAX_CONCURRENT_CONNECTIONS};
use crate::wire::server_busy_response;

#[test]
fn connection_limiter_rejects_after_limit_and_releases_permits() {
    let limiter = Arc::new(ConnectionLimiter::new());
    let mut permits = Vec::new();
    for _ in 0..MAX_CONCURRENT_CONNECTIONS {
        permits.push(
            limiter
                .try_acquire()
                .expect("permit should be available below the limit"),
        );
    }

    assert!(
        limiter.try_acquire().is_none(),
        "limiter should reject once all permits are held"
    );
    permits.pop();
    assert!(
        limiter.try_acquire().is_some(),
        "dropping a permit should reopen capacity"
    );
}

#[test]
fn server_busy_response_uses_structured_error_kind() {
    let response = server_busy_response(MAX_CONCURRENT_CONNECTIONS);

    assert_eq!(response["status"], "error");
    assert_eq!(response["error"]["kind"], "server_busy");
    assert_eq!(
        response["error"]["details"]["max_concurrent_connections"],
        MAX_CONCURRENT_CONNECTIONS
    );
}
