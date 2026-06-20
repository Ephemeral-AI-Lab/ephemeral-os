use serde_json::Value;

use crate::container::DaemonContainer;
use crate::daemon_wire::{encode_request_with_forward_metadata, ProtocolClient, READY_OP};

use super::{
    retry_attempt_index, tcp_once, tcp_with_connect_backoff, ForwardAttempt, ForwardError,
};
use crate::service::registry::resolve_endpoint;

pub(super) fn run_recovery(attempt: &ForwardAttempt<'_>) -> Result<Value, ForwardError> {
    let unavailable = |context: &str, err: &dyn std::fmt::Display| {
        ForwardError::SandboxUnavailable(format!(
            "{} ({context}): {err}",
            attempt.record.sandbox_id
        ))
    };

    let endpoint = match crate::service::registry::cached_or_resolve_endpoint(attempt.record) {
        Ok(addr) => addr,
        Err(err) => {
            return fallback_chain(attempt, &unavailable("resolve endpoint", &err));
        }
    };
    match tcp_with_connect_backoff(attempt, endpoint) {
        Ok(value) => Ok(value),
        Err(err) if err.is_connect_failure() => match resolve_endpoint(attempt.record) {
            Ok(addr) => match tcp_once(attempt, addr, retry_attempt_index()) {
                Ok(value) => Ok(value),
                Err(err) => fallback_chain(attempt, &unavailable("retry after re-resolve", &err)),
            },
            Err(err) => fallback_chain(attempt, &unavailable("re-resolve endpoint", &err)),
        },
        Err(err) => {
            restore_if_unreachable(attempt);
            Err(ForwardError::UncertainOutcome(format!(
                "{}: {err}",
                attempt.record.sandbox_id
            )))
        }
    }
}

fn restore_if_unreachable(attempt: &ForwardAttempt<'_>) {
    let probe = resolve_endpoint(attempt.record).ok().and_then(|endpoint| {
        let _forward_guard = attempt.record.begin_forward();
        let client = ProtocolClient::new(endpoint, None, std::time::Duration::from_secs(2));
        let mut line = encode_request_with_forward_metadata(
            READY_OP,
            "recovery-probe",
            &Value::Object(serde_json::Map::new()),
            Some(&attempt.record.forward_token),
        );
        line.push(b'\n');
        client.request_raw(&line).ok()
    });
    if probe.is_some() {
        return;
    }
    let _ = respawn_and_gate(attempt);
}

fn fallback_chain(
    attempt: &ForwardAttempt<'_>,
    failure: &ForwardError,
) -> Result<Value, ForwardError> {
    if let Ok(value) = exec_thin_client(attempt) {
        return Ok(value);
    }
    respawn_and_gate(attempt).map_err(|err| {
        let message = format!("{failure}; respawn failed: {err:#}");
        ForwardError::SandboxUnavailable(message)
    })?;
    Err(ForwardError::UncertainOutcome(format!(
        "{}: daemon respawned after a delivery-ambiguous failure; the original outcome is unknowable",
        attempt.record.sandbox_id
    )))
}

fn exec_thin_client(attempt: &ForwardAttempt<'_>) -> anyhow::Result<Value> {
    let _forward_guard = attempt.record.begin_forward();
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
    let payload = String::from_utf8(encode_request_with_forward_metadata(
        attempt.op,
        attempt.request_id,
        attempt.args,
        None,
    ))?;
    let stdout = container.exec(&[&eosd, "daemon", "--client", &socket, &payload])?;
    Ok(serde_json::from_str(stdout.trim())?)
}

fn respawn_and_gate(attempt: &ForwardAttempt<'_>) -> anyhow::Result<()> {
    let daemon = attempt.config.daemon_spec(attempt.record.tcp_port);
    let _respawn_guard = attempt.record.begin_respawn();
    handle(attempt).restart_daemon(&daemon)
}

fn handle(attempt: &ForwardAttempt<'_>) -> DaemonContainer {
    DaemonContainer::for_engine(
        attempt.record.container.clone(),
        attempt.record.token.clone(),
        attempt.record.forward_token.clone(),
        &attempt.config.daemon_spec(attempt.record.tcp_port),
        attempt.record.cached_endpoint(),
    )
}
