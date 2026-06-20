use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;

mod recovery;

use recovery::run_recovery;

use crate::daemon_wire::{encode_request_with_forward_metadata, ClientError, ProtocolClient};
use crate::service::registry::SandboxRecord;
use crate::service::HostConfig;

#[derive(Debug, thiserror::Error)]
pub enum ForwardError {
    #[error("sandbox unavailable: {0}")]
    SandboxUnavailable(String),
    #[error("uncertain outcome: {0}")]
    UncertainOutcome(String),
}

pub(crate) struct ForwardRequestInput<'a> {
    pub(crate) record: Arc<SandboxRecord>,
    pub(crate) config: &'a HostConfig,
    pub(crate) mutates_state: bool,
    pub(crate) op: &'a str,
    pub(crate) invocation_id: &'a str,
    pub(crate) args: &'a Value,
}

pub(crate) fn forward_request(input: ForwardRequestInput<'_>) -> Result<Value, ForwardError> {
    let ForwardRequestInput {
        record,
        config,
        mutates_state,
        op,
        invocation_id,
        args,
    } = input;
    let record_ref = record.as_ref();
    let mut tcp_line = encode_request_with_forward_metadata(
        op,
        invocation_id,
        args,
        Some(&record_ref.forward_token),
    );
    tcp_line.push(b'\n');
    let attempt = ForwardAttempt {
        record: record_ref,
        config,
        mutates_state,
        tcp_line,
        op,
        invocation_id,
        args,
    };
    run_recovery(&attempt)
}

pub(crate) struct ForwardAttempt<'a> {
    pub(crate) record: &'a SandboxRecord,
    pub(crate) config: &'a HostConfig,
    pub(crate) mutates_state: bool,
    pub(crate) tcp_line: Vec<u8>,
    pub(crate) op: &'a str,
    pub(crate) invocation_id: &'a str,
    pub(crate) args: &'a Value,
}

pub(crate) fn tcp_with_connect_backoff(
    attempt: &ForwardAttempt<'_>,
    endpoint: std::net::SocketAddr,
) -> Result<Value, ClientError> {
    let mut attempt_index = 0_u32;
    let mut last = match tcp_once(attempt, endpoint, attempt_index) {
        Ok(value) => return Ok(value),
        Err(err) if err.is_connect_failure() => err,
        Err(err) => return Err(err),
    };
    for delay_s in connect_retry_delays_s().iter().copied() {
        attempt_index = attempt_index.saturating_add(1);
        std::thread::sleep(Duration::from_secs_f64(delay_s));
        match tcp_once(attempt, endpoint, attempt_index) {
            Ok(value) => return Ok(value),
            Err(err) if err.is_connect_failure() => last = err,
            Err(err) => return Err(err),
        }
    }
    Err(last)
}

pub(crate) fn tcp_once(
    attempt: &ForwardAttempt<'_>,
    endpoint: std::net::SocketAddr,
    _attempt_index: u32,
) -> Result<Value, ClientError> {
    let _forward_guard = attempt.record.begin_forward();
    let client = ProtocolClient::new(endpoint, None, attempt.config.request_timeout);
    let response = client.request_raw_observed(&attempt.tcp_line)?;
    Ok(response.value)
}

fn retry_attempt_index() -> u32 {
    u32::try_from(connect_retry_delays_s().len()).unwrap_or(u32::MAX)
}

fn connect_retry_delays_s() -> &'static [f64] {
    &crate::daemon_wire::CONNECT_RETRY_DELAYS_S
}
