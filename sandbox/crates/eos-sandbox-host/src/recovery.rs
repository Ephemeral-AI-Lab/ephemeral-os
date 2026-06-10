//! The normative recovery ladder for a forwarded request that fails (SPEC §6):
//!
//! ```text
//! connect refused/reset ─► invalidate cached endpoint ─► re-resolve ─► retry once
//!         │ still failing
//!         ▼
//! docker exec thin-client fallback (eosd daemon --client)
//!         │ still failing
//!         ▼
//! respawn daemon in-place (docker exec --spawn …) ─► ready-gate
//!         ├─ op.mutates_state == false ─► replay original request
//!         └─ op.mutates_state == true  ─► error kind "uncertain_outcome"
//! ```
//!
//! An ambiguous failure (request possibly delivered: mid-stream I/O error,
//! empty response, undecodable response) on a MUTATING op fails closed with
//! `uncertain_outcome` — a write is never replayed after an ambiguous outcome.

use std::time::Duration;

use serde_json::Value;

use crate::client::{ClientError, ProtocolClient};
use crate::container::DaemonContainer;
use crate::endpoint;
use crate::lifecycle::HostConfig;
use crate::registry::SandboxRecord;
use crate::wire::CONNECT_RETRY_DELAYS_S;

/// Terminal failure of a forwarded request after recovery.
#[derive(Debug, thiserror::Error)]
pub enum ForwardError {
    /// Recovery exhausted: the sandbox cannot be reached or respawned.
    #[error("sandbox unavailable: {0}")]
    SandboxUnavailable(String),
    /// A mutating op was sent but its outcome is unknowable; NOT retried.
    #[error("uncertain outcome: {0}")]
    UncertainOutcome(String),
}

pub(crate) struct ForwardAttempt<'a> {
    pub record: &'a SandboxRecord,
    pub config: &'a HostConfig,
    pub mutates_state: bool,
    /// Stamped envelope WITH the auth token (TCP hop), newline-terminated.
    pub tcp_line: Vec<u8>,
    /// Stamped envelope WITHOUT the auth token (AF_UNIX thin-client hop),
    /// compact JSON without the trailing newline (passed as one argv token).
    pub uds_payload: String,
}

pub(crate) fn run(attempt: &ForwardAttempt<'_>) -> Result<Value, ForwardError> {
    let unavailable = |context: &str, err: &dyn std::fmt::Display| {
        ForwardError::SandboxUnavailable(format!(
            "{} ({context}): {err}",
            attempt.record.sandbox_id
        ))
    };

    let endpoint = match endpoint::cached_or_resolve(attempt.record) {
        Ok(addr) => addr,
        Err(err) => return fallback_chain(attempt, &unavailable("resolve endpoint", &err)),
    };
    match tcp_with_connect_backoff(attempt, endpoint) {
        Ok(value) => Ok(value),
        Err(err) if err.is_connect_failure() => {
            // Invalidate, re-resolve, retry once.
            match endpoint::resolve(attempt.record) {
                Ok(addr) => match tcp_once(attempt, addr) {
                    Ok(value) => Ok(value),
                    Err(err) => {
                        fallback_chain(attempt, &unavailable("retry after re-resolve", &err))
                    }
                },
                Err(err) => fallback_chain(attempt, &unavailable("re-resolve endpoint", &err)),
            }
        }
        Err(err) => {
            // The request may have been delivered: fail closed for writes.
            // The op is never replayed, but the sandbox is still restored
            // (probe, respawn only when dead) so the NEXT call finds a live
            // daemon instead of an eternally failing one.
            if attempt.mutates_state {
                restore_if_unreachable(attempt);
                return Err(ForwardError::UncertainOutcome(format!(
                    "{}: {err}",
                    attempt.record.sandbox_id
                )));
            }
            fallback_chain(attempt, &unavailable("tcp request", &err))
        }
    }
}

/// Best-effort sandbox restoration after an ambiguous mutating-op failure:
/// one short liveness probe, then an in-place respawn only when the daemon is
/// actually unreachable (a healthy-but-slow daemon is never killed).
fn restore_if_unreachable(attempt: &ForwardAttempt<'_>) {
    let probe = endpoint::resolve(attempt.record).ok().and_then(|endpoint| {
        let client = ProtocolClient::new(endpoint, None, Duration::from_secs(2));
        let mut line = crate::client::stamped_envelope_bytes(
            crate::wire::HEARTBEAT_OP,
            "recovery-probe",
            &Value::Object(serde_json::Map::new()),
            Some(&attempt.record.token),
        );
        line.push(b'\n');
        client.request_raw(&line).ok()
    });
    if probe.is_some_and(|resp| crate::client::is_success(&resp)) {
        return;
    }
    let _ = respawn_and_gate(attempt);
}

/// One TCP attempt per backoff slot while connects keep failing, then a final
/// attempt (the frozen host's connect-retry schedule). Non-connect failures
/// surface immediately.
fn tcp_with_connect_backoff(
    attempt: &ForwardAttempt<'_>,
    endpoint: std::net::SocketAddr,
) -> Result<Value, ClientError> {
    let mut last = match tcp_once(attempt, endpoint) {
        Ok(value) => return Ok(value),
        Err(err) if err.is_connect_failure() => err,
        Err(err) => return Err(err),
    };
    for delay_s in CONNECT_RETRY_DELAYS_S {
        std::thread::sleep(Duration::from_secs_f64(delay_s));
        match tcp_once(attempt, endpoint) {
            Ok(value) => return Ok(value),
            Err(err) if err.is_connect_failure() => last = err,
            Err(err) => return Err(err),
        }
    }
    Err(last)
}

fn tcp_once(
    attempt: &ForwardAttempt<'_>,
    endpoint: std::net::SocketAddr,
) -> Result<Value, ClientError> {
    let client = ProtocolClient::new(endpoint, None, attempt.config.request_timeout);
    client.request_raw(&attempt.tcp_line)
}

/// Stage 2 and 3 of the ladder: thin-client exec fallback, then in-place
/// respawn with the ready gate, then replay (read-only) or fail closed
/// (mutating).
fn fallback_chain(
    attempt: &ForwardAttempt<'_>,
    failure: &ForwardError,
) -> Result<Value, ForwardError> {
    if let Ok(value) = exec_thin_client(attempt) {
        return Ok(value);
    }
    respawn_and_gate(attempt).map_err(|err| {
        ForwardError::SandboxUnavailable(format!(
            "{}; respawn failed: {err:#}",
            failure_text(failure)
        ))
    })?;
    if attempt.mutates_state {
        return Err(ForwardError::UncertainOutcome(format!(
            "{}: daemon respawned after a delivery-ambiguous failure; the original outcome is unknowable",
            attempt.record.sandbox_id
        )));
    }
    let endpoint = endpoint::resolve(attempt.record).map_err(|err| {
        ForwardError::SandboxUnavailable(format!("resolve after respawn: {err:#}"))
    })?;
    tcp_once(attempt, endpoint)
        .map_err(|err| ForwardError::SandboxUnavailable(format!("replay after respawn: {err}")))
}

fn failure_text(failure: &ForwardError) -> String {
    failure.to_string()
}

/// `docker exec <container> eosd daemon --client <socket> <payload>` — the
/// daemon binary as its own thin client over its in-container AF_UNIX socket.
fn exec_thin_client(attempt: &ForwardAttempt<'_>) -> anyhow::Result<Value> {
    let container = handle(attempt);
    let socket = attempt
        .config
        .remote_daemon_dir
        .join("runtime.sock")
        .to_string_lossy()
        .into_owned();
    let eosd = attempt
        .config
        .remote_eosd_path
        .to_string_lossy()
        .into_owned();
    let stdout = container.exec(&[&eosd, "daemon", "--client", &socket, &attempt.uds_payload])?;
    Ok(serde_json::from_str(stdout.trim())?)
}

/// Respawn the daemon in place with the record's original spawn parameters
/// and block until the heartbeat ready gate passes.
fn respawn_and_gate(attempt: &ForwardAttempt<'_>) -> anyhow::Result<()> {
    let daemon = attempt.config.daemon_spec(attempt.record.tcp_port);
    handle(attempt).restart_daemon(&daemon)
}

/// A non-owning container handle for exec/respawn (lifetime `Keep` so drop
/// never removes the container).
fn handle(attempt: &ForwardAttempt<'_>) -> DaemonContainer {
    DaemonContainer::for_engine(
        attempt.record.container.clone(),
        attempt.record.token.clone(),
        &attempt.config.daemon_spec(attempt.record.tcp_port),
        attempt.record.cached_endpoint(),
    )
}
