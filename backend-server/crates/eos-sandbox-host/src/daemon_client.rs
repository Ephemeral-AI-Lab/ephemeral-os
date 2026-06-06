//! Host-side daemon transport: serialize one JSON envelope per call, send it to
//! the resident in-sandbox daemon (TCP fast path or `AF_UNIX` thin client through
//! `adapter.exec`), run the spawn/connect/empty-response recovery state machine,
//! cache the per-sandbox TCP endpoint with single-flight, and decode typed
//! errors.
//!
//! Per GC-04 the runtime is `eosd`; there is no alternate launcher branch.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use eos_sandbox_port::{DaemonOp, PluginPackageEnsureRequest, SandboxPortError, SandboxTransport};
use eos_types::{JsonObject, SandboxId};
use parking_lot::RwLock;
use serde_json::Value;

use crate::error::SandboxHostError;
use crate::provider::{DaemonTcpEndpoint, ProviderAdapter};
use crate::registry::ProviderRegistry;

mod codec;
mod shell;
mod tcp;

// Test bodies live under the crate `tests/` tree (spec §Backend Test Layout);
// included here as a private `#[cfg(test)]` submodule so they keep `super::`
// access to the daemon-client internals they exercise.
#[cfg(test)]
#[path = "../tests/daemon_client/mod.rs"]
mod tests;

pub(crate) use codec::map_host_error_to_api_error;
pub(crate) use shell::posix_quote;

use codec::{
    can_retry_empty_response, decode_and_classify, decode_response, detail, exec_failed, exec_opts,
    is_bootstrap_ready_response, is_empty_response, new_invocation_id, readiness_error_from_value,
    serialize_envelope, stderr_or_stdout, truthy_to_string, without_none,
};
use shell::{daemon_spawn_command, daemon_thin_client_command};
use tcp::call_tcp_daemon;

// --- wire protocol constants --------------------------------------------------
//
// Owned by the sibling `sandbox/` workspace's `eos-protocol` crate (the same
// crate the in-container `eosd` is built from). The host-typed forms below are
// DERIVED from `eos_protocol`, never hand-pinned, so a daemon-side bump cannot
// silently drift the host. Host-only operational knobs stay local.

/// The wire protocol version the host speaks (from `eos_protocol`). Lockstep
/// with [`crate::bootstrap_artifact::PROTOCOL_VERSION`].
pub const DAEMON_PROTOCOL_VERSION: u32 = eos_protocol::DAEMON_PROTOCOL_VERSION as u32;
const DAEMON_PROTOCOL_FIELD: &str = eos_protocol::DAEMON_PROTOCOL_FIELD;
const DAEMON_AUTH_FIELD: &str = eos_protocol::DAEMON_AUTH_FIELD;
const THIN_CLIENT_CONNECT_FAILED: i32 = eos_protocol::CONNECT_FAILED;
const THIN_CLIENT_IO_FAILED: i32 = eos_protocol::IO_FAILED;
const EMPTY_RESPONSE_MESSAGE: &str = "EOS_DAEMON_IO_FAILED:empty_response";
const DAEMON_SPAWN_TIMEOUT_S: u32 = 20;
const READINESS_TIMEOUT_S: u32 = 30;
const TCP_DEFAULT_TIMEOUT_S: u32 = 60;

/// Reconnect backoff, derived from `eos_protocol::CONNECT_RETRY_DELAYS_S`
/// (seconds) so the schedule stays single-sourced with the daemon contract.
const fn secs_to_duration(secs: f64) -> Duration {
    Duration::from_millis((secs * 1000.0) as u64)
}
const CONNECT_RETRY_DELAYS: [Duration; 4] = [
    secs_to_duration(eos_protocol::CONNECT_RETRY_DELAYS_S[0]),
    secs_to_duration(eos_protocol::CONNECT_RETRY_DELAYS_S[1]),
    secs_to_duration(eos_protocol::CONNECT_RETRY_DELAYS_S[2]),
    secs_to_duration(eos_protocol::CONNECT_RETRY_DELAYS_S[3]),
];

// --- resolved container-side paths --------------------------------------------

pub(crate) const BUNDLE_REMOTE_DIR: &str = "/eos/runtime/daemon";
/// Default `LayerStack` root injected into every envelope's `args.layer_stack_root`.
pub const DEFAULT_LAYER_STACK_ROOT: &str = "/eos/state/layer-stack";
const DAEMON_SOCKET_PATH: &str = "/eos/runtime/daemon/runtime.sock";
const DAEMON_PID_PATH: &str = "/eos/runtime/daemon/runtime.pid";
const DAEMON_LOG_PATH: &str = "/eos/runtime/daemon/runtime.log";
const DAEMON_ENV_SIGNATURE_PATH: &str = "/eos/runtime/daemon/runtime.env";
pub(crate) const EOSD_REMOTE_PATH: &str = "/eos/runtime/daemon/eosd";
pub(crate) const EOSD_SHA_MARKER: &str = "/eos/runtime/daemon/.eosd-sha256";

// --- public helpers -----------------------------------------------------------

/// Prepend the wire protocol-version field to a payload (payload wins on a key
/// collision). The `eos-sandbox-host` [`SandboxTransport`] impl applies this
/// before dispatch.
#[must_use]
pub fn with_daemon_protocol_version(payload: JsonObject) -> JsonObject {
    let mut out = JsonObject::new();
    out.insert(
        DAEMON_PROTOCOL_FIELD.to_owned(),
        Value::from(DAEMON_PROTOCOL_VERSION),
    );
    out.extend(payload);
    out
}

/// The daemon-backed [`SandboxTransport`] implementor: resolves the provider
/// adapter, runs the recovery state machine, decodes the typed response, and
/// owns the per-sandbox TCP-endpoint cache + single-flight locks.
#[derive(Debug)]
pub struct DaemonClient {
    registry: Arc<ProviderRegistry>,
    /// `Some(None)` is a valid negative-cache entry (adapter has no TCP path);
    /// absence means "not yet resolved". Sync read/insert (`own-rwlock-readers`).
    tcp_cache: RwLock<HashMap<SandboxId, Option<DaemonTcpEndpoint>>>,
    /// Per-sandbox single-flight guards — the one `tokio::sync::Mutex` in this
    /// crate, deliberately held across the async resolve round-trip (spec §7).
    tcp_locks: RwLock<HashMap<SandboxId, Arc<tokio::sync::Mutex<()>>>>,
}

impl DaemonClient {
    /// Build a daemon client over a shared provider registry.
    #[must_use]
    pub fn new(registry: Arc<ProviderRegistry>) -> Self {
        Self {
            registry,
            tcp_cache: RwLock::new(HashMap::new()),
            tcp_locks: RwLock::new(HashMap::new()),
        }
    }

    /// The shared provider registry this client dispatches through.
    #[must_use]
    pub fn registry(&self) -> &Arc<ProviderRegistry> {
        &self.registry
    }

    /// Drop any cached TCP endpoint for `sandbox_id`.
    pub fn invalidate_daemon_tcp_endpoint(&self, sandbox_id: &SandboxId) {
        self.tcp_cache.write().remove(sandbox_id);
    }

    /// Dispatch one daemon op and return the decoded response object.
    ///
    /// `op` is the verbatim wire op string (e.g. `api.v1.read_file`,
    /// `api.ensure_workspace_base`). `args` are merged over the injected
    /// `layer_stack_root`; `args` win on collision.
    pub async fn call_daemon_api(
        &self,
        sandbox_id: &SandboxId,
        op: &str,
        args: JsonObject,
        timeout_s: u32,
        layer_stack_root: &str,
    ) -> Result<JsonObject, SandboxHostError> {
        let mut daemon_args = JsonObject::new();
        daemon_args.insert(
            "layer_stack_root".to_owned(),
            Value::String(layer_stack_root.to_owned()),
        );
        daemon_args.extend(args);

        let adapter = self.registry.adapter()?;
        let tcp_endpoint = self
            .resolve_daemon_tcp_endpoint(&*adapter, sandbox_id)
            .await;
        self.call_daemon(
            &*adapter,
            sandbox_id,
            op,
            daemon_args,
            timeout_s,
            tcp_endpoint.as_ref(),
        )
        .await
    }

    /// Re-spawn the resident daemon. The eosd spawn restarts the daemon when its
    /// env signature changes. This does **not** invalidate the TCP cache;
    /// invalidation happens only on the send path.
    pub async fn ensure_daemon_current(
        &self,
        sandbox_id: &SandboxId,
        timeout_s: u32,
    ) -> Result<(), SandboxHostError> {
        let adapter = self.registry.adapter()?;
        let tcp_endpoint = self
            .resolve_daemon_tcp_endpoint(&*adapter, sandbox_id)
            .await;
        let command = daemon_spawn_command(tcp_endpoint.as_ref());
        let result = adapter
            .exec(
                sandbox_id,
                &command,
                &exec_opts(BUNDLE_REMOTE_DIR, timeout_s),
            )
            .await?;
        if result.exit_code != 0 {
            return Err(exec_failed(&result));
        }
        Ok(())
    }

    // --- envelope build + decode (AC-03 / AC-05) ------------------------------

    async fn call_daemon(
        &self,
        adapter: &dyn ProviderAdapter,
        sandbox_id: &SandboxId,
        op: &str,
        args: JsonObject,
        timeout_s: u32,
        tcp_endpoint: Option<&DaemonTcpEndpoint>,
    ) -> Result<JsonObject, SandboxHostError> {
        let mut clean_args = without_none(args);
        // invocation_id: fresh for cancel; else reuse a present truthy one or mint
        // (and write it back into args for non-cancel ops).
        let invocation_id = if op == "api.v1.cancel" {
            new_invocation_id()
        } else {
            let id = clean_args
                .get("invocation_id")
                .and_then(truthy_to_string)
                .unwrap_or_else(new_invocation_id);
            clean_args.insert("invocation_id".to_owned(), Value::String(id.clone()));
            id
        };
        let envelope_json = serialize_envelope(op, &invocation_id, &clean_args);
        let result = self
            .dispatch_with_daemon_spawn_recovery(
                adapter,
                sandbox_id,
                op,
                &clean_args,
                &envelope_json,
                timeout_s,
                tcp_endpoint,
            )
            .await?;
        decode_and_classify(&result)
    }

    // --- recovery state machine (AC-04) ---------------------------------------

    // one cohesive recovery-dispatch signature: the full daemon-call context
    // (adapter, sandbox id, op, args, timeout, endpoint) threaded through the
    // spawn/connect/empty-response state machine.
    #[allow(clippy::too_many_arguments)]
    async fn dispatch_with_daemon_spawn_recovery(
        &self,
        adapter: &dyn ProviderAdapter,
        sandbox_id: &SandboxId,
        op: &str,
        args: &JsonObject,
        envelope_json: &str,
        timeout_s: u32,
        tcp_endpoint: Option<&DaemonTcpEndpoint>,
    ) -> Result<crate::provider::RawExecResult, SandboxHostError> {
        // STEP 1 — first attempt.
        let result = self
            .send_daemon_envelope(adapter, sandbox_id, envelope_json, timeout_s, tcp_endpoint)
            .await?;

        // STEP 2 — recover iff CONNECT_FAILED (not op-gated) OR empty-response on
        // a retry-eligible op. A mutating op with an empty response fails closed.
        if result.exit_code != THIN_CLIENT_CONNECT_FAILED
            && !(is_empty_response(&result) && can_retry_empty_response(op))
        {
            return Ok(result);
        }

        // STEP 3 — spawn the daemon.
        let spawn_command = daemon_spawn_command(tcp_endpoint);
        let spawn_result = adapter
            .exec(
                sandbox_id,
                &spawn_command,
                &exec_opts(BUNDLE_REMOTE_DIR, DAEMON_SPAWN_TIMEOUT_S),
            )
            .await?;
        if spawn_result.exit_code != 0 {
            return Ok(spawn_result);
        }

        // STEP 4 — readiness requires a layer_stack_root.
        let layer_stack_root = args
            .get("layer_stack_root")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| SandboxHostError::DaemonDispatch {
                kind: "MissingLayerStackRoot".to_owned(),
                message: "daemon readiness check requires layer_stack_root".to_owned(),
                details: detail(&[("op", op)]),
            })?
            .to_owned();

        // STEP 5 — readiness probe with connect-retry (fresh id, fixed 30s,
        // unconditional empty-response retry — readiness is a control op).
        let mut ready_args = JsonObject::new();
        ready_args.insert(
            "layer_stack_root".to_owned(),
            Value::String(layer_stack_root),
        );
        let readiness_json =
            serialize_envelope("api.runtime.ready", &new_invocation_id(), &ready_args);
        let readiness_result = self
            .call_daemon_envelope_with_connect_retry(
                adapter,
                sandbox_id,
                &readiness_json,
                READINESS_TIMEOUT_S,
                tcp_endpoint,
                true,
            )
            .await?;

        // STEP 6 — readiness result handling (ANY error raises; no policy gate).
        if readiness_result.exit_code != 0 {
            let mut details = detail(&[("original_op", op)]);
            details.insert(
                "exit_code".to_owned(),
                Value::from(readiness_result.exit_code),
            );
            return Err(SandboxHostError::DaemonDispatch {
                kind: "RuntimeReadinessFailed".to_owned(),
                message: stderr_or_stdout(&readiness_result),
                details,
            });
        }
        let response = match decode_response(&readiness_result.stdout) {
            Ok(response) => response,
            Err(_) => {
                let mut details = detail(&[("original_op", op)]);
                details.insert(
                    "stdout".to_owned(),
                    Value::String(readiness_result.stdout.clone()),
                );
                return Err(SandboxHostError::DaemonDispatch {
                    kind: "BadRuntimeReadinessResponse".to_owned(),
                    message: "daemon returned invalid JSON".to_owned(),
                    details,
                });
            }
        };
        if let Some(error) = response.get("error").filter(|v| !v.is_null()) {
            return Err(readiness_error_from_value(error, op));
        }

        // STEP 7 — ready flag (must be exactly `true`), with the bootstrap fall-through.
        if response.get("ready") != Some(&Value::Bool(true)) {
            if is_bootstrap_ready_response(op, &response) {
                tracing::warn!(
                    op,
                    "daemon-readiness: declaring op ready despite control_plane WorkspaceBindingError"
                );
            } else {
                let mut details = JsonObject::new();
                details.insert("response".to_owned(), Value::Object(response));
                details.insert("original_op".to_owned(), Value::String(op.to_owned()));
                return Err(SandboxHostError::DaemonNotReady { details });
            }
        }

        // STEP 8 — replay the original envelope (op-gated empty-response retry).
        self.call_daemon_envelope_with_connect_retry(
            adapter,
            sandbox_id,
            envelope_json,
            timeout_s,
            tcp_endpoint,
            can_retry_empty_response(op),
        )
        .await
    }

    async fn call_daemon_envelope_with_connect_retry(
        &self,
        adapter: &dyn ProviderAdapter,
        sandbox_id: &SandboxId,
        envelope_json: &str,
        timeout_s: u32,
        tcp_endpoint: Option<&DaemonTcpEndpoint>,
        retry_empty_response: bool,
    ) -> Result<crate::provider::RawExecResult, SandboxHostError> {
        // 4 retry attempts each followed by its delay, then one final attempt
        // (total 5 sends, 4 sleeps); the 5th is returned unconditionally.
        for delay in CONNECT_RETRY_DELAYS {
            let result = self
                .send_daemon_envelope(adapter, sandbox_id, envelope_json, timeout_s, tcp_endpoint)
                .await?;
            if result.exit_code != THIN_CLIENT_CONNECT_FAILED
                && !(retry_empty_response && is_empty_response(&result))
            {
                return Ok(result);
            }
            tokio::time::sleep(delay).await;
        }
        self.send_daemon_envelope(adapter, sandbox_id, envelope_json, timeout_s, tcp_endpoint)
            .await
    }

    // --- send path (TCP-first, AF_UNIX fallback) ------------------------------

    async fn send_daemon_envelope(
        &self,
        adapter: &dyn ProviderAdapter,
        sandbox_id: &SandboxId,
        envelope_json: &str,
        timeout_s: u32,
        tcp_endpoint: Option<&DaemonTcpEndpoint>,
    ) -> Result<crate::provider::RawExecResult, SandboxHostError> {
        if let Some(endpoint) = tcp_endpoint {
            let tcp_result = call_tcp_daemon(endpoint, envelope_json, timeout_s).await;
            if is_empty_response(&tcp_result) {
                self.invalidate_daemon_tcp_endpoint(sandbox_id);
                return Ok(tcp_result);
            }
            if tcp_result.exit_code != THIN_CLIENT_CONNECT_FAILED {
                return Ok(tcp_result);
            }
            // CONNECT_FAILED → drop cache, fall through to the AF_UNIX thin client.
            self.invalidate_daemon_tcp_endpoint(sandbox_id);
        }
        let command = daemon_thin_client_command(envelope_json);
        adapter
            .exec(
                sandbox_id,
                &command,
                &exec_opts(BUNDLE_REMOTE_DIR, timeout_s),
            )
            .await
    }

    // --- TCP endpoint resolution + single-flight (AC-07b) ---------------------

    /// Resolve (and cache) the per-sandbox TCP endpoint, single-flighting
    /// concurrent callers. Returns `None` when the adapter has no TCP path; a
    /// resolver **error** returns `None` without caching (so the next call
    /// retries — intentional asymmetry).
    pub(crate) async fn resolve_daemon_tcp_endpoint(
        &self,
        adapter: &dyn ProviderAdapter,
        sandbox_id: &SandboxId,
    ) -> Option<DaemonTcpEndpoint> {
        // (1) fast-path cache read — clone out, drop the guard before any await.
        {
            let cache = self.tcp_cache.read();
            if let Some(cached) = cache.get(sandbox_id) {
                return cached.clone();
            }
        }
        // (2) get-or-insert the per-sandbox async mutex (parking_lot guard dropped immediately).
        let lock = {
            let mut locks = self.tcp_locks.write();
            Arc::clone(
                locks
                    .entry(sandbox_id.clone())
                    .or_insert_with(|| Arc::new(tokio::sync::Mutex::new(()))),
            )
        };
        // (3) acquire the async mutex — held across the resolve await (the one
        // legitimate must-span-await lock in this crate).
        let _guard = lock.lock().await;
        // (4) re-check the cache under the single-flight guard.
        {
            let cache = self.tcp_cache.read();
            if let Some(cached) = cache.get(sandbox_id) {
                return cached.clone();
            }
        }
        // (5) resolve once.
        let endpoint = match adapter.daemon_tcp_endpoint(sandbox_id).await {
            Ok(endpoint) => endpoint,
            Err(_) => return None, // resolver error → None WITHOUT caching
        };
        // (6) publish under the write guard.
        self.tcp_cache
            .write()
            .insert(sandbox_id.clone(), endpoint.clone());
        endpoint
    }
}

#[async_trait]
impl SandboxTransport for DaemonClient {
    async fn call(
        &self,
        sandbox_id: &SandboxId,
        op: DaemonOp,
        payload: JsonObject,
        timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        let payload = with_daemon_protocol_version(payload);
        self.call_daemon_api(
            sandbox_id,
            op.as_wire(),
            payload,
            timeout_s,
            DEFAULT_LAYER_STACK_ROOT,
        )
        .await
        .map_err(map_host_error_to_api_error)
    }

    async fn call_dynamic(
        &self,
        sandbox_id: &SandboxId,
        op: &str,
        payload: JsonObject,
        timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        let payload = with_daemon_protocol_version(payload);
        self.call_daemon_api(sandbox_id, op, payload, timeout_s, DEFAULT_LAYER_STACK_ROOT)
            .await
            .map_err(map_host_error_to_api_error)
    }

    async fn ensure_plugin_package(
        &self,
        sandbox_id: &SandboxId,
        request: PluginPackageEnsureRequest,
    ) -> Result<JsonObject, SandboxPortError> {
        crate::plugin_package::ensure_plugin_package(self, sandbox_id, request).await
    }
}
