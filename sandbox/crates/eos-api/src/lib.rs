//! `eos-api` — the single sandbox entry point: receive → gate → route →
//! return. No fleet logic lives here; the engine is `eos-sandbox-host`, and
//! the routing vocabulary is the committed `contract/ops.json` embedded as
//! data.

#![forbid(unsafe_code)]

pub mod admin;
pub mod public;
pub mod router;
pub mod server;
pub mod wire;

use anyhow::Result;
use serde_json::{json, Value};

use eos_sandbox_host::{ForwardError, SandboxHost, SandboxStatus};

use crate::router::Engine;

impl Engine for SandboxHost {
    fn acquire(&self) -> Result<String> {
        SandboxHost::acquire(self)
    }

    fn release(&self, sandbox_id: &str) -> bool {
        SandboxHost::release(self, sandbox_id)
    }

    fn status(&self, sandbox_id: &str) -> Option<Value> {
        SandboxHost::status(self, sandbox_id).map(|status| status_value(&status, true))
    }

    fn list(&self) -> Vec<Value> {
        SandboxHost::list(self)
            .iter()
            .map(|status| status_value(status, false))
            .collect()
    }

    fn forward(
        &self,
        sandbox_id: &str,
        mutates_state: bool,
        op: &str,
        invocation_id: &str,
        args: &Value,
    ) -> Option<Result<Value, ForwardError>> {
        SandboxHost::forward(self, sandbox_id, mutates_state, op, invocation_id, args)
    }
}

fn status_value(status: &SandboxStatus, embed_daemon: bool) -> Value {
    let mut value = json!({
        "success": true,
        "sandbox_id": status.sandbox_id,
        "container": status.container,
        "endpoint": status.endpoint.map(|addr| addr.to_string()),
        "created_by": status.created_by,
    });
    if embed_daemon {
        value["daemon"] = status.daemon.clone();
    }
    value
}
