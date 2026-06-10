//! Pure catalog routing (SPEC §7):
//!
//! ```text
//! visibility != allowed-on-surface        → forbidden
//! served_by == host                       → eos-sandbox-host call
//! served_by == daemon (incl. plugin.*)    → host::forward(sandbox_id, envelope)
//! op not in catalog                       → unknown_op
//! ```
//!
//! The router never branches on specific op names; the only per-op data it
//! reads is `served_by` (as a parsed [`Route`]), `visibility`, and
//! `mutates_state`.

use serde_json::{json, Value};

use eos_sandbox_host::ForwardError;

use crate::public::{Catalog, HostVerb, Route, Visibility};
use crate::wire::{error_envelope, ClientRequest};

/// The engine surface the router drives. `SandboxHost` is the production
/// implementation; contract tests substitute a stub so the full wire+router
/// path runs without docker.
pub trait Engine: Send + Sync {
    /// Provision a sandbox; returns its id.
    fn acquire(&self) -> anyhow::Result<String>;
    /// Destroy a sandbox; `false` when unknown.
    fn release(&self, sandbox_id: &str) -> bool;
    /// Host view of one sandbox, `None` when unknown.
    fn status(&self, sandbox_id: &str) -> Option<Value>;
    /// Enumerate the registry.
    fn list(&self) -> Vec<Value>;
    /// Forward a daemon-bound request; `None` when the sandbox is unknown.
    fn forward(
        &self,
        sandbox_id: &str,
        mutates_state: bool,
        op: &str,
        invocation_id: &str,
        args: &Value,
    ) -> Option<Result<Value, ForwardError>>;
}

/// Which socket a request arrived on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Surface {
    /// The public client socket: `visibility: public` only.
    Client,
    /// The operator socket (`eos-api admin`): public + operator.
    Admin,
}

impl Surface {
    const fn allows(self, visibility: Visibility) -> bool {
        match visibility {
            Visibility::Public => true,
            Visibility::Operator => matches!(self, Self::Admin),
            // Internal ops belong to the host recovery machine and test ops
            // to daemon test builds; neither is served from any socket.
            Visibility::Internal | Visibility::Test => false,
        }
    }
}

/// Route one decoded request to its response value.
pub fn handle(
    catalog: &Catalog,
    engine: &dyn Engine,
    surface: Surface,
    request: &ClientRequest,
) -> Value {
    let Some(entry) = catalog.lookup(&request.op) else {
        // Dynamic plugin ops are daemon-served, public, and (fail-closed)
        // treated as mutating; they exist only inside their sandbox.
        if request.op.starts_with("plugin.") {
            return forward(engine, request, true);
        }
        return error_envelope("unknown_op", &format!("unknown op: {}", request.op));
    };
    if !surface.allows(entry.visibility) {
        return error_envelope(
            "forbidden",
            &format!("op {} is not served on this socket", entry.name),
        );
    }
    match entry.route {
        Route::Daemon => forward(engine, request, entry.mutates_state),
        Route::Host(verb) => host_call(engine, verb, request),
    }
}

fn forward(engine: &dyn Engine, request: &ClientRequest, mutates_state: bool) -> Value {
    let Some(sandbox_id) = request.sandbox_id.as_deref() else {
        return error_envelope("invalid_envelope", "sandbox_id is required for this op");
    };
    match engine.forward(
        sandbox_id,
        mutates_state,
        &request.op,
        &request.invocation_id,
        &request.args,
    ) {
        // Forwarded ops return the daemon's response verbatim.
        Some(Ok(response)) => response,
        Some(Err(ForwardError::UncertainOutcome(message))) => {
            error_envelope("uncertain_outcome", &message)
        }
        Some(Err(ForwardError::SandboxUnavailable(message))) => {
            error_envelope("sandbox_unavailable", &message)
        }
        None => unknown_sandbox(sandbox_id),
    }
}

fn host_call(engine: &dyn Engine, verb: HostVerb, request: &ClientRequest) -> Value {
    match verb {
        HostVerb::Acquire => match engine.acquire() {
            Ok(sandbox_id) => json!({"success": true, "sandbox_id": sandbox_id}),
            Err(err) => error_envelope("sandbox_unavailable", &format!("acquire failed: {err:#}")),
        },
        HostVerb::List => json!({"success": true, "sandboxes": engine.list()}),
        HostVerb::Release | HostVerb::Status => {
            let Some(sandbox_id) = request.sandbox_id.as_deref() else {
                return error_envelope("invalid_envelope", "sandbox_id is required for this op");
            };
            match verb {
                HostVerb::Release => {
                    if engine.release(sandbox_id) {
                        json!({"success": true, "sandbox_id": sandbox_id})
                    } else {
                        unknown_sandbox(sandbox_id)
                    }
                }
                _ => match engine.status(sandbox_id) {
                    Some(status) => status,
                    None => unknown_sandbox(sandbox_id),
                },
            }
        }
    }
}

fn unknown_sandbox(sandbox_id: &str) -> Value {
    error_envelope("unknown_sandbox", &format!("unknown sandbox: {sandbox_id}"))
}
