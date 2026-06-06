use crate::bootstrap_artifact::EOSD_VERSION;
use crate::provider::DaemonTcpEndpoint;

use super::{
    DAEMON_ENV_SIGNATURE_PATH, DAEMON_LOG_PATH, DAEMON_PID_PATH, DAEMON_SOCKET_PATH,
    EOSD_REMOTE_PATH, EOSD_SHA_MARKER,
};

pub(crate) fn posix_quote(s: &str) -> String {
    if s.is_empty() {
        return "''".to_owned();
    }
    if s.bytes().all(|b| {
        b.is_ascii_alphanumeric()
            || matches!(
                b,
                b'@' | b'%' | b'_' | b'+' | b'=' | b':' | b',' | b'.' | b'/' | b'-'
            )
    }) {
        return s.to_owned();
    }
    let mut out = String::with_capacity(s.len() + 2);
    out.push('\'');
    for ch in s.chars() {
        if ch == '\'' {
            out.push_str("'\\''");
        } else {
            out.push(ch);
        }
    }
    out.push('\'');
    out
}

fn shell_join(parts: &[&str]) -> String {
    parts
        .iter()
        .map(|p| posix_quote(p))
        .collect::<Vec<_>>()
        .join(" ")
}

pub(super) fn daemon_thin_client_command(envelope_json: &str) -> String {
    shell_join(&[
        EOSD_REMOTE_PATH,
        "daemon",
        "--client",
        DAEMON_SOCKET_PATH,
        envelope_json,
    ])
}

pub(super) fn daemon_spawn_command(tcp_endpoint: Option<&DaemonTcpEndpoint>) -> String {
    let mut parts: Vec<String> = vec![
        EOSD_REMOTE_PATH.to_owned(),
        "daemon".to_owned(),
        "--spawn".to_owned(),
        "--socket".to_owned(),
        DAEMON_SOCKET_PATH.to_owned(),
        "--pid-file".to_owned(),
        DAEMON_PID_PATH.to_owned(),
        "--log-file".to_owned(),
        DAEMON_LOG_PATH.to_owned(),
    ];
    if let Some(endpoint) = tcp_endpoint {
        let port = endpoint.internal_port.unwrap_or(endpoint.port);
        parts.push("--tcp-host".to_owned());
        parts.push("0.0.0.0".to_owned());
        parts.push("--tcp-port".to_owned());
        parts.push(port.to_string());
        if !endpoint.auth_token.is_empty() {
            parts.push("--auth-token".to_owned());
            parts.push(endpoint.auth_token.clone());
        }
    }
    let spawn_command = parts
        .iter()
        .map(|p| posix_quote(p))
        .collect::<Vec<_>>()
        .join(" ");
    let inner = rust_daemon_spawn_shell(&spawn_command, &daemon_env_signature(tcp_endpoint));
    // Source /etc/environment so feature-flag env vars propagate to the daemon.
    format!("if [ -r /etc/environment ]; then set -a; . /etc/environment; set +a; fi; {inner}")
}

/// The restart-on-signature-change shell.
fn rust_daemon_spawn_shell(spawn_command: &str, signature: &str) -> String {
    let marker = posix_quote(EOSD_SHA_MARKER);
    let socket = posix_quote(DAEMON_SOCKET_PATH);
    let pid = posix_quote(DAEMON_PID_PATH);
    let env = posix_quote(DAEMON_ENV_SIGNATURE_PATH);
    [
        format!("daemon_env_sig={};", posix_quote(signature)),
        format!(
            "if [ -f {marker} ]; then daemon_env_sig=\"$daemon_env_sig;eosd_sha=$(cat {marker})\"; fi;"
        ),
        format!(
            "if [ -S {socket} ] && [ -f {pid} ]; then \
             if [ ! -f {env} ] || [ \"$(cat {env})\" != \"$daemon_env_sig\" ]; then \
             daemon_pid=$(cat {pid} 2>/dev/null || true); \
             if [ -n \"$daemon_pid\" ]; then \
             kill \"$daemon_pid\" 2>/dev/null || true; \
             for _ in $(seq 1 50); do \
             kill -0 \"$daemon_pid\" 2>/dev/null || break; \
             sleep 0.02; \
             done; \
             fi; \
             rm -f {socket} {pid}; \
             fi; \
             fi;"
        ),
        format!("{spawn_command} && printf %s \"$daemon_env_sig\" > {env}"),
    ]
    .join(" ")
}

/// Daemon env signature. GC-04: `sandbox_runtime` collapses to `rust`; the
/// dropped module-bundle `bundle_hash()` is replaced by
/// the pinned `EOSD_VERSION` as the `runtime_bundle_sha` identity (the binary's
/// own digest is appended container-side from the `.eosd-sha256` marker).
fn daemon_env_signature(tcp_endpoint: Option<&DaemonTcpEndpoint>) -> String {
    let mut parts = vec![
        "sandbox_runtime=rust".to_owned(),
        format!("runtime_bundle_sha={EOSD_VERSION}"),
    ];
    if let Some(endpoint) = tcp_endpoint {
        let port = endpoint.internal_port.unwrap_or(endpoint.port);
        parts.push(format!("daemon_tcp_port={port}"));
    }
    parts.join(";")
}
